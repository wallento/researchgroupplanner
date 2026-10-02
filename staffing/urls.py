from django.urls import path

from . import views

app_name = "staffing"
urlpatterns = [
    path("", views.index, name="index"),
    path("details/<int:staff_id>/", views.details, name="details"),
    path("plan/", views.plan_employment, name="plan_employment"),
    path("umbuchungen/", views.rebookings, name="rebookings"),
    path("employment/<int:employment_id>/estimate/", views.estimate_salaries, name="estimate_salaries"),
    path("allocation/<int:allocation_id>/edit/", views.edit_allocation, name="edit_allocation"),
    path("allocation/<int:allocation_id>/rebook/", views.rebook_allocation, name="rebook_allocation"),
    path("rebooking/<int:rebooking_id>/edit/", views.edit_rebooking, name="edit_rebooking"),
    path("rebooking/<int:rebooking_id>/delete/", views.delete_rebooking, name="delete_rebooking"),
    path("rebooking/<int:rebooking_id>/complete/", views.complete_rebooking, name="complete_rebooking"),
]