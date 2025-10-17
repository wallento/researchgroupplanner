from django.urls import path

from . import views

app_name = "staffing"
urlpatterns = [
    path("", views.index, name="index"),
    path("details/<int:staff_id>/", views.details, name="details"),
]