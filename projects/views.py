from decimal import Decimal
from controlling.utils import render
from django.db.models import Q
from django.utils import timezone

from .models import Landesstelle, OtherBudgetItemTransaction, StaffBudgetItem, Project
from staffing.models import Rebooking, StaffFundingAllocation
from sap_integration.crosscheck import references
from staffing.utils import rebooking_cost_deltas, rebooking_effects, rebooking_person_month_deltas
from django.http import HttpRequest
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from .utils import (
    budget_usage_percent,
    calculate_salary_for_allocation,
    amount_in_period,
    get_allocation_person_months,
    person_months_in_period,
    project_budget_overview,
    get_allocations_salary_sum_of_year,
    get_table_allocations,
    get_timeline_allocations,
)


def index(request: HttpRequest):
    today = timezone.now().date()
    running_projects = Project.objects.filter(
        Q(extension_planning_date__isnull=False, extension_planning_date__gte=today)
        | Q(extension_planning_date__isnull=True, end_date__gte=today)
    ).order_by("start_date", "acronym")

    completed_projects = Project.objects.filter(
        Q(extension_planning_date__isnull=False, extension_planning_date__lt=today)
        | Q(extension_planning_date__isnull=True, end_date__lt=today)
    ).order_by("start_date", "acronym")

    projects, project_totals = project_budget_overview(running_projects)
    return render(request, "projects/index.html", {
        "running_projects": running_projects,
        "completed_projects": completed_projects,
        "projects": projects,
        "project_totals": project_totals,
    })


def _rebooked_cell(amount, pm, rebooked, rebooked_pm):
    """(amount, PM, with Umbuchungen, PM with Umbuchungen); the latter only if they change the cell."""
    if rebooked is None or (rebooked == amount and rebooked_pm == pm):
        return amount, pm, None, None
    return amount, pm, rebooked, rebooked_pm


def details(request: HttpRequest, acronym: str):
    project = get_object_or_404(Project, acronym=acronym)

    staff_budget_items = StaffBudgetItem.objects.filter(project=project).all()
    rebooking_deltas = rebooking_cost_deltas()
    rebooking_pm_deltas = rebooking_person_month_deltas()
    effects = rebooking_effects()

    for budget_item in staff_budget_items:
        budget_item.staff_allocations = []
        budget_item.projected_sum = Decimal("0.00")
        budget_item.pm_years = {}
        for allocation in StaffFundingAllocation.objects.filter(budget_item=budget_item).select_related("employment__staff_member"):
            salary_allocation = calculate_salary_for_allocation(allocation)
            salary_allocation.person_months = get_allocation_person_months(allocation)
            salary_allocation.pm_total = sum(salary_allocation.person_months.values(), Decimal("0"))
            salary_allocation.outgoing_rebookings = list(allocation.rebookings.select_related("budget_item__project"))
            budget_item.staff_allocations.append(salary_allocation)
            budget_item.projected_sum += salary_allocation.salary_sum
            for year, pm in salary_allocation.person_months.items():
                budget_item.pm_years[year] = budget_item.pm_years.get(year, Decimal("0")) + pm
        budget_item.years = {}
        for year in project.get_years():
            budget_item.years[year] = Decimal("0.00")
            for allocation in budget_item.staff_allocations:
                budget_item.years[year] += get_allocations_salary_sum_of_year(year, allocation)
        # PM outside the project years still count towards the total.
        budget_item.pm_total = sum(budget_item.pm_years.values(), Decimal("0"))
        # Open Umbuchungen per year: (amount, PM, amount with Umbuchungen, PM with Umbuchungen).
        budget_item.effects = [(sign, sa) for item_id, sign, sa in effects if item_id == budget_item.id]
        budget_item.year_cells = []
        for year, amount in budget_item.years.items():
            pm = budget_item.pm_years.get(year, Decimal("0"))
            rebooked = rebooked_pm = None
            if budget_item.effects:
                rebooked = amount + sum(
                    (sign * get_allocations_salary_sum_of_year(year, sa) for sign, sa in budget_item.effects), Decimal("0.00"),
                )
                rebooked_pm = pm + sum(
                    (sign * get_allocation_person_months(sa.allocation).get(year, Decimal("0")) for sign, sa in budget_item.effects),
                    Decimal("0"),
                )
            budget_item.year_cells.append(_rebooked_cell(amount, pm, rebooked, rebooked_pm))
        budget_item.remain = budget_item.amount - budget_item.projected_sum
        budget_item.incoming_rebookings = []
        for rebooking in Rebooking.objects.filter(budget_item=budget_item).select_related(
            "allocation__employment__staff_member", "allocation__budget_item__project",
        ):
            _, target = rebooking.as_allocations()
            source = rebooking.allocation.budget_item
            budget_item.incoming_rebookings.append({
                "rebooking": rebooking,
                "source_label": source.project.acronym if source else "Umbuchung",
                "cost": calculate_salary_for_allocation(target).salary_sum,
                "pm": sum(get_allocation_person_months(target).values(), Decimal("0")),
            })
        delta = rebooking_deltas.get(budget_item.id)
        if delta:
            budget_item.rebooked_sum = budget_item.projected_sum + delta
            budget_item.rebooked_remain = budget_item.remain - delta
            budget_item.rebooked_pm = budget_item.pm_total + rebooking_pm_deltas.get(budget_item.id, Decimal("0"))

    table_assignments = get_table_allocations(project, staff_budget_items)
    timeline_assignments = get_timeline_allocations(project)

    other_budget_items = project.otherbudgetitem_set.all()
    for item in other_budget_items:
        item.years = {}
        item.projected_sum = Decimal("0.00")
        for year in project.get_years():
            item.years[year] = Decimal("0.00")
            for transaction in item.get_transactions(year):
                item.years[year] += transaction.amount
            item.projected_sum += item.years[year]
        item.remain = item.amount - item.projected_sum

    budget_items = [*staff_budget_items, *other_budget_items]
    budget_totals = {
        "amount": sum((item.amount for item in budget_items), Decimal("0.00")),
        "years": [
            sum((item.years[year] for item in budget_items), Decimal("0.00"))
            for year in project.get_years()
        ],
        "projected": sum((item.projected_sum for item in budget_items), Decimal("0.00")),
        "remain": sum((item.remain for item in budget_items), Decimal("0.00")),
    }
    budget_totals["pm_years"] = [
        sum((item.pm_years.get(year, Decimal("0")) for item in staff_budget_items), Decimal("0"))
        for year in project.get_years()
    ]
    affected = any(item.effects for item in staff_budget_items)
    budget_totals["year_cells"] = []
    for index, (amount, pm) in enumerate(zip(budget_totals["years"], budget_totals["pm_years"])):
        rebooked = rebooked_pm = None
        if affected:
            changed = [item.year_cells[index] for item in staff_budget_items if item.year_cells[index][2] is not None]
            rebooked = amount + sum((cell[2] - cell[0] for cell in changed), Decimal("0.00"))
            rebooked_pm = pm + sum((cell[3] - cell[1] for cell in changed), Decimal("0"))
        budget_totals["year_cells"].append(_rebooked_cell(amount, pm, rebooked, rebooked_pm))
    budget_totals["pm_total"] = sum((item.pm_total for item in staff_budget_items), Decimal("0"))
    rebooking_delta = sum((rebooking_deltas.get(item.id, Decimal("0.00")) for item in staff_budget_items), Decimal("0.00"))
    has_rebookings = any(item.id in rebooking_deltas for item in staff_budget_items)
    if has_rebookings:
        budget_totals["rebooked_projected"] = budget_totals["projected"] + rebooking_delta
        budget_totals["rebooked_remain"] = budget_totals["remain"] - rebooking_delta
        budget_totals["rebooked_pm"] = budget_totals["pm_total"] + sum(
            (rebooking_pm_deltas.get(item.id, Decimal("0")) for item in staff_budget_items), Decimal("0"),
        )

    reporting_periods = list(project.reporting_periods.all())
    reporting = None
    if reporting_periods:
        rows = []
        def period_cell(item, period):
            start, end = period.start_date, period.end_date
            amount = sum((amount_in_period(sa.months, start, end) for sa in item.staff_allocations), Decimal("0.00"))
            pm = sum((person_months_in_period(sa.allocation, start, end) for sa in item.staff_allocations), Decimal("0"))
            if not item.effects:
                return amount, pm, None, None
            rebooked = amount + sum(
                (sign * amount_in_period(sa.months, start, end) for sign, sa in item.effects), Decimal("0.00"),
            )
            rebooked_pm = pm + sum(
                (sign * person_months_in_period(sa.allocation, start, end) for sign, sa in item.effects), Decimal("0"),
            )
            return _rebooked_cell(amount, pm, rebooked, rebooked_pm)

        for item in staff_budget_items:
            rows.append({"title": item.title, "staff": True, "cells": [period_cell(item, p) for p in reporting_periods]})
        for item in other_budget_items:
            transactions = item.get_transactions()
            rows.append({"title": item.title, "staff": False, "cells": [
                (sum((t.amount for t in transactions if p.start_date <= t.date <= p.end_date), Decimal("0.00")), None, None, None)
                for p in reporting_periods
            ]})
        totals = []
        for i in range(len(reporting_periods)):
            amount = sum((row["cells"][i][0] for row in rows), Decimal("0.00"))
            pm = sum((row["cells"][i][1] for row in rows if row["staff"]), Decimal("0"))
            rebooked = rebooked_pm = None
            if affected:
                rebooked = amount + sum((row["cells"][i][2] - row["cells"][i][0]
                                         for row in rows if row["cells"][i][2] is not None), Decimal("0.00"))
                rebooked_pm = pm + sum((row["cells"][i][3] - row["cells"][i][1]
                                        for row in rows if row["cells"][i][3] is not None), Decimal("0"))
            totals.append(_rebooked_cell(amount, pm, rebooked, rebooked_pm))
        reporting = {"periods": reporting_periods, "rows": rows, "totals": totals}

    total_staff_allocated = sum((item.projected_sum for item in staff_budget_items), Decimal("0.00"))
    total_other_allocated = sum((item.projected_sum for item in other_budget_items), Decimal("0.00"))
    total_overhead_allocated = sum((item.amount for item in project.overheadbudgetitem_set.all()), Decimal("0.00"))
    total_allocated = (total_staff_allocated + total_other_allocated + total_overhead_allocated).quantize(Decimal("0.01"))

    remain_sum = None
    if project.budget_total is not None:
        remain_sum = (project.budget_total - total_allocated).quantize(Decimal("0.01"))
    rebooked_allocated = rebooked_remain = None
    if has_rebookings:
        rebooked_allocated = total_allocated + rebooking_delta
        rebooked_remain = remain_sum - rebooking_delta if remain_sum is not None else None

    parameters = {
        "project": project,
        "staff_budget_items": staff_budget_items,
        "other_budget_items": other_budget_items,
        "budget_totals": budget_totals,
        "table_assignments": table_assignments,
        "timeline_assignments": timeline_assignments,
        "allocated_sum": total_allocated,
        "remain_sum": remain_sum,
        "rebooked_allocated": rebooked_allocated,
        "reporting": reporting,
        "rebooked_remain": rebooked_remain,
    }

    return render(request, "projects/details.html", parameters)

def _sap_positions(project):
    """{SAP reference: (reconciliation page, position)} for the latest import of each of the project's funds."""
    from sap_integration.models import SAPPosition

    positions = {}
    for fund in project.sap_funds.all():
        sap_import = fund.sap_imports.order_by("-imported_at").first()
        if sap_import is None:
            continue
        for position in SAPPosition.objects.filter(sap_import=sap_import):
            positions.setdefault(position.reference, (
                reverse("sap_integration:position_detail", args=[fund.id, position.id]), position,
            ))
    return positions


SAP_STATUS_LABELS = {
    "ist": ("Ist", "text-bg-success", "In SAP bezahlt"),
    "partial": ("Ist + offen", "text-bg-info", "Teilweise bezahlt, Bestellung noch offen"),
    "obligo": ("Obligo", "text-bg-warning", "In SAP reserviert/bestellt, noch nicht bezahlt"),
    "plan": ("Plan", "text-bg-light border", "Ohne SAP-Beleg"),
}


def _sap_status(linked_positions):
    """(status key, paid actual) of a planning entry from its linked SAP positions."""
    if not linked_positions:
        return "plan", None
    actual = sum((p.actual for p in linked_positions), Decimal("0.00"))
    planned = sum((p.planned_amount for p in linked_positions), Decimal("0.00"))
    if not actual:
        return "obligo", actual
    if planned - actual > Decimal("0.01"):
        return "partial", actual
    return "ist", actual


def other_budget_items(request: HttpRequest, acronym: str):
    project = get_object_or_404(Project, acronym=acronym)
    years = {int(year) for year in project.get_years()}

    positions = _sap_positions(project)

    budget_items = list(project.otherbudgetitem_set.order_by("title"))
    for item in budget_items:
        item.transactions = sorted(item.get_transactions(), key=lambda t: (t.date, t.id))
        for transaction in item.transactions:
            # Only bookings within the project years count, as on the details page.
            transaction.counts = transaction.date.year in years
            refs = sorted(references(transaction.sap_id))
            transaction.sap_links = [(reference, positions.get(reference, (None,))[0]) for reference in refs]
            status, transaction.sap_actual = _sap_status([positions[r][1] for r in refs if r in positions])
            transaction.sap_status = SAP_STATUS_LABELS[status]
        item.used = sum((t.amount for t in item.transactions if t.counts), Decimal("0.00"))
        item.actual = sum((t.sap_actual or Decimal("0.00") for t in item.transactions if t.counts), Decimal("0.00"))
        item.remain = item.amount - item.used
        item.usage_percent = budget_usage_percent(item.used, item.amount)

    total_amount = sum((item.amount for item in budget_items), Decimal("0.00"))
    total_used = sum((item.used for item in budget_items), Decimal("0.00"))
    total_actual = sum((item.actual for item in budget_items), Decimal("0.00"))
    return render(request, "projects/other_budget_items.html", {
        "total_actual": total_actual,
        "project": project,
        "budget_items": budget_items,
        "total_amount": total_amount,
        "total_used": total_used,
        "total_remain": total_amount - total_used,
        "total_percent": budget_usage_percent(total_used, total_amount),
    })


@require_POST
def other_budget_transaction_description(request: HttpRequest, acronym: str, id: int):
    transaction = get_object_or_404(
        OtherBudgetItemTransaction.objects.select_related("budget_item"), id=id, budget_item__project__acronym=acronym,
    )
    transaction.description = request.POST.get("description", "").strip()
    transaction.save(update_fields=["description"])
    messages.success(request, f"Beschreibung für {transaction.budget_item.title} ({transaction.date:%d.%m.%Y}) gespeichert.")
    return redirect(f"{reverse('projects:other_budget_items', args=[acronym])}#transaction-{transaction.id}")


def staff_budget_item(request: HttpRequest, acronym: str, id: int):
    project = get_object_or_404(Project, acronym=acronym)
    budget_item = get_object_or_404(StaffBudgetItem, id=id, project=project)

    # Render the budget item details
    return render(request, "projects/staff_budget_item.html", {"project": project, "budget_item": budget_item})


def landesstelle_detail(request: HttpRequest, id: int):
    landesstelle = get_object_or_404(Landesstelle, id=id)
    allocations = StaffFundingAllocation.objects.filter(landesstelle=landesstelle).select_related(
        "employment__staff_member"
    ).order_by("start_date")
    return render(request, "projects/landesstelle_detail.html", {
        "landesstelle": landesstelle,
        "allocations": allocations,
    })