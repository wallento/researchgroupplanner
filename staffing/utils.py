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
