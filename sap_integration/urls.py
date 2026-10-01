from django.urls import path

from sap_integration import views


app_name = "sap_integration"

urlpatterns = [
    path("", views.overview, name="overview"),
    path("<int:year>/", views.overview, name="overview_year"),
    path("<int:year>/fonds/<int:fund_id>/", views.fund_detail, name="fund_detail"),
    path("import/", views.upload_export, name="upload_export"),
    path("abgleich/<int:fund_id>/", views.reconciliation, name="reconciliation"),
    path("abgleich/<int:fund_id>/zuordnung/", views.cost_type_mapping, name="cost_type_mapping"),
    path("abgleich/<int:fund_id>/ohne-beleg/loeschen/", views.delete_orphan, name="delete_orphan"),
    path(
        "abgleich/<int:fund_id>/position/<int:position_id>/",
        views.position_detail,
        name="position_detail",
    ),
]
