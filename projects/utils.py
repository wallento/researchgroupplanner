from decimal import Decimal
from typing import Literal
from .models import Project, StaffBudgetItem
from staffing.models import StaffAssignment, EmploymentSalaries
from django.db.models import Q
from collections import defaultdict
from dateutil.relativedelta import relativedelta
from calendar import monthrange

from dataclasses import dataclass

@dataclass
class SalaryAssignment:
    assignment: StaffAssignment
    salary_sum: Decimal | Literal[0]
    months: dict


def calculate_salary_for_assignment(assignment: StaffAssignment):
    salaries = EmploymentSalaries.objects.filter(employment=assignment.employment).order_by('start_date')
    total_salary = 0

    months = {}

    for salary in salaries:
        salary_start = salary.start_date
        salary_end = salary.end_date

        period_start = max(salary_start, assignment.start_date)
        period_end = min(salary_end, assignment.end_date)

        current = period_start.replace(day=1)

        while current <= period_end:
            key = current.strftime("%Y-%m")
            current_salary = salary.salary
            if current.month == period_start.month and period_start.day != 1:
                days_in_month = monthrange(current.year, current.month)[1]
                current_salary *= Decimal((days_in_month - period_start.day + 1) / days_in_month)
                current_salary = current_salary.quantize(Decimal('0.01'))
            months[key] = months.get(key, 0) + current_salary
            total_salary += current_salary
            current += relativedelta(months=1)

    return SalaryAssignment(assignment, total_salary, months)

def get_assignments_salary_sum_of_year(year: int, assignment: SalaryAssignment) -> Decimal:
    return Decimal(sum(assignment.months.get(f"{year}-{month:02d}", 0) for month in range(1, 13))).quantize(Decimal('0.01'))

def get_table_assignments(project: Project, budget_items: list[StaffBudgetItem]) -> dict[str, list[dict]]:
    table = {}
    staff = []
    current = project.start_date.replace(day=1)
    while current <= project.end_date:
        key = current.strftime("%Y-%m")
        table[key] = {}
        for budget_item in budget_items:
            for budget_assignment in budget_item.staff_assignments:
                if budget_assignment.assignment.employment.staff_member not in staff:
                    staff.append(budget_assignment.assignment.employment.staff_member)
                if budget_assignment.assignment.start_date.replace(day=1) <= current <= budget_assignment.assignment.end_date.replace(day=1):
                    table[key][budget_assignment.assignment.employment.staff_member] = budget_assignment.months.get(current.strftime("%Y-%m"), 0)
        current += relativedelta(months=1)

    return (staff, table)

def get_timeline_assignments(project: Project) -> list[dict]:
    assignments = []
    for assignment in StaffAssignment.objects.filter(budget_item__project=project):
        assignments.append({
            "employee": assignment.employment.staff_member,
            "category": assignment.employment.get_category(),
            "start": assignment.start_date,
            "end": assignment.end_date
        })
    return assignments