from decimal import Decimal
from controlling.utils import render
from django.db.models import Q
from django.utils import timezone

from .models import Landesstelle, StaffBudgetItem, Project
from staffing.models import StaffFundingAllocation
from django.http import HttpRequest
from django.shortcuts import get_object_or_404

from .utils import calculate_salary_for_allocation, get_allocations_salary_sum_of_year, get_table_allocations, get_timeline_allocations


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
        budget_item.staff_allocations = []
        budget_item.projected_sum = Decimal("0.00")
        for allocation in StaffFundingAllocation.objects.filter(budget_item=budget_item).select_related("employment__staff_member"):
            salary_allocation = calculate_salary_for_allocation(allocation)
            budget_item.staff_allocations.append(salary_allocation)
            budget_item.projected_sum += salary_allocation.salary_sum
        budget_item.years = {}
        for year in project.get_years():
            budget_item.years[year] = Decimal("0.00")
            for allocation in budget_item.staff_allocations:
                budget_item.years[year] += get_allocations_salary_sum_of_year(year, allocation)
        budget_item.remain = budget_item.amount - budget_item.projected_sum

    table_assignments = get_table_allocations(project, staff_budget_items)
    timeline_assignments = get_timeline_allocations(project)

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

    total_staff_allocated = sum((item.projected_sum for item in staff_budget_items), Decimal("0.00"))
    total_other_allocated = sum((item.projected_sum for item in other_budget_items), Decimal("0.00"))
    total_overhead_allocated = sum((item.amount for item in project.overheadbudgetitem_set.all()), Decimal("0.00"))
    total_allocated = (total_staff_allocated + total_other_allocated + total_overhead_allocated).quantize(Decimal("0.01"))

    remain_sum = None
    if project.budget_total is not None:
        remain_sum = (project.budget_total - total_allocated).quantize(Decimal("0.01"))

    parameters = {
        "project": project,
        "staff_budget_items": staff_budget_items,
        "other_budget_items": other_budget_items,
        "table_assignments": table_assignments,
        "timeline_assignments": timeline_assignments,
        "allocated_sum": total_allocated,
        "remain_sum": remain_sum,
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