"""
URL configuration for groupplanning project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.conf import settings
from django.urls import include, path
from django.contrib.staticfiles.urls import staticfiles_urlpatterns

from controlling.views import (
    annual_pools as controlling_annual_pools,
    apply_all_sap_salaries as controlling_apply_all_sap_salaries,
    apply_sap_salary as controlling_apply_sap_salary,
    create_manual_backup,
    main as controlling_main,
    merge_salary_overlap as controlling_merge_salary_overlap,
    send_test_email,
    statistics as controlling_statistics,
    warnings as controlling_warnings,
)

urlpatterns = [
    path(
        "login/",
        auth_views.LoginView.as_view(
            template_name="registration/login.html",
            redirect_authenticated_user=True,
        ),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", controlling_main, name="main"),
    path("warnings/", controlling_warnings, name="warnings"),
    path(
        "warnings/merge-salary-overlap/<int:current_id>/<int:following_id>/",
        controlling_merge_salary_overlap,
        name="merge_salary_overlap",
    ),
    path(
        "warnings/apply-sap-salary/<int:staff_id>/<int:year>/<int:month>/",
        controlling_apply_sap_salary,
        name="apply_sap_salary",
    ),
    path(
        "warnings/apply-sap-salaries/<int:staff_id>/",
        controlling_apply_all_sap_salaries,
        name="apply_all_sap_salaries",
    ),
    path("statistics/", controlling_statistics, name="statistics"),
    path("send-test-email/", send_test_email, name="send_test_email"),
    path("create-backup/", create_manual_backup, name="create_manual_backup"),
    path("annual-pools/", controlling_annual_pools, name="annual_pools"),
    path("staffing/", include("staffing.urls")),
    path("projects/", include("projects.urls")),
    path("ist-stand/", include("sap_integration.urls")),
    path("admin/", admin.site.urls),
]

if settings.DEBUG:
    urlpatterns.append(path("__reload__/", include("django_browser_reload.urls")))
    urlpatterns += staticfiles_urlpatterns()
