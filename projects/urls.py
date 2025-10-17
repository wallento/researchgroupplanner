from django.urls import path

from . import views

app_name = "projects"
urlpatterns = [
    path("", views.index, name="index"),
    path("details/<str:acronym>/", views.details, name="details"),
    path("details/<str:acronym>/staff_budget_item/<int:id>/", views.staff_budget_item, name="staff_budget_item"),
]