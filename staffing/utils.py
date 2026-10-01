from .models import StaffMember, Employment, EmploymentSalaries
from django.contrib import messages
from decimal import Decimal
from dateutil.relativedelta import relativedelta
from calendar import monthrange


CENT = Decimal("0.01")


def get_salary_amounts_by_month(salary, period_start=None, period_end=None):
    """Return a salary record's contribution per month for an overlap period."""
    start = max(salary.start_date, period_start or salary.start_date)
    end = min(salary.end_date, period_end or salary.end_date)
    if end < start:
        return {}

    amounts = {}
    current = start.replace(day=1)
    exact_period_days = (salary.end_date - salary.start_date).days + 1
    while current <= end:
        month_end = current.replace(day=monthrange(current.year, current.month)[1])
        overlap_start = max(start, current)
        overlap_end = min(end, month_end)
        overlap_days = (overlap_end - overlap_start).days + 1
        denominator = (
            exact_period_days
            if salary.is_exact_amount
            else monthrange(current.year, current.month)[1]
        )
        amount = (
            Decimal(salary.salary)
            * Decimal(overlap_days)
            / Decimal(denominator)
        ).quantize(CENT)
        amounts[current.strftime("%Y-%m")] = amount
        current += relativedelta(months=1)
    return amounts

def get_salaries_by_month(employment: Employment):
    current = employment.start_date.replace(day=1)
    months = {}
    while current <= employment.end_date:
        months[current.strftime("%Y-%m")] = Decimal('0.00')
        current += relativedelta(months=1)

    for salary in employment.employmentsalaries_set.all().order_by('start_date'):
        for key, amount in get_salary_amounts_by_month(
            salary,
            employment.start_date,
            employment.end_date,
        ).items():
            if key in months:
                months[key] += amount

    return months


def get_sap_actuals_by_month(allocations):
    """SAP payroll actuals per month for the positions referenced by the allocations.

    Returns {"YYYY-MM": [{"project", "label", "amount"}]} summed per fund,
    using only the latest import of each fund.
    """
    from sap_integration.crosscheck import references
    from sap_integration.models import SAPPosition

    # The same SAP reference can span several employments, so only months
    # covered by an allocation carrying the reference count here.
    months_by_ref = {}
    for allocation in allocations:
        end = allocation.end_date or allocation.employment.end_date
        months = set(_month_keys(allocation.start_date, end))
        for ref in references(allocation.sap_reference):
            months_by_ref.setdefault(ref, set()).update(months)
    if not months_by_ref:
        return {}

    positions = SAPPosition.objects.filter(reference__in=months_by_ref).select_related(
        "sap_import__fund__project", "sap_import__fund__annual_pool"
    ).order_by("-sap_import__imported_at")
    latest_imports = {}
    actuals = {}
    for position in positions:
        fund = position.sap_import.fund
        latest_imports.setdefault(fund.id, position.sap_import_id)
        if latest_imports[fund.id] != position.sap_import_id:
            continue
        for month, amount in position.monthly_actuals.items():
            if month not in months_by_ref[position.reference]:
                continue
            entry = actuals.setdefault(month, {}).setdefault(fund.id, {
                "project": fund.project,
                "label": _fund_label(fund),
                "amount": Decimal("0.00"),
            })
            entry["amount"] += Decimal(amount)
    return {month: list(by_fund.values()) for month, by_fund in actuals.items()}


def _fund_label(fund):
    if fund.project_id:
        return fund.project.acronym
    if fund.annual_pool_id:
        return f"Annual Pool {fund.annual_pool.title}"
    if fund.is_universal:
        return "Universalprojekt"
    return fund.label or fund.fund_number


def _month_keys(start, end):
    current = start.replace(day=1)
    while current <= end:
        yield current.strftime("%Y-%m")
        current += relativedelta(months=1)
