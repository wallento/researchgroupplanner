from django.urls import path

from sap_integration import views


app_name = "sap_integration"

urlpatterns = [
    path("", views.overview, name="overview"),
    path("<int:year>/", views.overview, name="overview_year"),
    path("<int:year>/fonds/<int:fund_id>/", views.fund_detail, name="fund_detail"),
]
