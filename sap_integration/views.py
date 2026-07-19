from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_datetime

from controlling.utils import render
from projects.models import SAPFund
from sap_integration.cache import SAPCacheError, available_years, fund_values, load_year


def _ensure_enabled():
    if not settings.SAP_ENABLED:
        raise Http404("Die SAP-Integration ist deaktiviert.")


def _owner(fund):
    if fund.project_id:
        return fund.project.acronym, "Projekt"
    if fund.annual_pool_id:
        return fund.annual_pool.title, "Annual Pool"
    return "Universalprojekt", "Universalprojekt"


def _generated_at(payload):
    value = payload.get("generated_at") if payload else None
    return parse_datetime(value) if value else None


def _has_year_data(values):
    return values is not None and (
        values.get("has_budget") or bool(values.get("transactions"))
    )


@staff_member_required
def overview(request, year=None):
    _ensure_enabled()
    years = available_years(settings.SAP_DATA_DIR)
    selected_year = year if year is not None else (years[0] if years else None)
    payload = None
    cache_error = None
    if selected_year is not None:
        try:
            payload = load_year(settings.SAP_DATA_DIR, selected_year)
        except SAPCacheError as error:
            cache_error = str(error)

    cached_funds = payload.get("funds", {}) if payload else {}
    rows = []
    funds = SAPFund.objects.filter(is_active=True).select_related(
        "project", "annual_pool"
    ).order_by("fund_number")
    for fund in funds:
        owner, owner_type = _owner(fund)
        values = fund_values(cached_funds.get(fund.fund_number))
        if not _has_year_data(values):
            continue
        rows.append(
            {
                "fund": fund,
                "owner": owner,
                "owner_type": owner_type,
                "values": values,
            }
        )

    return render(
        request,
        "sap_integration/overview.html",
        {
            "years": years,
            "selected_year": selected_year,
            "rows": rows,
            "cache_error": cache_error,
            "generated_at": _generated_at(payload),
        },
    )


@staff_member_required
def fund_detail(request, year, fund_id):
    _ensure_enabled()
    fund = get_object_or_404(
        SAPFund.objects.select_related("project", "annual_pool"),
        pk=fund_id,
        is_active=True,
    )
    try:
        payload = load_year(settings.SAP_DATA_DIR, year)
    except SAPCacheError as error:
        raise Http404(str(error)) from error

    values = fund_values(payload.get("funds", {}).get(fund.fund_number))
    if not _has_year_data(values):
        raise Http404(f"Für Fonds {fund.fund_number} liegen {year} keine SAP-Daten vor.")
    owner, owner_type = _owner(fund)
    return render(
        request,
        "sap_integration/fund_detail.html",
        {
            "fund": fund,
            "owner": owner,
            "owner_type": owner_type,
            "year": year,
            "years": available_years(settings.SAP_DATA_DIR),
            "values": values,
            "generated_at": _generated_at(payload),
        },
    )
