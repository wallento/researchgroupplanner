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

    Returns {"YYYY-MM": [{"project", "label", "amount", "positions"}]} summed
    per fund, using only the latest import of each fund. "positions" holds
    (fund_id, position_id, reference) of the contributing SAP positions.
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
                "positions": set(),
            })
            entry["amount"] += Decimal(amount)
            entry["positions"].add((fund.id, position.id, position.reference))
    return {month: list(by_fund.values()) for month, by_fund in actuals.items()}


SAP_SALARY_TOLERANCE = Decimal("1.00")


def get_sap_salary_mismatches(salaries_by_month, sap_actuals):
    """Months where the planned salary differs from SAP's actual payroll (all funds combined)."""
    mismatches = []
    for month, entries in sorted(sap_actuals.items()):
        sap_total = sum((entry["amount"] for entry in entries), Decimal("0.00"))
        planned = salaries_by_month.get(month, Decimal("0.00"))
        if abs(sap_total - planned) > SAP_SALARY_TOLERANCE:
            mismatches.append({
                "month": month,
                "planned": planned,
                "sap": sap_total,
                "difference": sap_total - planned,
            })
    return mismatches


def get_sap_correction_links(sap_actuals, mismatches):
    """Reconciliation pages of the SAP positions booked in the mismatching months."""
    from django.urls import reverse

    links = {}
    for mismatch in mismatches:
        for entry in sap_actuals.get(mismatch["month"], []):
            if entry["project"] is None:
                continue
            for fund_id, position_id, reference in entry["positions"]:
                links.setdefault((fund_id, position_id), {
                    "label": f"{entry['label']} – SAP-Position {reference}",
                    "url": reverse("sap_integration:position_detail", args=[fund_id, position_id]),
                })
    return sorted(links.values(), key=lambda link: link["label"])


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
