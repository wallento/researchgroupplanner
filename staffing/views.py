from controlling.utils import render

from django.shortcuts import get_object_or_404
from staffing.models import StaffMember
from django.http import HttpRequest
from django.utils import timezone

from projects.models import EmploymentCategories

from .utils import get_salaries_by_month

def index(request):
    today = timezone.now().date()
    staff_members = StaffMember.objects.prefetch_related("employment_set").all().order_by("last_name", "first_name")

    category_order = list(EmploymentCategories.keys())
    category_labels = dict(EmploymentCategories)

    current_by_category = {}
    former_by_category = {}

    def category_index(key):
        return category_order.index(key) if key in category_order else len(category_order)

    for staff_member in staff_members:
        employments = sorted(list(staff_member.employment_set.all()), key=lambda e: (e.start_date, e.end_date))
        active_employments = [e for e in employments if e.start_date <= today <= e.end_date]

        if active_employments:
            category_key = active_employments[-1].category
        elif employments:
            category_key = employments[-1].category
        else:
            category_key = "uncategorized"

        category_label = category_labels.get(category_key, "Ohne Kategorie")
        is_former = staff_member.status == "alumni" or (employments and all(e.end_date < today for e in employments))

        target = former_by_category if is_former else current_by_category
        target.setdefault(category_key, {
            "label": category_label,
            "staff": [],
        })["staff"].append(staff_member)

    def sort_grouped(data):
        result = []
        for key, value in data.items():
            value["staff"].sort(key=lambda s: (s.last_name, s.first_name))
            result.append((key, value))
        result.sort(key=lambda item: (category_index(item[0]), item[1]["label"]))
        return result

    return render(request, "staffing/index.html", {
        "current_staff_by_category": sort_grouped(current_by_category),
        "former_staff_by_category": sort_grouped(former_by_category),
    })

def details(request: HttpRequest, staff_id: int):
    staff_member = get_object_or_404(StaffMember, id=staff_id)

    employments = staff_member.employment_set.prefetch_related(
        "stafffundingallocation_set__budget_item__project",
        "stafffundingallocation_set__landesstelle",
        "stafffundingallocation_set__annual_pool_budget__annual_pool",
    ).all()
    for employment in employments:
        employment.salaries_by_month = get_salaries_by_month(employment)
        employment.allocations = employment.stafffundingallocation_set.all().order_by("start_date")

    return render(request, "staffing/details.html", {"staff_member": staff_member, "employments": employments})