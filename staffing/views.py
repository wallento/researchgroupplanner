from controlling.utils import render

from django.contrib import messages
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta
from django.db import transaction
from django.views.decorators.http import require_POST
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from projects.models import Project
from django.core.exceptions import ValidationError
from django.utils.dateparse import parse_date
from staffing.models import MAX_LEVEL, Employment, Rebooking, SalaryCategory, StaffFundingAllocation, StaffMember
from staffing.models import complete_rebooking as apply_rebooking
from django.http import HttpRequest
from django.utils import timezone

from projects.models import EmploymentCategories

from .forms import AllocationForm, PlanEmploymentForm, RebookingForm
from .utils import (
    apply_tariff_salaries,
    employment_merge_candidates,
    merge_employments,
    get_salaries_by_month,
    get_sap_actuals_by_month,
    get_sap_correction_links,
    get_sap_salary_mismatches,
    level_defaults,
    staff_reservations,
)

def index(request):
    today = timezone.now().date()
    staff_members = StaffMember.objects.prefetch_related("employment_set").all().order_by("last_name", "first_name")

    category_order = list(EmploymentCategories.keys())
    category_labels = dict(EmploymentCategories)

    current_by_category = {}
    former_by_category = {}

    def category_index(key):
        return category_order.index(key) if key in category_order else len(category_order)

    for staff_member in staff_members:
        employments = sorted(list(staff_member.employment_set.all()), key=lambda e: (e.start_date, e.end_date))
        active_employments = [e for e in employments if e.start_date <= today <= e.end_date]

        if active_employments:
            category_key = active_employments[-1].category
        elif employments:
            category_key = employments[-1].category
        else:
            category_key = "uncategorized"

        category_label = category_labels.get(category_key, "Ohne Kategorie")
        is_former = staff_member.status == "alumni" or (employments and all(e.end_date < today for e in employments))

        target = former_by_category if is_former else current_by_category
        target.setdefault(category_key, {
            "label": category_label,
            "staff": [],
        })["staff"].append(staff_member)

    def sort_grouped(data):
        result = []
        for key, value in data.items():
            value["staff"].sort(key=lambda s: (s.last_name, s.first_name))
            result.append((key, value))
        result.sort(key=lambda item: (category_index(item[0]), item[1]["label"]))
        return result

    return render(request, "staffing/index.html", {
        "current_staff_by_category": sort_grouped(current_by_category),
        "former_staff_by_category": sort_grouped(former_by_category),
    })

def details(request: HttpRequest, staff_id: int):
    staff_member = get_object_or_404(StaffMember, id=staff_id)

    employments = staff_member.employment_set.prefetch_related(
        "stafffundingallocation_set__budget_item__project",
        "stafffundingallocation_set__landesstelle",
        "stafffundingallocation_set__annual_pool_budget__annual_pool",
    ).order_by("start_date")
    merge_hints = {
        second.id: {"previous": first, "ok": ok, "reason": reason}
        for first, second, ok, reason in employment_merge_candidates(employments)
    }
    person_defaults = level_defaults(staff_member)
    allocation_timeline = []
    for employment in employments:
        employment.merge_hint = merge_hints.get(employment.id)
        employment.salaries_by_month = get_salaries_by_month(employment)
        employment.allocations = employment.stafffundingallocation_set.all().order_by("start_date")
        for allocation in employment.allocations:
            allocation_timeline.append(_timeline_entry(allocation))
            allocation.open_rebookings = list(allocation.rebookings.select_related("budget_item__project"))
            for rebooking in allocation.open_rebookings:
                allocation_timeline.extend(_rebooking_timeline_entries(rebooking))
        sap_actuals = get_sap_actuals_by_month(employment.allocations)
        employment.has_sap_actuals = bool(sap_actuals)
        employment.sap_mismatches = get_sap_salary_mismatches(employment.salaries_by_month, sap_actuals)
        employment.sap_correction_links = get_sap_correction_links(sap_actuals, employment.sap_mismatches)
        employment.can_estimate = bool(employment.salary_category_id and employment.start_level)
        if not employment.can_estimate:
            # Prefill pay grade and level for the estimate from the person's other employments.
            defaults = person_defaults or {}
            employment.estimate_defaults = {
                "salary_category": employment.salary_category_id or defaults.get("salary_category"),
                "start_level": defaults.get("start_level") or 1,
                "level_start_date": min(
                    date.fromisoformat(defaults["level_start_date"]) if defaults.get("level_start_date") else employment.start_date,
                    employment.start_date,
                ).isoformat(),
            }
        employment.estimate_from = _estimate_from(employment, sap_actuals)
        mismatch_months = {mismatch["month"] for mismatch in employment.sap_mismatches}
        employment.salary_rows = [
            (month, salary, sap_actuals.get(month, []), month in mismatch_months)
            for month, salary in employment.salaries_by_month.items()
        ]

    reservations = staff_reservations(staff_member)
    for reservation in reservations:
        allocation_timeline.extend(_reservation_timeline_entries(reservation))
    return render(request, "staffing/details.html", {
        "staff_member": staff_member,
        "employments": employments,
        "reservations": reservations,
        "allocation_timeline": allocation_timeline,
        "salary_categories": SalaryCategory.objects.all(),
        "levels": range(1, MAX_LEVEL + 1),
    })


def _allocation_source(allocation_or_item):
    """(group, label, link, title) of the funding source of an allocation."""
    allocation = allocation_or_item
    if allocation.budget_item_id:
        project = allocation.budget_item.project
        return (
            f"project-{project.id}", project.acronym, reverse("projects:details", args=[project.acronym]),
            f"{project.acronym} – {allocation.budget_item.title}",
        )
    if allocation.annual_pool_budget_id:
        pool = allocation.annual_pool_budget.annual_pool
        label = f"Annual Pool {pool.title}"
        return f"pool-{pool.id}", label, None, f"{label} ({allocation.annual_pool_budget.year})"
    if allocation.is_universal:
        return "universal", "Universalprojekt", None, "Universalprojekt"
    label = f"Landesstelle {allocation.landesstelle.title}"
    return f"landesstelle-{allocation.landesstelle_id}", label, None, label


def _timeline_entry(allocation):
    """One bar of the funding timeline, grouped by funding source."""
    group, label, link, title = _allocation_source(allocation)
    end = allocation.end_date or allocation.employment.end_date
    return {
        "id": allocation.id,
        "group": group,
        "label": label,
        "link": link,
        "title": f"{title}: {allocation.percentage.normalize():f} % – {allocation.employment.get_status_display()} ({allocation.start_date:%d.%m.%Y} – {end:%d.%m.%Y})",
        "percentage": float(allocation.percentage),
        "status": allocation.employment.status,
        "status_label": allocation.employment.get_status_display(),
        "rebooking": None,
        "start": allocation.start_date.isoformat(),
        "end": end.isoformat(),
    }


def _rebooking_timeline_entries(rebooking):
    """Two overlay bars of an open Umbuchung: outgoing in the source row, incoming in the target row."""
    employment = rebooking.allocation.employment
    source_group, source_label, source_link, _ = _allocation_source(rebooking.allocation)
    target = rebooking.budget_item.project
    target_link = reverse("projects:details", args=[target.acronym])
    percentage = f"{rebooking.percentage.normalize():f}"
    title = (
        f"Umbuchung (offen): {source_label} → {target.acronym} – {rebooking.budget_item.title}, "
        f"{percentage} % ({rebooking.start_date:%d.%m.%Y} – {rebooking.end:%d.%m.%Y})"
    )
    common = {
        "title": title,
        "percentage": float(rebooking.percentage),
        "status": employment.status,
        "status_label": employment.get_status_display(),
        "start": rebooking.start_date.isoformat(),
        "end": rebooking.end.isoformat(),
    }
    return [
        {**common, "id": f"rebooking-out-{rebooking.id}", "group": source_group, "label": source_label,
         "link": source_link, "rebooking": "out", "content": f"→ {target.acronym} · {percentage}\u00a0%"},
        {**common, "id": f"rebooking-{rebooking.id}", "group": f"project-{target.id}", "label": target.acronym,
         "link": target_link, "rebooking": "in", "content": f"← {source_label} · {percentage}\u00a0%"},
    ]


def _reservation_timeline_entries(reservation):
    """Thin bars of the SAP contract periods of a reservation in its project row."""
    project, position = reservation["project"], reservation["position"]
    share = f" · {position.percentage.normalize():f}\u00a0%" if position.percentage else ""
    return [
        {
            "id": f"sap-{position.id}-{index}",
            "group": f"project-{project.id}",
            "label": project.acronym,
            "link": reservation["url"],
            "title": (
                f"SAP-Mittelreservierung {position.reference} ({position.contract_type or 'Vertrag'}{share}): "
                f"{start:%d.%m.%Y} – {end:%d.%m.%Y}"
                + ("" if reservation["linked"] else " – nur über den Namen zugeordnet")
            ),
            "content": f"SAP {position.contract_type or 'Vertrag'}{share}",
            "percentage": float(position.percentage or 0),
            "status": "contract",
            "status_label": "",
            "rebooking": None,
            "sap_contract": True,
            "start": start.isoformat(),
            "end": end.isoformat(),
        }
        for index, (start, end) in enumerate(reservation["periods"])
    ]


@require_POST
def link_reservation(request: HttpRequest, staff_id: int, position_id: int):
    """Add the SAP reference of a name-matched reservation to the person's matching allocations."""
    from sap_integration.transform import link_allocations

    staff_member = get_object_or_404(StaffMember, id=staff_id)
    reservation = next(
        (r for r in staff_reservations(staff_member) if r["position"].id == position_id and not r["linked"]), None,
    )
    if reservation is None or not reservation["link_candidates"]:
        messages.error(request, "Keine passende Zuordnung zum Verknüpfen gefunden.")
    else:
        link_allocations(reservation["position"], reservation["link_candidates"])
        messages.success(request, f"SAP-Reservierung {reservation['position'].reference} verknüpft.")
    return redirect("staffing:details", staff_id=staff_member.id)


def plan_employment(request: HttpRequest):
    """Create an employment with its project allocation and projected salaries."""
    project = None
    if request.GET.get("project"):
        project = get_object_or_404(Project, acronym=request.GET["project"])
    initial = {}
    staff_defaults = {
        member.id: defaults
        for member in StaffMember.objects.prefetch_related("employment_set")
        if (defaults := level_defaults(member))
    }
    if request.GET.get("staff"):
        member = get_object_or_404(StaffMember, id=request.GET["staff"])
        initial["staff_member"] = member
        initial.update(staff_defaults.get(member.id, {}))

    form = PlanEmploymentForm(request.POST or None, project=project, initial=initial)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        with transaction.atomic():
            staff_member = data["staff_member"] or StaffMember.objects.create(
                first_name=data["first_name"], last_name=data["last_name"], status="in_hire",
            )
            employment = Employment.objects.create(
                staff_member=staff_member,
                start_date=data["start_date"],
                end_date=data["end_date"],
                percentage=data["percentage"],
                category=data["category"],
                status=data["status"],
                salary_category=data["salary_category"],
                start_level=data["start_level"],
                level_start_date=data["level_start_date"] if data["start_level"] else None,
                statutory_health_insurance=data["statutory_health_insurance"],
            )
            StaffFundingAllocation.objects.create(
                employment=employment,
                budget_item=data["budget_item"],
                percentage=data["percentage"],
                start_date=data["start_date"],
                end_date=data["end_date"],
            )
            missing = apply_tariff_salaries(employment) if data["salary_category"] else None
        messages.success(request, f"Anstellung für {staff_member} ({employment.get_status_display()}) angelegt.")
        if missing:
            messages.warning(
                request,
                "Für folgende Monate gibt es keinen Tabellenwert, bitte Gehalt ergänzen: " + ", ".join(missing),
            )
        elif missing is None:
            messages.warning(request, "Ohne Entgeltgruppe wurden keine Gehälter angelegt.")
        return redirect("staffing:details", staff_id=staff_member.id)

    return render(request, "staffing/plan_employment.html", {
        "form": form,
        "project": project,
        "staff_defaults": staff_defaults,
    })


def _estimate_from(employment, sap_actuals):
    """First month to estimate: after the last month booked in SAP, else the employment start."""
    if not sap_actuals:
        return employment.start_date.strftime("%Y-%m")
    last = date.fromisoformat(f"{max(sap_actuals)}-01")
    return (last + relativedelta(months=1)).strftime("%Y-%m")


@require_POST
def estimate_salaries(request: HttpRequest, employment_id: int):
    employment = get_object_or_404(Employment.objects.select_related("staff_member", "salary_category"), id=employment_id)
    from_month = request.POST.get("from_month") or None
    if request.POST.get("insurance_field"):
        employment.statutory_health_insurance = bool(request.POST.get("statutory_health_insurance"))
        employment.save(update_fields=["statutory_health_insurance"])
    if request.POST.get("salary_category"):
        # Pay grade and level entered together with the estimate are stored on the employment.
        employment.salary_category = get_object_or_404(SalaryCategory, id=request.POST["salary_category"])
        employment.start_level = int(request.POST.get("start_level") or 1)
        employment.level_start_date = parse_date(request.POST.get("level_start_date") or "") or employment.start_date
        try:
            employment.full_clean()
        except ValidationError as error:
            messages.error(request, "Gehälter nicht geschätzt: " + " ".join(error.messages))
            return redirect("staffing:details", staff_id=employment.staff_member_id)
        employment.save(update_fields=["salary_category", "start_level", "level_start_date"])
    if not (employment.salary_category_id and employment.start_level):
        messages.error(request, "Für die Schätzung bitte Entgeltgruppe und Stufe bei der Anstellung hinterlegen.")
    else:
        with transaction.atomic():
            missing = apply_tariff_salaries(employment, from_month=from_month)
        start = from_month or employment.start_date.strftime("%Y-%m")
        messages.success(request, f"Gehälter ab {start} aus den Entgelttabellen geschätzt.")
        if missing:
            messages.warning(request, "Ohne Tabellenwert, unverändert gelassen: " + ", ".join(missing))
    return redirect("staffing:details", staff_id=employment.staff_member_id)


def _allocation(allocation_id):
    return get_object_or_404(
        StaffFundingAllocation.objects.select_related("employment__staff_member", "budget_item__project"),
        id=allocation_id,
    )


def edit_allocation(request: HttpRequest, allocation_id: int):
    allocation = _allocation(allocation_id)
    form = AllocationForm(request.POST or None, instance=allocation)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Zuordnung gespeichert.")
        return redirect("staffing:details", staff_id=allocation.employment.staff_member_id)
    return render(request, "staffing/allocation_form.html", {
        "allocation": allocation, "form": form, "title": "Zuordnung bearbeiten", "submit": "Speichern",
    })


def rebook_allocation(request: HttpRequest, allocation_id: int):
    """Create an open Umbuchung; the allocation stays unchanged until it is completed."""
    allocation = _allocation(allocation_id)
    form = RebookingForm(request.POST or None, allocation=allocation)
    if request.method == "POST" and form.is_valid():
        rebooking = form.save()
        messages.success(request, f"Umbuchung ab {rebooking.start_date:%d.%m.%Y} auf {rebooking.budget_item} angelegt.")
        return redirect("staffing:details", staff_id=allocation.employment.staff_member_id)
    return render(request, "staffing/allocation_form.html", {
        "allocation": allocation, "form": form, "title": "Zuordnung umbuchen", "submit": "Umbuchung anlegen",
        "is_rebooking": True,
    })


def _rebooking(rebooking_id):
    return get_object_or_404(
        Rebooking.objects.select_related("allocation__employment__staff_member", "allocation__budget_item__project",
                                         "budget_item__project"),
        id=rebooking_id,
    )


def _redirect_back(request, rebooking):
    next_url = request.POST.get("next") or ""
    if next_url.startswith("/"):
        return redirect(next_url)
    return redirect("staffing:details", staff_id=rebooking.allocation.employment.staff_member_id)


def edit_rebooking(request: HttpRequest, rebooking_id: int):
    rebooking = _rebooking(rebooking_id)
    form = RebookingForm(request.POST or None, allocation=rebooking.allocation, instance=rebooking)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Umbuchung gespeichert.")
        return redirect("staffing:details", staff_id=rebooking.allocation.employment.staff_member_id)
    return render(request, "staffing/allocation_form.html", {
        "allocation": rebooking.allocation, "form": form, "title": "Umbuchung bearbeiten", "submit": "Speichern",
        "is_rebooking": True,
    })


@require_POST
def delete_rebooking(request: HttpRequest, rebooking_id: int):
    rebooking = _rebooking(rebooking_id)
    rebooking.delete()
    messages.success(request, "Umbuchung gelöscht; die Zuordnung ist unverändert.")
    return _redirect_back(request, rebooking)


@require_POST
def complete_rebooking(request: HttpRequest, rebooking_id: int):
    """Apply an Umbuchung (see staffing.models.complete_rebooking)."""
    rebooking = _rebooking(rebooking_id)
    allocation = rebooking.allocation
    apply_rebooking(rebooking)
    messages.success(request, f"Umbuchung für {allocation.employment.staff_member} abgeschlossen (Vertrag).")
    return _redirect_back(request, rebooking)


def rebookings(request: HttpRequest):
    """Open Umbuchungen."""
    return render(request, "staffing/rebookings.html", {
        "rebookings": Rebooking.objects.select_related(
            "allocation__employment__staff_member", "allocation__budget_item__project", "allocation__landesstelle",
            "allocation__annual_pool_budget__annual_pool", "budget_item__project",
        ).order_by("start_date", "allocation__employment__staff_member__last_name"),
    })


@require_POST
def merge_employment(request: HttpRequest, first_id: int, second_id: int):
    first = get_object_or_404(Employment.objects.select_related("staff_member"), id=first_id)
    second = get_object_or_404(Employment, id=second_id)
    try:
        merge_employments(first, second)
    except ValueError as error:
        messages.error(request, f"Zusammenführen nicht möglich: {error}")
    else:
        messages.success(request, f"Anstellungen von {first.staff_member} zusammengeführt ({first.start_date:%d.%m.%Y} – {first.end_date:%d.%m.%Y}).")
    next_url = request.POST.get("next") or ""
    if next_url.startswith("/"):
        return redirect(next_url)
    return redirect("staffing:details", staff_id=first.staff_member_id)
