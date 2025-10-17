from .models import StaffMember, Employment, EmploymentSalaries, StaffAssignment
from django.contrib import messages
from decimal import Decimal
from dateutil.relativedelta import relativedelta

def get_salaries_by_month(employment: Employment):
    current = employment.start_date.replace(day=1)
    months = {}
    while current <= employment.end_date:
        months[current.strftime("%Y-%m")] = Decimal('0.00')
        current += relativedelta(months=1)

    for salary in employment.employmentsalaries_set.all().order_by('start_date'):
        current = salary.start_date.replace(day=1)
        while current <= (salary.end_date if salary.end_date else employment.end_date):
            key = current.strftime("%Y-%m")
            if key in months:
                months[key] += salary.salary
            current += relativedelta(months=1)

    return months
