from decimal import Decimal
from typing import Literal
from .models import Project, StaffBudgetItem
from staffing.models import StaffFundingAllocation
from staffing.utils import get_salary_amounts_by_month
from dateutil.relativedelta import relativedelta

from dataclasses import dataclass

@dataclass
class SalaryAllocation:
    allocation: StaffFundingAllocation
    salary_sum: Decimal | Literal[0]
    months: dict


def calculate_salary_for_allocation(allocation: StaffFundingAllocation):
    salaries = allocation.employment.employmentsalaries_set.order_by('start_date')
    total_salary = Decimal("0.00")

    months = {}

    for salary in salaries:
        allocation_end = allocation.end_date or allocation.employment.end_date
        period_start = max(allocation.start_date, allocation.employment.start_date)
        period_end = min(allocation_end, allocation.employment.end_date)
        salary_months = get_salary_amounts_by_month(
            salary,
            period_start,
            period_end,
        )
        for key, current_salary in salary_months.items():
            # Allocation percentage scales contract salary to source-specific cost.
            if allocation.employment.percentage:
                current_salary = (
                    current_salary
                    * Decimal(allocation.percentage)
                    / Decimal(allocation.employment.percentage)
                ).quantize(Decimal("0.01"))
            months[key] = months.get(key, 0) + current_salary
            total_salary += current_salary

    return SalaryAllocation(allocation, total_salary, months)

def get_allocation_person_months(allocation: StaffFundingAllocation) -> dict[str, Decimal]:
    """Person months (PM) per year: share of each month covered × allocation percentage / 100.

    A full month at 100 % is 1 PM; the allocation is limited to its employment.
    """
    from calendar import monthrange

    employment = allocation.employment
    start = max(allocation.start_date, employment.start_date)
    end = min(allocation.end_date or employment.end_date, employment.end_date)
    factor = Decimal(allocation.percentage) / Decimal("100")
    years = {}
    current = start.replace(day=1)
    while current <= end:
        days = monthrange(current.year, current.month)[1]
        covered = (min(end, current.replace(day=days)) - max(start, current)).days + 1
        year = str(current.year)
        years[year] = years.get(year, Decimal("0")) + Decimal(covered) / Decimal(days) * factor
        current += relativedelta(months=1)
    return years


def get_allocations_salary_sum_of_year(year: int, allocation: SalaryAllocation) -> Decimal:
    return Decimal(sum(allocation.months.get(f"{year}-{month:02d}", 0) for month in range(1, 13))).quantize(Decimal('0.01'))

def get_table_allocations(project: Project, budget_items: list[StaffBudgetItem]) -> dict[str, list[dict]]:
    table = {}
    staff = []
    current = project.start_date.replace(day=1)
    while current <= project.end_date:
        key = current.strftime("%Y-%m")
        table[key] = {}
        for budget_item in budget_items:
            for budget_allocation in budget_item.staff_allocations:
                if budget_allocation.allocation.employment.staff_member not in staff:
                    staff.append(budget_allocation.allocation.employment.staff_member)
                allocation_end = budget_allocation.allocation.end_date or budget_allocation.allocation.employment.end_date
                if budget_allocation.allocation.start_date.replace(day=1) <= current <= allocation_end.replace(day=1):
                    table[key][budget_allocation.allocation.employment.staff_member] = budget_allocation.months.get(current.strftime("%Y-%m"), 0)
        current += relativedelta(months=1)

    return (staff, table)

def get_timeline_allocations(project: Project) -> list[dict]:
    allocations = []
    for allocation in StaffFundingAllocation.objects.filter(budget_item__project=project).select_related("employment__staff_member"):
        allocations.append({
            "employee": allocation.employment.staff_member,
            "category": allocation.employment.get_category(),
            "status": allocation.employment.status,
            "status_label": allocation.employment.get_status_display(),
            "rebooking": None,
            "start": allocation.start_date,
            "end": allocation.end_date or allocation.employment.end_date
        })
    # Open Umbuchungen onto ("in") and away from ("out") this project are shown as extra bars.
    from django.db.models import Q

    from staffing.models import Rebooking
    for rebooking in Rebooking.objects.filter(
        Q(budget_item__project=project) | Q(allocation__budget_item__project=project)
    ).select_related("allocation__employment__staff_member", "allocation__budget_item__project", "budget_item__project"):
        employment = rebooking.allocation.employment
        source = rebooking.allocation.budget_item
        source_label = source.project.acronym if source else "Umbuchung"
        incoming = rebooking.budget_item.project_id == project.id
        percentage = f"{rebooking.percentage.normalize():f}\u00a0%"
        allocations.append({
            "employee": employment.staff_member,
            "category": employment.get_category(),
            "status": employment.status,
            "status_label": employment.get_status_display(),
            "rebooking": "in" if incoming else "out",
            "rebooking_label": (
                f"← {source_label} · {percentage}" if incoming else f"→ {rebooking.budget_item.project.acronym} · {percentage}"
            ),
            "rebooking_title": (
                f"Umbuchung (offen): {source_label} → {rebooking.budget_item.project.acronym}, {percentage}"
            ),
            "start": rebooking.start_date,
            "end": rebooking.end,
        })
    return allocations


def get_staff_budget_item_used(budget_item: StaffBudgetItem) -> Decimal:
    """Planned salary costs of all allocations on a staff budget item."""
    return sum(
        (
            calculate_salary_for_allocation(allocation).salary_sum
            for allocation in StaffFundingAllocation.objects.filter(budget_item=budget_item)
        ),
        Decimal("0.00"),
    )


def get_other_budget_item_used(budget_item) -> Decimal:
    """Planned transactions of an other budget item within the project years."""
    years = [int(year) for year in budget_item.project.get_years()]
    return sum(
        (transaction.amount for transaction in budget_item.get_transactions() if transaction.date.year in years),
        Decimal("0.00"),
    )


def budget_usage_percent(used: Decimal, budget: Decimal | None) -> Decimal | None:
    if not budget:
        return None
    return (used * 100 / budget).quantize(Decimal("0.1"))


def amount_in_period(months: dict, start, end) -> Decimal:
    """Share of monthly amounts ({"YYYY-MM": amount}) within a period, prorated by days for partial months."""
    from calendar import monthrange
    from datetime import date

    total = Decimal("0")
    for key, amount in months.items():
        month_start = date.fromisoformat(f"{key}-01")
        days = monthrange(month_start.year, month_start.month)[1]
        overlap = (min(end, month_start.replace(day=days)) - max(start, month_start)).days + 1
        if overlap > 0:
            total += Decimal(amount) * Decimal(overlap) / Decimal(days)
    return total.quantize(Decimal("0.01"))


def person_months_in_period(allocation: StaffFundingAllocation, start, end) -> Decimal:
    """Person months of an allocation within a period."""
    allocation_end = allocation.end_date or allocation.employment.end_date
    clipped = StaffFundingAllocation(
        employment=allocation.employment, percentage=allocation.percentage,
        start_date=max(allocation.start_date, start), end_date=min(allocation_end, end),
    )
    if clipped.start_date > clipped.end_date:
        return Decimal("0")
    return sum(get_allocation_person_months(clipped).values(), Decimal("0"))
