from django.http import HttpRequest
from .models import StaffMember, EmploymentSalaries, Employment
from django.contrib import messages
from dateutil.relativedelta import relativedelta
from decimal import Decimal

def check_staff_salaries_in_employments(request: HttpRequest) -> None:
    for salary in EmploymentSalaries.objects.all():
        if salary.start_date < salary.employment.start_date:
            messages.error(request, f"Das Gehalt {salary} hat ein Startdatum vor dem Start der Anstellung {salary.employment.start_date}.")
        if salary.end_date > salary.employment.end_date:
            messages.error(request, f"Das Gehalt {salary} hat ein Enddatum nach dem Ende der Anstellung {salary.employment.end_date}.")

def check_staff_employments_full_salaries(request: HttpRequest, employment: Employment) -> None:
    current = employment.start_date.replace(day=1)
    months = {}
    while current <= employment.end_date:
        months[current.strftime("%Y-%m")] = Decimal('0.00')
        current += relativedelta(months=1)

    for salary in EmploymentSalaries.objects.filter(employment=employment).order_by('start_date'):
        current = salary.start_date.replace(day=1)
        while current <= (salary.end_date if salary.end_date else employment.end_date):
            key = current.strftime("%Y-%m")
            if key in months:
                months[key] += salary.salary
            current += relativedelta(months=1)
    for (month, salary) in months.items():
        if salary == Decimal('0.00'):
            messages.warning(request, f"Die Anstellung {employment} hat kein Gehalt für den Monat {month}.")

def staff_checks(request: HttpRequest, staff_member: StaffMember) -> None:
    for employment in staff_member.employment_set.all():
        check_staff_employments_full_salaries(request, employment)

def full_checks(request: HttpRequest) -> None:
    for staff_member in StaffMember.objects.all():
        staff_checks(request, staff_member)

    check_staff_salaries_in_employments(request)