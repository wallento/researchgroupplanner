from django.shortcuts import render as django_render

from staffing.models import StaffMember
from projects.models import Project

def render(request, template, parameters):
    parameters["group_projects"] = Project.objects.all().order_by("acronym")
    parameters["group_staff"] = StaffMember.objects.all().order_by("last_name")

    return django_render(request, template, parameters)