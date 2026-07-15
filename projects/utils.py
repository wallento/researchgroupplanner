from decimal import Decimal
from typing import Literal
from .models import Project, StaffBudgetItem
from staffing.models import EmploymentSalaries, StaffFundingAllocation
from dateutil.relativedelta import relativedelta
from calendar import monthrange

from dataclasses import dataclass

@dataclass
class SalaryAllocation:
    allocation: StaffFundingAllocation
    salary_sum: Decimal | Literal[0]
    months: dict


def calculate_salary_for_allocation(allocation: StaffFundingAllocation):
    salaries = EmploymentSalaries.objects.filter(employment=allocation.employment).order_by('start_date')
    total_salary = 0

    months = {}

    for salary in salaries:
        salary_start = salary.start_date
        salary_end = salary.end_date

        allocation_end = allocation.end_date or allocation.employment.end_date
        period_start = max(salary_start, allocation.start_date)
        period_end = min(salary_end, allocation_end)

        if period_end < period_start:
            continue

        current = period_start.replace(day=1)

        while current <= period_end:
            key = current.strftime("%Y-%m")
            current_salary = salary.salary

            # Allocation percentage scales contract salary to source-specific cost.
            if allocation.employment.percentage:
                current_salary *= Decimal(allocation.percentage) / Decimal(allocation.employment.percentage)

            if current.month == period_start.month and period_start.day != 1:
                days_in_month = monthrange(current.year, current.month)[1]
                current_salary *= Decimal((days_in_month - period_start.day + 1) / days_in_month)
                current_salary = current_salary.quantize(Decimal('0.01'))
            months[key] = months.get(key, 0) + current_salary
            total_salary += current_salary
            current += relativedelta(months=1)

    return SalaryAllocation(allocation, total_salary, months)

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
            "start": allocation.start_date,
            "end": allocation.end_date or allocation.employment.end_date
        })
    return allocations