from django.urls import path

from . import views

app_name = "staffing"
urlpatterns = [
    path("", views.index, name="index"),
    path("details/<int:staff_id>/", views.details, name="details"),
    path("plan/", views.plan_employment, name="plan_employment"),
    path("employment/<int:employment_id>/estimate/", views.estimate_salaries, name="estimate_salaries"),
]