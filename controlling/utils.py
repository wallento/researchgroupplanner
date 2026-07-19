from django.conf import settings
from django.shortcuts import render as django_render

from staffing.models import StaffMember
from projects.models import Landesstelle, Project

def render(request, template, parameters):
    parameters["group_projects"] = Project.objects.all().order_by("acronym")
    parameters["group_staff"] = StaffMember.objects.all().order_by("last_name")
    if settings.LANDESSTELLEN_ENABLED:
        parameters["group_landesstellen"] = Landesstelle.objects.all().order_by("title")

    return django_render(request, template, parameters)