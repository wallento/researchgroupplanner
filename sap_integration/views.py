import tempfile
from collections import defaultdict
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_POST

from controlling.utils import render
from projects.models import OtherBudgetItem, SAPFund, StaffBudgetItem
from sap_integration.cache import SAPCacheError, available_years, fund_values, load_year
from sap_integration.crosscheck import (
    STATUS_LABELS,
    Status,
    build_reconciliation,
    contract_periods,
    expected_percentage,
    planner_category,
)
from sap_integration.forms import (
    CostTypeMappingForm,
    CreateTransactionForm,
    ExportUploadForm,
    IgnoreForm,
    LinkAllocationsForm,
    LinkTransactionForm,
    StaffTransformForm,
)
from sap_integration.gm_export import import_gm_export
from sap_integration.models import (
    SAPCostTypeMapping,
    SAPIgnoredPosition,
    SAPImport,
    SAPPositionKind,
)
from sap_integration.transform import (
    apply_monthly_salaries,
    create_staff_planning,
    create_transaction,
    link_allocations,
    link_transaction,
    projected_monthly_costs,
    update_transaction_amount,
)
from sap_integration.cleaning import clean_fund_values


def _ensure_enabled():
    if not settings.SAP_ENABLED:
        raise Http404("Die SAP-Integration ist deaktiviert.")


def _ensure_gm_import_enabled():
    if not settings.SAP_GM_IMPORT_ENABLED:
        raise Http404("Der Import von SAP-Projektexporten ist deaktiviert.")


def _owner(fund):
    if fund.project_id:
        return fund.project.acronym, "Projekt"
    if fund.annual_pool_id:
        return fund.annual_pool.title, "Annual Pool"
    return "Universalprojekt", "Universalprojekt"


def _generated_at(payload):
    value = payload.get("generated_at") if payload else None
    return parse_datetime(value) if value else None


def _has_year_data(values):
    return values is not None and (
        values.get("has_budget") or bool(values.get("transactions"))
    )


def _display_values(fund, cached_fund):
    values = fund_values(cached_fund)
    if values is not None and fund.treat_negative_actuals_as_funding:
        return clean_fund_values(
            values,
            treat_negative_actuals_as_funding=True,
        )
    return values


def _project_time_percentage(project, today):
    start_date = project.start_date
    end_date = project.get_effective_end_date()
    total_days = (end_date - start_date).days
    if total_days <= 0:
        return Decimal("100.00") if today >= end_date else Decimal("0.00")

    elapsed_days = (today - start_date).days
    bounded_days = min(max(elapsed_days, 0), total_days)
    return (
        Decimal(bounded_days) / Decimal(total_days) * Decimal("100")
    ).quantize(Decimal("0.01"))


def _project_lifetime_summaries(funds, payloads, today=None):
    today = today or timezone.localdate()
    used_by_project = defaultdict(lambda: Decimal("0"))
    projects = {}

    for fund in funds:
        if not fund.project_id:
            continue
        projects[fund.project_id] = fund.project
        for payload in payloads.values():
            values = _display_values(
                fund,
                payload.get("funds", {}).get(fund.fund_number),
            )
            if values is not None:
                used_by_project[fund.project_id] += values["combined_total"]

    summaries = {}
    for project_id, project in projects.items():
        budget = project.budget_total
        used = used_by_project[project_id]
        utilization = (
            (used / budget * Decimal("100")).quantize(Decimal("0.01"))
            if budget
            else None
        )
        summaries[project_id] = {
            "budget": budget,
            "used": used,
            "utilization": utilization,
            "is_over_budget": utilization is not None and utilization > 100,
            "time_percentage": _project_time_percentage(project, today),
        }
        summaries[project_id]["utilization_bar_width"] = format(
            min(max(utilization or Decimal("0"), Decimal("0")), Decimal("100")),
            "f",
        )
        summaries[project_id]["time_bar_width"] = format(
            summaries[project_id]["time_percentage"],
            "f",
        )
    return summaries


@staff_member_required
def overview(request, year=None):
    if not (settings.SAP_ENABLED or settings.SAP_GM_IMPORT_ENABLED):
        raise Http404("Die SAP-Integration ist deaktiviert.")
    if year is not None:
        _ensure_enabled()
    years = available_years(settings.SAP_DATA_DIR) if settings.SAP_ENABLED else []
    selected_year = year if year is not None else (years[0] if years else None)
    payload = None
    cache_error = None
    if selected_year is not None:
        try:
            payload = load_year(settings.SAP_DATA_DIR, selected_year)
        except SAPCacheError as error:
            cache_error = str(error)

    payloads = {}
    for available_year in years:
        if available_year == selected_year and payload is not None:
            payloads[available_year] = payload
            continue
        try:
            payloads[available_year] = load_year(
                settings.SAP_DATA_DIR,
                available_year,
            )
        except SAPCacheError:
            # A damaged historical cache must not hide the selected year. It is
            # omitted from the lifetime calculation until it is rebuilt.
            continue

    cached_funds = payload.get("funds", {}) if payload else {}
    rows = []
    funds = list(
        SAPFund.objects.filter(is_active=True)
        .select_related("project", "annual_pool")
        .order_by("fund_number")
    )
    lifetime_summaries = _project_lifetime_summaries(funds, payloads)
    for fund in funds:
        owner, owner_type = _owner(fund)
        values = _display_values(fund, cached_funds.get(fund.fund_number))
        if not _has_year_data(values):
            continue
        is_adjusted = fund.treat_negative_actuals_as_funding
        rows.append(
            {
                "fund": fund,
                "owner": owner,
                "owner_type": owner_type,
                "values": values,
                "is_adjusted": is_adjusted,
                "lifetime": lifetime_summaries.get(fund.project_id),
            }
        )

    project_exports = []
    if settings.SAP_GM_IMPORT_ENABLED:
        for sap_import in SAPImport.objects.select_related("fund__project").order_by("fund__fund_number"):
            result = build_reconciliation(sap_import.fund)
            project_exports.append({
                "import": sap_import,
                "open_count": len(result.open_checks) if result else None,
            })

    return render(
        request,
        "sap_integration/overview.html",
        {
            "sap_webgui_enabled": settings.SAP_ENABLED,
            "project_exports": project_exports,
            "upload_form": ExportUploadForm(),
            "years": years,
            "selected_year": selected_year,
            "rows": rows,
            "cache_error": cache_error,
            "generated_at": _generated_at(payload),
        },
    )


@staff_member_required
def fund_detail(request, year, fund_id):
    _ensure_enabled()
    fund = get_object_or_404(
        SAPFund.objects.select_related("project", "annual_pool"),
        pk=fund_id,
        is_active=True,
    )
    try:
        payload = load_year(settings.SAP_DATA_DIR, year)
    except SAPCacheError as error:
        raise Http404(str(error)) from error

    values = fund_values(payload.get("funds", {}).get(fund.fund_number))
    if not _has_year_data(values):
        raise Http404(f"Für Fonds {fund.fund_number} liegen {year} keine SAP-Daten vor.")
    owner, owner_type = _owner(fund)
    is_clean = request.GET.get("clean") == "1"
    if is_clean:
        values = clean_fund_values(
            values,
            treat_negative_actuals_as_funding=(
                fund.treat_negative_actuals_as_funding
            ),
        )
    return render(
        request,
        "sap_integration/fund_detail.html",
        {
            "fund": fund,
            "owner": owner,
            "owner_type": owner_type,
            "year": year,
            "years": available_years(settings.SAP_DATA_DIR),
            "values": values,
            "is_clean": is_clean,
            "generated_at": _generated_at(payload),
        },
    )


KIND_SECTIONS = [
    (SAPPositionKind.STAFF, "Personal"),
    (SAPPositionKind.TRAVEL, "Reisen"),
    (SAPPositionKind.OTHER, "Sachmittel und Dienstleistungen"),
    (SAPPositionKind.INCOME, "Einnahmen (Mittelabrufe)"),
]
FILTERS = {
    "open": ("Offen", {Status.MISSING, Status.MISMATCH}),
    "ok": ("Übereinstimmend", {Status.OK}),
    "neutral": ("Ohne Betrag", {Status.NEUTRAL}),
    "ignored": ("Ignoriert", {Status.IGNORED}),
    "income": ("Einnahmen", {Status.INFO}),
    "all": ("Alle", set(STATUS_LABELS)),
}


@staff_member_required
@require_POST
def upload_export(request):
    _ensure_gm_import_enabled()
    form = ExportUploadForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, "Bitte eine Excel-Datei auswählen.")
        return redirect("sap_integration:overview")

    upload = form.cleaned_data["export_file"]
    with tempfile.NamedTemporaryFile(suffix=".xlsx") as temp_file:
        for chunk in upload.chunks():
            temp_file.write(chunk)
        temp_file.flush()
        try:
            sap_import = import_gm_export(temp_file.name, file_name=upload.name, user=request.user)
        except ValueError as error:
            messages.error(request, f"Import fehlgeschlagen: {error}")
            return redirect("sap_integration:overview")
        except Exception:
            messages.error(request, f"Die Datei {upload.name} konnte nicht als SAP-Export gelesen werden.")
            return redirect("sap_integration:overview")

    messages.success(
        request,
        f"{sap_import.positions.count()} SAP-Positionen aus {upload.name} für {sap_import.fund} importiert.",
    )
    return redirect("sap_integration:reconciliation", sap_import.fund_id)


def _load_reconciliation(fund_id):
    fund = get_object_or_404(SAPFund.objects.select_related("project"), pk=fund_id)
    result = build_reconciliation(fund)
    if result is None:
        raise Http404("Für diesen Fonds liegt kein Projektexport vor oder er ist keinem Projekt zugeordnet.")
    return fund, result


@staff_member_required
def reconciliation(request, fund_id):
    _ensure_gm_import_enabled()
    fund, result = _load_reconciliation(fund_id)
    selected = request.GET.get("filter", "open")
    if selected not in FILTERS:
        selected = "open"
    statuses = FILTERS[selected][1]
    filters = [
        {
            "key": key,
            "label": label,
            "count": sum(1 for check in result.checks if check.status in filter_statuses),
        }
        for key, (label, filter_statuses) in FILTERS.items()
    ]
    sections = []
    for kind, label in KIND_SECTIONS:
        checks = [c for c in result.checks if c.position.kind == kind and c.status in statuses]
        if checks:
            sections.append({"kind": kind, "label": label, "checks": checks})

    for row in result.cost_types:
        mapping = row["mapping"]
        initial = ""
        if mapping and mapping.staff_budget_item_id:
            initial = f"staff:{mapping.staff_budget_item_id}"
        elif mapping and mapping.other_budget_item_id:
            initial = f"other:{mapping.other_budget_item_id}"
        row["form"] = CostTypeMappingForm(
            project=fund.project,
            initial={"cost_type": row["cost_type"], "target": initial},
        )

    return render(request, "sap_integration/reconciliation.html", {
        "fund": fund,
        "project": fund.project,
        "result": result,
        "filters": filters,
        "selected_filter": selected,
        "sections": sections,
    })


@staff_member_required
@require_POST
def cost_type_mapping(request, fund_id):
    _ensure_gm_import_enabled()
    fund = get_object_or_404(SAPFund.objects.select_related("project"), pk=fund_id, project__isnull=False)
    form = CostTypeMappingForm(request.POST, project=fund.project)
    if form.is_valid():
        cost_type = form.cleaned_data["cost_type"]
        target = form.cleaned_data["target"]
        if not target:
            SAPCostTypeMapping.objects.filter(fund=fund, cost_type=cost_type).delete()
        else:
            kind, item_id = target.split(":")
            mapping, _ = SAPCostTypeMapping.objects.get_or_create(fund=fund, cost_type=cost_type)
            mapping.staff_budget_item = (
                get_object_or_404(StaffBudgetItem, pk=item_id, project=fund.project) if kind == "staff" else None
            )
            mapping.other_budget_item = (
                get_object_or_404(OtherBudgetItem, pk=item_id, project=fund.project) if kind == "other" else None
            )
            mapping.save()
        messages.success(request, f"Zuordnung für E/A-Art {cost_type} gespeichert.")
    return redirect("sap_integration:reconciliation", fund.id)


@staff_member_required
def position_detail(request, fund_id, position_id):
    _ensure_gm_import_enabled()
    fund, result = _load_reconciliation(fund_id)
    check = next((c for c in result.checks if c.position.id == position_id), None)
    if check is None:
        raise Http404("SAP-Position nicht gefunden.")
    position = check.position
    project = fund.project
    back_url = reverse("sap_integration:reconciliation", args=[fund.id])

    if request.method == "POST":
        response = _handle_position_action(request, fund, check)
        if response is not None:
            return response

    last_name, _, first_name = position.person_name.partition(", ")
    existing_employment = _overlapping_employment(check)
    staff_form = StaffTransformForm(
        request.POST if request.POST.get("action") == "transform_staff" else None,
        project=project,
        staff_member=check.staff_member,
        initial={
            "staff_member": check.staff_member,
            "first_name": first_name,
            "last_name": last_name,
            "budget_item": check.budget_item,
            "category": planner_category(position),
            "percentage": expected_percentage(position) or 100,
            "employment": existing_employment,
            "create_salaries": existing_employment is None,
        },
    )
    salary_rows = []
    if position.kind == SAPPositionKind.STAFF:
        months = sorted(set(position.monthly_actuals) | set(check.planned_months))
        for month in months:
            sap_amount = position.monthly_actuals.get(month)
            planned = check.planned_months.get(month)
            salary_rows.append({
                "month": month,
                "sap": sap_amount,
                "planned": planned,
                "mismatch": any(m == month for m, _, _ in check.salary_mismatch_months),
            })

    return render(request, "sap_integration/position_detail.html", {
        "fund": fund,
        "project": project,
        "check": check,
        "position": position,
        "back_url": back_url,
        "staff_form": staff_form,
        "link_allocations_form": LinkAllocationsForm(project=project),
        "create_transaction_form": CreateTransactionForm(
            project=project, initial={"budget_item": check.budget_item}
        ),
        "link_transaction_form": LinkTransactionForm(project=project),
        "ignore_form": IgnoreForm(),
        "salary_rows": salary_rows,
    })


def _handle_position_action(request, fund, check):
    position = check.position
    project = fund.project
    action = request.POST.get("action")
    detail_url = reverse("sap_integration:position_detail", args=[fund.id, position.id])
    back_url = reverse("sap_integration:reconciliation", args=[fund.id])
    next_url = request.POST.get("next") or back_url
    if not next_url.startswith("/"):
        next_url = back_url

    if action == "ignore":
        form = IgnoreForm(request.POST)
        note = form.cleaned_data["note"] if form.is_valid() else ""
        SAPIgnoredPosition.objects.update_or_create(
            fund=fund,
            reference=position.reference,
            defaults={"note": note, "created_by": request.user},
        )
        messages.success(request, f"Position {position.reference} wird ignoriert.")
        return redirect(next_url)

    if action == "unignore":
        SAPIgnoredPosition.objects.filter(fund=fund, reference=position.reference).delete()
        messages.success(request, f"Position {position.reference} wird wieder abgeglichen.")
        return redirect(next_url)

    if position.kind == SAPPositionKind.STAFF:
        if action == "transform_staff":
            form = StaffTransformForm(request.POST, project=project, staff_member=check.staff_member)
            if not form.is_valid():
                return None
            data = form.cleaned_data
            staff_member, allocations = create_staff_planning(
                position,
                budget_item=data["budget_item"],
                category=data["category"],
                percentage=data["percentage"],
                staff_member=data["staff_member"],
                first_name=data["first_name"],
                last_name=data["last_name"],
                employment=data["employment"],
                create_salaries=data["create_salaries"],
                remember_name=data["remember_name"],
            )
            messages.success(
                request,
                f"{len(allocations)} Zuordnung(en) für {staff_member} aus SAP-Position {position.reference} angelegt.",
            )
            return redirect(back_url)

        if action == "link_allocations":
            form = LinkAllocationsForm(request.POST, project=project)
            if form.is_valid():
                link_allocations(position, form.cleaned_data["allocations"])
                messages.success(request, f"SAP-Position {position.reference} verknüpft.")
            return redirect(detail_url)

        if action == "confirm_link" and check.allocations:
            link_allocations(position, check.allocations)
            messages.success(request, f"Zuordnung zu {check.staff_member} bestätigt.")
            return redirect(next_url)

        if action == "apply_salaries" and check.allocations:
            costs = (
                projected_monthly_costs(position)
                if request.POST.get("include_forecast")
                else {month: Decimal(amount) for month, amount in position.monthly_actuals.items()}
            )
            link_allocations(position, check.allocations)
            skipped = set()
            for allocation in check.allocations:
                skipped |= set(apply_monthly_salaries(allocation, costs))
            covered = {
                month for month in costs
                if any(_allocation_covers(allocation, month) for allocation in check.allocations)
            }
            skipped -= covered
            messages.success(request, f"Gehälter für {check.staff_member} aus SAP übernommen.")
            if skipped:
                messages.warning(
                    request,
                    "Nicht übernommen, da außerhalb der geplanten Zuordnung: " + ", ".join(sorted(skipped)),
                )
            return redirect(detail_url)
    else:
        if action == "create_transaction":
            form = CreateTransactionForm(request.POST, project=project)
            if form.is_valid():
                create_transaction(position, form.cleaned_data["budget_item"])
                messages.success(request, f"Planungseintrag für {position.reference} angelegt.")
            else:
                messages.error(request, "Bitte ein Sachmittelbudget auswählen.")
            return redirect(next_url)

        if action == "update_amount" and len(check.transactions) == 1:
            update_transaction_amount(position, check.transactions[0])
            messages.success(request, f"Betrag für {position.reference} aus SAP übernommen.")
            return redirect(next_url)

        if action == "link_transaction":
            form = LinkTransactionForm(request.POST, project=project)
            if form.is_valid():
                link_transaction(position, form.cleaned_data["transaction"])
                messages.success(request, f"SAP-Position {position.reference} verknüpft.")
            return redirect(detail_url)

    messages.error(request, "Diese Aktion ist für die Position nicht möglich.")
    return redirect(detail_url)


def _overlapping_employment(check):
    if check.staff_member is None:
        return None
    periods = contract_periods(check.position)
    for employment in check.staff_member.employment_set.order_by("start_date"):
        if any(employment.start_date <= end and start <= employment.end_date for start, end in periods):
            return employment
    return None


def _allocation_covers(allocation, month):
    end = allocation.end_date or allocation.employment.end_date
    return allocation.start_date.strftime("%Y-%m") <= month <= end.strftime("%Y-%m")
