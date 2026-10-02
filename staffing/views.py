from controlling.utils import render

from django.contrib import messages
from datetime import date

from dateutil.relativedelta import relativedelta
from django.db import transaction
from django.views.decorators.http import require_POST
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from projects.models import Project
from staffing.models import Employment, StaffFundingAllocation, StaffMember
from django.http import HttpRequest
from django.utils import timezone

from projects.models import EmploymentCategories

from .forms import PlanEmploymentForm
from .utils import (
    apply_tariff_salaries,
    get_salaries_by_month,
    get_sap_actuals_by_month,
    get_sap_correction_links,
    get_sap_salary_mismatches,
    level_defaults,
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
    ).all()
    allocation_timeline = []
    for employment in employments:
        employment.salaries_by_month = get_salaries_by_month(employment)
        employment.allocations = employment.stafffundingallocation_set.all().order_by("start_date")
        for allocation in employment.allocations:
            allocation_timeline.append(_timeline_entry(allocation))
        sap_actuals = get_sap_actuals_by_month(employment.allocations)
        employment.has_sap_actuals = bool(sap_actuals)
        employment.sap_mismatches = get_sap_salary_mismatches(employment.salaries_by_month, sap_actuals)
        employment.sap_correction_links = get_sap_correction_links(sap_actuals, employment.sap_mismatches)
        employment.can_estimate = bool(employment.salary_category_id and employment.start_level)
        employment.estimate_from = _estimate_from(employment, sap_actuals)
        mismatch_months = {mismatch["month"] for mismatch in employment.sap_mismatches}
        employment.salary_rows = [
            (month, salary, sap_actuals.get(month, []), month in mismatch_months)
            for month, salary in employment.salaries_by_month.items()
        ]

    return render(request, "staffing/details.html", {
        "staff_member": staff_member,
        "employments": employments,
        "allocation_timeline": allocation_timeline,
    })


def _timeline_entry(allocation):
    """One bar of the funding timeline, grouped by funding source."""
    link = None
    if allocation.budget_item_id:
        project = allocation.budget_item.project
        group, label = f"project-{project.id}", project.acronym
        link = reverse("projects:details", args=[project.acronym])
        title = f"{project.acronym} – {allocation.budget_item.title}"
    elif allocation.annual_pool_budget_id:
        pool = allocation.annual_pool_budget.annual_pool
        group, label = f"pool-{pool.id}", f"Annual Pool {pool.title}"
        title = f"{label} ({allocation.annual_pool_budget.year})"
    elif allocation.is_universal:
        group, label = "universal", "Universalprojekt"
        title = label
    else:
        group, label = f"landesstelle-{allocation.landesstelle_id}", f"Landesstelle {allocation.landesstelle.title}"
        title = label
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
        "start": allocation.start_date.isoformat(),
        "end": end.isoformat(),
    }

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
