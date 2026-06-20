from decimal import Decimal
from controlling.utils import render
from django.db.models import Q
from django.utils import timezone

from .models import Landesstelle, StaffBudgetItem, Project
from staffing.models import StaffAssignment, StaffFundingAllocation
from django.http import HttpRequest
from django.shortcuts import get_object_or_404

from .checks import check_staff_budget_item
from .utils import calculate_salary_for_assignment, get_assignments_salary_sum_of_year, get_table_assignments, get_timeline_assignments


def index(request: HttpRequest):
    today = timezone.now().date()
    running_projects = Project.objects.filter(
        Q(extension_planning_date__isnull=False, extension_planning_date__gte=today)
        | Q(extension_planning_date__isnull=True, end_date__gte=today)
    ).order_by("start_date", "acronym")

    completed_projects = Project.objects.filter(
        Q(extension_planning_date__isnull=False, extension_planning_date__lt=today)
        | Q(extension_planning_date__isnull=True, end_date__lt=today)
    ).order_by("start_date", "acronym")

    return render(request, "projects/index.html", {
        "running_projects": running_projects,
        "completed_projects": completed_projects,
    })


def details(request: HttpRequest, acronym: str):
    project = get_object_or_404(Project, acronym=acronym)

    staff_budget_items = StaffBudgetItem.objects.filter(project=project).all()

    for budget_item in staff_budget_items:
        check_staff_budget_item(request, budget_item)

    for budget_item in staff_budget_items:
        budget_item.staff_assignments = []
        budget_item.projected_sum = Decimal("0.00")
        for assignment in StaffAssignment.objects.filter(budget_item=budget_item):
            salary_assignment = calculate_salary_for_assignment(assignment)
            budget_item.staff_assignments.append(salary_assignment)
            budget_item.projected_sum += salary_assignment.salary_sum
        budget_item.years = {}
        for year in project.get_years():
            budget_item.years[year] = Decimal("0.00")
            for assignment in budget_item.staff_assignments:
                budget_item.years[year] += get_assignments_salary_sum_of_year(year, assignment)
        budget_item.remain = budget_item.amount - budget_item.projected_sum

    table_assignments = get_table_assignments(project, staff_budget_items)
    timeline_assignments = get_timeline_assignments(project)

    other_budget_items = project.otherbudgetitem_set.all()
    for item in other_budget_items:
        item.years = {}
        item.projected_sum = Decimal("0.00")
        for year in project.get_years():
            item.years[year] = Decimal("0.00")
            for transaction in item.get_transactions(year):
                item.years[year] += transaction.amount
            item.projected_sum += item.years[year]
        item.remain = item.amount - item.projected_sum

    parameters = {
        "project": project,
        "staff_budget_items": staff_budget_items,
        "other_budget_items": other_budget_items,
        "table_assignments": table_assignments,
        "timeline_assignments": timeline_assignments
    }

    return render(request, "projects/details.html", parameters)

def staff_budget_item(request: HttpRequest, acronym: str, id: int):
    project = get_object_or_404(Project, acronym=acronym)
    budget_item = get_object_or_404(StaffBudgetItem, id=id, project=project)

    # Render the budget item details
    return render(request, "projects/staff_budget_item.html", {"project": project, "budget_item": budget_item})


def landesstelle_detail(request: HttpRequest, id: int):
    landesstelle = get_object_or_404(Landesstelle, id=id)
    allocations = StaffFundingAllocation.objects.filter(landesstelle=landesstelle).select_related(
        "employment__staff_member"
    ).order_by("start_date")
    return render(request, "projects/landesstelle_detail.html", {
        "landesstelle": landesstelle,
        "allocations": allocations,
    })