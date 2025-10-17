from decimal import Decimal
from .utils import render

from projects.models import Project
from staffing.models import StaffMember
from django.utils import timezone

from projects.checks import full_checks as projects_full_checks
from staffing.checks import full_checks as staffing_full_checks

# Create your views here.
def main(request):
    projects_full_checks(request)
    staffing_full_checks(request)

    projects = Project.objects.filter(end_date__gt=timezone.now()).order_by('start_date')
    staff = StaffMember.objects.all()

    budgets_per_year = {}
    for project in projects:
        months = (project.end_date.year - project.start_date.year) * 12 + (project.end_date.month - project.start_date.month + 1)

        budgets_per_year.setdefault(str(project.start_date.year), {})[project.acronym] = (project.budget_total * (12 - project.start_date.month) / months).quantize(Decimal('0.01'))
        for year in range(project.start_date.year + 1, project.end_date.year):
            budgets_per_year.setdefault(str(year), {})[project.acronym] = (12 * project.budget_total / months).quantize(Decimal('0.01'))
        budgets_per_year.setdefault(str(project.end_date.year), {})[project.acronym] = (project.budget_total * project.end_date.month / months).quantize(Decimal('0.01'))

    for key, value in budgets_per_year.items():
        budgets_per_year[key]["total"] = sum(value.values())

    return render(request, "controlling/main.html", {"projects": projects, "staff_list": staff, "budgets_per_year": budgets_per_year})