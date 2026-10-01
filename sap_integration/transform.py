"""Actions that transfer SAP positions into the planning."""

from calendar import monthrange
from datetime import date
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from django.db import transaction

from projects.models import OtherBudgetItemTransaction
from sap_integration.crosscheck import add_reference, contract_periods, references
from sap_integration.models import SAPPersonMapping
from staffing.models import Employment, EmploymentSalaries, StaffFundingAllocation, StaffMember
from staffing.utils import get_sap_actuals_by_month


def remember_person(sap_name, staff_member):
    if not sap_name:
        return
    mapping = SAPPersonMapping.objects.filter(sap_name=sap_name).first() or SAPPersonMapping(sap_name=sap_name)
    mapping.staff_member = staff_member
    mapping.save()


@transaction.atomic
def create_staff_planning(
    position,
    *,
    budget_item,
    category,
    percentage,
    staff_member=None,
    first_name="",
    last_name="",
    employment=None,
    create_salaries=True,
    remember_name=True,
):
    """Create employment(s), allocations and optionally salaries for a staff position.

    One allocation is created per SAP contract period. Without an existing
    employment, each period becomes its own employment.
    """
    if staff_member is None:
        staff_member = StaffMember.objects.create(first_name=first_name, last_name=last_name)
    if remember_name:
        remember_person(position.person_name, staff_member)

    periods = contract_periods(position) or [(position.first_date, position.last_date)]
    allocations = []
    for start, end in periods:
        if employment is not None:
            start = max(start, employment.start_date)
            end = min(end, employment.end_date)
            if start > end:
                continue
            target_employment = employment
        else:
            target_employment = Employment.objects.create(
                staff_member=staff_member,
                start_date=start,
                end_date=end,
                percentage=percentage,
                category=category,
            )
        allocations.append(
            StaffFundingAllocation.objects.create(
                employment=target_employment,
                budget_item=budget_item,
                percentage=percentage,
                start_date=start,
                end_date=end,
                sap_reference=position.reference,
            )
        )

    if create_salaries:
        costs = projected_monthly_costs(position)
        for allocation in allocations:
            apply_monthly_salaries(allocation, costs)
    return staff_member, allocations


def projected_monthly_costs(position):
    """Actual payroll per month; remaining contract months continue the last full SAP month."""
    costs = {month: Decimal(amount) for month, amount in position.monthly_actuals.items()}
    last_paid = max(costs, default="")
    remaining_months = [
        month
        for start, end in contract_periods(position)
        for month in _months(start, end)
        if month > last_paid
    ]
    if not remaining_months:
        return costs
    rate = _forecast_rate(position, costs)
    if rate is None and position.commitment > 0:
        rate = (position.commitment / len(remaining_months)).quantize(Decimal("0.01"))
    if rate is not None:
        for month in remaining_months:
            costs[month] = rate
    return costs


def _forecast_rate(position, costs):
    partial_months = {
        start.strftime("%Y-%m") for start, _ in contract_periods(position) if start.day != 1
    }
    for month in sorted(costs, reverse=True):
        if costs[month] > 0 and month not in partial_months:
            return costs[month]
    return None


@transaction.atomic
def link_allocations(position, allocations):
    for allocation in allocations:
        allocation.sap_reference = add_reference(allocation.sap_reference, position.reference)
        allocation.save(update_fields=["sap_reference"])


@transaction.atomic
def apply_monthly_salaries(allocation, monthly_costs):
    """Set the employment's salaries so the allocation's planned cost equals SAP per month.

    Only months covered by the allocation and with a positive amount are
    changed. Returns the months that were skipped because they lie outside.
    """
    employment = allocation.employment
    allocation_end = allocation.end_date or employment.end_date
    if not allocation.percentage or not employment.percentage:
        return sorted(monthly_costs)
    # SAP books one amount per month for the whole position, which can be
    # split over several allocations (e.g. a contract extension mid-month).
    linked = _linked_allocations(allocation)
    # A month paid from several funds (e.g. a switch mid-month) is only
    # complete when all of them are combined; SAP does not split it by days.
    sap_linked = [a for a in employment.stafffundingallocation_set.all() if references(a.sap_reference)]
    multi_fund_months = {
        month: sum((entry["amount"] for entry in entries), Decimal("0.00"))
        for month, entries in get_sap_actuals_by_month(sap_linked).items()
        if len(entries) > 1
    }

    salaries = _salaries_by_month(employment)
    skipped = []
    for month, cost in monthly_costs.items():
        month_start = date.fromisoformat(f"{month}-01")
        if cost <= 0:
            continue
        if not (allocation.start_date.replace(day=1) <= month_start <= allocation_end.replace(day=1)):
            skipped.append(month)
            continue
        share_allocations = linked
        if month in multi_fund_months:
            cost, share_allocations = multi_fund_months[month], sap_linked
        # calculate_salary_for_allocation prorates by covered days and
        # percentage; SAP's amount already reflects both, so undo them.
        share = _month_share(share_allocations, month_start)
        if not share:
            skipped.append(month)
            continue
        salaries[month] = (cost / share).quantize(Decimal("0.01"))

    _replace_salaries(employment, salaries)
    return sorted(skipped)


@transaction.atomic
def create_transaction(position, budget_item):
    return OtherBudgetItemTransaction.objects.create(
        budget_item=budget_item,
        date=position.first_date or date.today(),
        amount=position.total,
        description=_transaction_description(position),
        sap_id=position.reference,
    )


@transaction.atomic
def update_transaction_amount(position, planner_transaction):
    planner_transaction.amount = position.total
    planner_transaction.save(update_fields=["amount"])


@transaction.atomic
def link_transaction(position, planner_transaction):
    planner_transaction.sap_id = add_reference(planner_transaction.sap_id, position.reference)
    planner_transaction.save(update_fields=["sap_id"])


def _transaction_description(position):
    parts = [position.title]
    if position.description and position.description not in position.title:
        parts.append(position.description)
    return " – ".join(part for part in parts if part)


def _linked_allocations(allocation):
    """Allocations of the same employment and funding owner sharing an SAP reference."""
    refs = references(allocation.sap_reference)
    linked = [
        other for other in allocation.employment.stafffundingallocation_set.select_related("budget_item")
        if other.id != allocation.id
        and references(other.sap_reference) & refs
        and _owner(other) == _owner(allocation)
    ]
    return [allocation, *linked]


def _owner(allocation):
    if allocation.budget_item_id:
        return "project", allocation.budget_item.project_id
    if allocation.annual_pool_budget_id:
        return "annual_pool", allocation.annual_pool_budget_id
    if allocation.landesstelle_id:
        return "landesstelle", allocation.landesstelle_id
    return "universal", None


def _month_share(allocations, month_start):
    """Fraction of a full monthly salary that the allocations plan as cost."""
    days = monthrange(month_start.year, month_start.month)[1]
    month_end = month_start.replace(day=days)
    share = Decimal("0")
    for allocation in allocations:
        employment = allocation.employment
        start = max(allocation.start_date, employment.start_date, month_start)
        end = min(allocation.end_date or employment.end_date, employment.end_date, month_end)
        if end < start:
            continue
        covered = Decimal((end - start).days + 1) / Decimal(days)
        share += covered * Decimal(allocation.percentage) / Decimal(employment.percentage)
    return share


def _salaries_by_month(employment):
    months = {}
    for salary in employment.employmentsalaries_set.all():
        for month in _months(salary.start_date, salary.end_date):
            months[month] = months.get(month, Decimal("0.00")) + salary.salary
    return months


def _replace_salaries(employment, salaries):
    """Rewrite salary rows as consolidated runs of equal consecutive months."""
    runs = []
    for month in sorted(salaries):
        amount = salaries[month]
        if runs and runs[-1]["amount"] == amount and runs[-1]["last"] == _previous_month(month):
            runs[-1]["last"] = month
        else:
            runs.append({"first": month, "last": month, "amount": amount})

    employment_first = employment.start_date.strftime("%Y-%m")
    employment_last = employment.end_date.strftime("%Y-%m")
    employment.employmentsalaries_set.all().delete()
    EmploymentSalaries.objects.bulk_create(
        EmploymentSalaries(
            employment=employment,
            salary=run["amount"],
            start_date=(
                employment.start_date if run["first"] == employment_first
                else date.fromisoformat(f"{run['first']}-01")
            ),
            end_date=employment.end_date if run["last"] == employment_last else _month_end(run["last"]),
        )
        for run in runs
    )


def _months(start, end):
    current = start.replace(day=1)
    while current <= end:
        yield current.strftime("%Y-%m")
        current += relativedelta(months=1)


def _previous_month(month):
    return (date.fromisoformat(f"{month}-01") - relativedelta(months=1)).strftime("%Y-%m")


def _month_end(month):
    first = date.fromisoformat(f"{month}-01")
    return first.replace(day=monthrange(first.year, first.month)[1])
