from controlling.utils import render

from django.shortcuts import get_object_or_404
from staffing.models import StaffMember
from django.http import HttpRequest

from .checks import staff_checks

from .utils import get_salaries_by_month

def index(request):
    return HttpResponse("Hello, world. You're at the polls index.")

def details(request: HttpRequest, staff_id: int):
    staff_member = get_object_or_404(StaffMember, id=staff_id)
    staff_checks(request, staff_member)

    employments = staff_member.employment_set.all()
    for employment in employments:
        employment.salaries_by_month = get_salaries_by_month(employment)

    return render(request, "staffing/details.html", {"staff_member": staff_member, "employments": employments})