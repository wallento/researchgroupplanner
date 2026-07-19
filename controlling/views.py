from decimal import Decimal
from datetime import date, timedelta
from .utils import render
from dateutil.relativedelta import relativedelta

from projects.models import AnnualPool, Landesstelle, OverheadBudgetItemShare, Project, StaffBudgetItem
from staffing.models import Employment, EmploymentSalaries, StaffFundingAllocation, StaffMember
from staffing.utils import get_salaries_by_month
from django.conf import settings
from django.utils import timezone
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.core.mail import send_mail
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST
from django.db.models import Sum

from projects.utils import calculate_salary_for_allocation


def _month_iter(start_date, end_date):
    current = start_date.replace(day=1)
    last = end_date.replace(day=1)
    while current <= last:
        yield current
        current += relativedelta(months=1)


def _month_key(date_obj):
    return date_obj.strftime("%Y-%m")


def _project_overhead_available_sum(project):
    total = Decimal("0.00")
    for item in project.overheadbudgetitem_set.all():
        total += item.available_amount()
    return total


def _project_overhead_total_sum(project):
    total = Decimal("0.00")
    for item in project.overheadbudgetitem_set.all():
        total += item.amount
    return total


def _iterate_month_starts(start_date, end_date):
    current = start_date.replace(day=1)
    last = end_date.replace(day=1)
    while current <= last:
        yield current
        current += relativedelta(months=1)


def warnings(request):
    today = timezone.now().date()
    warnings_list = []

    # 1) Budget checks on staff budget items.
    staff_budget_items = StaffBudgetItem.objects.select_related("project")
    for budget_item in staff_budget_items:
        if budget_item.get_eligibilities().count() == 0:
            warnings_list.append({
                "severity": "warning",
                "title": f"Personalbudget ohne Kategorien: {budget_item}",
                "detail": "Diesem Personalbudget sind keine zulässigen Beschäftigungskategorien zugewiesen.",
                "link": f"/projects/details/{budget_item.project.acronym}/",
            })

        projected_sum = Decimal("0.00")
        for allocation in StaffFundingAllocation.objects.filter(budget_item=budget_item).select_related("employment__staff_member"):
            assignment_end = allocation.end_date or allocation.employment.end_date
            project_end = budget_item.project.extension_planning_date or budget_item.project.end_date

            if allocation.start_date < budget_item.project.start_date or assignment_end > project_end:
                warnings_list.append({
                    "severity": "warning",
                    "title": f"Zuweisung außerhalb Budgetzeitraum: {budget_item}",
                    "detail": (
                        f"Die Zuweisung {allocation.employment.staff_member} ({allocation.start_date} - {assignment_end}) "
                        f"liegt außerhalb des Projektzeitraums {budget_item.project.start_date} - {project_end}."
                    ),
                    "link": f"/projects/details/{budget_item.project.acronym}/",
                })

            projected_sum += calculate_salary_for_allocation(allocation).salary_sum

        if projected_sum > budget_item.amount:
            warnings_list.append({
                "severity": "danger",
                "title": f"Budgetüberziehung Personalbudget: {budget_item.title}",
                "detail": (
                    f"Geplante Personalkosten {projected_sum} EUR überschreiten Budget {budget_item.amount} EUR "
                    f"(Projekt {budget_item.project.acronym})."
                ),
                "link": f"/projects/details/{budget_item.project.acronym}/",
            })

    # 2) Budget checks on total project level.
    projects = Project.objects.all()
    for project in projects:
        if settings.OVERHEAD_SPLIT_ENABLED:
            for overhead_item in project.overheadbudgetitem_set.all():
                distributed_percentage = sum(
                    share.percentage for share in overhead_item.overheadbudgetitemshare_set.all()
                )
                if distributed_percentage != Decimal("100.00"):
                    warnings_list.append({
                        "severity": "warning",
                        "title": f"Overhead-Verteilung unvollständig: {project.acronym}",
                        "detail": (
                            f"Der Overhead-Posten ({overhead_item.amount} EUR) ist aktuell mit "
                            f"{distributed_percentage}% verteilt. Erwartet sind 100%."
                        ),
                        "link": f"/projects/details/{project.acronym}/",
                    })

        staff_budget_sum = project.staffbudgetitem_set.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
        other_budget_sum = project.otherbudgetitem_set.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
        overhead_budget_sum = _project_overhead_total_sum(project)
        planned_total = staff_budget_sum + other_budget_sum + overhead_budget_sum

        if planned_total != project.budget_total:
            diff = (planned_total - project.budget_total).quantize(Decimal("0.01"))
            warnings_list.append({
                "severity": "warning",
                "title": f"Budgetsumme ungleich Fördersumme: {project.acronym}",
                "detail": (
                    f"Einzelsummen ergeben {planned_total} EUR, Fördersumme ist {project.budget_total} EUR "
                    f"(Differenz: {diff} EUR)."
                ),
                "link": f"/projects/details/{project.acronym}/",
            })

        has_personnel_allocation = StaffFundingAllocation.objects.filter(
            budget_item__project=project
        ).exists()
        if not has_personnel_allocation:
            warnings_list.append({
                "severity": "warning",
                "title": f"Keine Personalallokation im Projekt: {project.acronym}",
                "detail": "Es wurde noch kein Personalanteil auf Projekt-Personalbudgets allokiert.",
                "link": f"/projects/details/{project.acronym}/",
            })

        staff_sum = Decimal("0.00")
        for allocation in StaffFundingAllocation.objects.filter(budget_item__project=project):
            staff_sum += calculate_salary_for_allocation(allocation).salary_sum

        other_sum = project.otherbudgetitem_set.aggregate(total=Sum("otherbudgetitemtransaction__amount"))["total"] or Decimal("0.00")
        overhead_sum = _project_overhead_total_sum(project)
        total_allocated = staff_sum + other_sum + overhead_sum

        if total_allocated > project.budget_total:
            warnings_list.append({
                "severity": "danger",
                "title": f"Budgetüberziehung Projekt: {project.acronym}",
                "detail": (
                    f"Allokiert/gebunden {total_allocated} EUR bei Gesamtbudget {project.budget_total} EUR."
                ),
                "link": f"/projects/details/{project.acronym}/",
            })
        elif total_allocated < project.budget_total:
            remaining = project.budget_total - total_allocated
            warnings_list.append({
                "severity": "success",
                "title": f"Restbudget vorhanden: {project.acronym}",
                "detail": (
                    f"Aktuell sind noch {remaining} EUR Restbudget verfügbar "
                    f"(allokiert/gebunden: {total_allocated} EUR von {project.budget_total} EUR)."
                ),
                "link": f"/projects/details/{project.acronym}/",
            })

        if project.staffbudgetitem_set.count() == 0:
            warnings_list.append({
                "severity": "warning",
                "title": f"Projekt ohne Personalbudget: {project.acronym}",
                "detail": "Es ist kein Personalbudget-Posten hinterlegt.",
                "link": f"/projects/details/{project.acronym}/",
            })

        if project.overheadbudgetitem_set.count() == 0 and not project.no_overhead:
            warnings_list.append({
                "severity": "warning",
                "title": f"Projekt ohne Overheadposition: {project.acronym}",
                "detail": "Es ist keine Overhead-Position hinterlegt.",
                "link": f"/projects/details/{project.acronym}/",
            })

    # 3) Overlapping salary periods per employment.
    for employment in Employment.objects.select_related("staff_member").prefetch_related("stafffundingallocation_set"):
        salaries = list(EmploymentSalaries.objects.filter(employment=employment).order_by("start_date", "end_date"))
        allocations = list(employment.stafffundingallocation_set.all())
        has_project_funding = any(allocation.budget_item_id for allocation in allocations)
        is_landesstelle_only = bool(allocations) and not has_project_funding

        for salary in salaries:
            if salary.start_date < employment.start_date:
                warnings_list.append({
                    "severity": "warning",
                    "title": f"Gehalt vor Vertragsbeginn: {employment.staff_member}",
                    "detail": (
                        f"Gehalt {salary.start_date} - {salary.end_date} beginnt vor der Anstellung "
                        f"({employment.start_date} - {employment.end_date})."
                    ),
                    "link": f"/staffing/details/{employment.staff_member.id}/",
                })

            if salary.end_date > employment.end_date:
                warnings_list.append({
                    "severity": "warning",
                    "title": f"Gehalt nach Vertragsende: {employment.staff_member}",
                    "detail": (
                        f"Gehalt {salary.start_date} - {salary.end_date} endet nach der Anstellung "
                        f"({employment.start_date} - {employment.end_date})."
                    ),
                    "link": f"/staffing/details/{employment.staff_member.id}/",
                })

        missing_salary_months = []
        for month in _month_iter(employment.start_date, employment.end_date):
            month_total = Decimal("0.00")
            for salary in salaries:
                if salary.start_date.replace(day=1) <= month <= salary.end_date.replace(day=1):
                    month_total += salary.salary

            if month_total == Decimal("0.00"):
                missing_salary_months.append(_month_key(month))

        if missing_salary_months and not is_landesstelle_only:
            sample = ", ".join(missing_salary_months[:4])
            if len(missing_salary_months) > 4:
                sample += ", ..."
            warnings_list.append({
                "severity": "warning",
                "title": f"Fehlende Gehaltsmonate: {employment.staff_member}",
                "detail": (
                    f"Für {len(missing_salary_months)} Monat(e) der Anstellung {employment.start_date} - {employment.end_date} "
                    f"ist kein Gehalt hinterlegt: {sample}"
                ),
                "link": f"/staffing/details/{employment.staff_member.id}/",
            })

        for i in range(len(salaries) - 1):
            current = salaries[i]
            following = salaries[i + 1]
            if following.start_date <= current.end_date:
                overlap_start = following.start_date
                overlap_end = min(current.end_date, following.end_date)
                warnings_list.append({
                    "severity": "warning",
                    "title": f"Überlappende Gehaltssätze: {employment.staff_member}",
                    "detail": (
                        f"Gehalt {current.start_date} - {current.end_date} ({current.salary} €) überlappt mit "
                        f"{following.start_date} - {following.end_date} ({following.salary} €) "
                        f"im Zeitraum {overlap_start} - {overlap_end}."
                    ),
                    "link": f"/staffing/details/{employment.staff_member.id}/",
                    "merge_salary_ids": (current.id, following.id),
                })

    employments = Employment.objects.select_related("staff_member").prefetch_related(
        "stafffundingallocation_set__budget_item__project",
        "stafffundingallocation_set__landesstelle",
        "stafffundingallocation_set__annual_pool_budget__annual_pool",
    )

    for employment in employments:
        allocations = list(employment.stafffundingallocation_set.all())
        expected = Decimal(employment.percentage)

        if not allocations:
            warnings_list.append({
                "severity": "warning",
                "title": f"Keine Zuordnung für {employment.staff_member}",
                "detail": (
                    f"Die Anstellung ({employment.start_date} - {employment.end_date}, {employment.percentage}%) "
                    "hat keine Finanzierungszuordnungen."
                ),
                "link": f"/staffing/details/{employment.staff_member.id}/",
            })
            continue

        under_months = []
        over_months = []

        # Compare per month contract percentage vs summed allocations.
        for month in _month_iter(employment.start_date, employment.end_date):
            total = Decimal("0.00")
            for allocation in allocations:
                alloc_start = allocation.start_date
                alloc_end = allocation.end_date or employment.end_date
                if alloc_start.replace(day=1) <= month <= alloc_end.replace(day=1):
                    total += Decimal(allocation.percentage)

            if total < expected:
                under_months.append((_month_key(month), total))
            elif total > expected:
                over_months.append((_month_key(month), total))

        if under_months:
            sample = ", ".join(f"{m} ({p}%)" for m, p in under_months[:4])
            if len(under_months) > 4:
                sample += ", ..."
            warnings_list.append({
                "severity": "warning",
                "title": f"Unterallokation bei {employment.staff_member}",
                "detail": (
                    f"{len(under_months)} Monat(e) liegen unter dem Vertragsanteil von {employment.percentage}%: {sample}"
                ),
                "link": f"/staffing/details/{employment.staff_member.id}/",
            })

        if over_months:
            sample = ", ".join(f"{m} ({p}%)" for m, p in over_months[:4])
            if len(over_months) > 4:
                sample += ", ..."
            warnings_list.append({
                "severity": "danger",
                "title": f"Überallokation bei {employment.staff_member}",
                "detail": (
                    f"{len(over_months)} Monat(e) liegen über dem Vertragsanteil von {employment.percentage}%: {sample}"
                ),
                "link": f"/staffing/details/{employment.staff_member.id}/",
            })

        for allocation in allocations:
            alloc_end = allocation.end_date or employment.end_date

            if allocation.start_date < employment.start_date or alloc_end > employment.end_date:
                warnings_list.append({
                    "severity": "warning",
                    "title": f"Zuordnung außerhalb der Vertragslaufzeit ({employment.staff_member})",
                    "detail": (
                        f"Zuordnung {allocation.percentage}% läuft von {allocation.start_date} bis {alloc_end}, "
                        f"Vertrag aber nur von {employment.start_date} bis {employment.end_date}."
                    ),
                    "link": f"/staffing/details/{employment.staff_member.id}/",
                })

            if allocation.budget_item_id:
                project = allocation.budget_item.project
                project_end = project.extension_planning_date or project.end_date
                if allocation.start_date < project.start_date or alloc_end > project_end:
                    warnings_list.append({
                        "severity": "warning",
                        "title": f"Zuordnung außerhalb Projektlaufzeit ({project.acronym})",
                        "detail": (
                            f"Zuordnung {allocation.percentage}% ({allocation.start_date} - {alloc_end}) "
                            f"liegt außerhalb Projektzeitraum {project.start_date} - {project_end}."
                        ),
                        "link": f"/projects/details/{project.acronym}/",
                    })
            elif allocation.landesstelle_id and settings.LANDESSTELLEN_ENABLED:
                ls = allocation.landesstelle
                if allocation.start_date < ls.start_date:
                    warnings_list.append({
                        "severity": "warning",
                        "title": f"Zuordnung vor Landesstellen-Start ({ls.title})",
                        "detail": (
                            f"Zuordnung startet am {allocation.start_date}, Landesstelle aber erst am {ls.start_date}."
                        ),
                        "link": f"/projects/landesstelle/{ls.id}/",
                    })
                if ls.end_date and alloc_end > ls.end_date:
                    warnings_list.append({
                        "severity": "warning",
                        "title": f"Zuordnung nach Landesstellen-Ende ({ls.title})",
                        "detail": (
                            f"Zuordnung endet am {alloc_end}, Landesstelle aber am {ls.end_date}."
                        ),
                        "link": f"/projects/landesstelle/{ls.id}/",
                    })
            elif allocation.annual_pool_budget_id and settings.ANNUAL_POOLS_ENABLED:
                pool_budget = allocation.annual_pool_budget
                if allocation.start_date.year != pool_budget.year or alloc_end.year != pool_budget.year:
                    warnings_list.append({
                        "severity": "warning",
                        "title": f"Zuordnung außerhalb Annual-Pool-Jahr ({pool_budget.annual_pool.title})",
                        "detail": (
                            f"Zuordnung {allocation.percentage}% ({allocation.start_date} - {alloc_end}) "
                            f"liegt nicht vollständig im Budgetjahr {pool_budget.year}."
                        ),
                        "link": "/admin/projects/annualpool/",
                    })

    for staff_member in StaffMember.objects.prefetch_related("employment_set"):
        active_employments = [e for e in staff_member.employment_set.all() if e.start_date <= today <= e.end_date]
        if staff_member.status == "alumni" and active_employments:
            warnings_list.append({
                "severity": "warning",
                "title": f"Status-Inkonsistenz bei {staff_member}",
                "detail": "Person ist als Alumni markiert, hat aber aktive Anstellung(en).",
                "link": f"/staffing/details/{staff_member.id}/",
            })
        if staff_member.status in {"active", "in_hire"} and not active_employments:
            warnings_list.append({
                "severity": "warning",
                "title": f"Status-Inkonsistenz bei {staff_member}",
                "detail": "Person ist als aktiv/Einstellung markiert, hat aber keine aktive Anstellung.",
                "link": f"/staffing/details/{staff_member.id}/",
            })

    severity_order = {"danger": 0, "warning": 1, "info": 2, "success": 3}
    warnings_list.sort(key=lambda item: (severity_order.get(item["severity"], 99), item["title"]))

    return render(request, "controlling/warnings.html", {
        "warnings_list": warnings_list,
    })


@login_required
@require_POST
def merge_salary_overlap(request, current_id, following_id):
    current = get_object_or_404(EmploymentSalaries, pk=current_id)
    following = get_object_or_404(EmploymentSalaries, pk=following_id)

    if current.employment_id != following.employment_id:
        return redirect("warnings")

    if following.start_date > current.end_date:
        # No longer overlapping (already resolved) - nothing to do.
        return redirect("warnings")

    employment = current.employment
    overlap_start = following.start_date
    overlap_end = min(current.end_date, following.end_date)

    new_salaries = []

    if current.start_date < overlap_start:
        new_salaries.append({
            "start_date": current.start_date,
            "end_date": overlap_start - timedelta(days=1),
            "salary": current.salary,
        })

    new_salaries.append({
        "start_date": overlap_start,
        "end_date": overlap_end,
        "salary": current.salary + following.salary,
    })

    if current.end_date > overlap_end:
        new_salaries.append({
            "start_date": overlap_end + timedelta(days=1),
            "end_date": current.end_date,
            "salary": current.salary,
        })
    elif following.end_date > overlap_end:
        new_salaries.append({
            "start_date": overlap_end + timedelta(days=1),
            "end_date": following.end_date,
            "salary": following.salary,
        })

    with transaction.atomic():
        current.delete()
        following.delete()
        for entry in new_salaries:
            EmploymentSalaries.objects.create(employment=employment, **entry)

    return redirect("warnings")


def statistics(request):
    projects = Project.objects.all().order_by("start_date")
    yearly_totals = {}

    for project in projects:
        effective_end = project.end_date
        if project.extension_planning_date and project.extension_planning_date > project.end_date:
            effective_end = project.extension_planning_date

        month_count = (
            (effective_end.year - project.start_date.year) * 12
            + (effective_end.month - project.start_date.month)
            + 1
        )
        if month_count <= 0:
            continue

        monthly_share = (project.budget_total / Decimal(month_count))
        current = project.start_date.replace(day=1)
        end_month = effective_end.replace(day=1)

        while current <= end_month:
            key = str(current.year)
            yearly_totals[key] = yearly_totals.get(key, Decimal("0.00")) + monthly_share
            current += relativedelta(months=1)

    sorted_years = sorted(yearly_totals.keys())
    yearly_values = [yearly_totals[year].quantize(Decimal("0.01")) for year in sorted_years]

    cumulative_values = []
    running = Decimal("0.00")
    for value in yearly_values:
        running += value
        cumulative_values.append(running.quantize(Decimal("0.01")))

    total_third_party_funds = sum((project.budget_total for project in projects), Decimal("0.00")).quantize(Decimal("0.01"))

    # Overhead distribution per institute and year.
    overhead_institutes = []
    overhead_rows = []
    overhead_totals_list = []
    overhead_overall_total = Decimal("0.00")

    if settings.OVERHEAD_SPLIT_ENABLED:
        overhead_by_year_and_institute = {}
        institute_names = set()

        overhead_shares = OverheadBudgetItemShare.objects.select_related(
            "institute",
            "overhead_item__project",
        )

        for share in overhead_shares:
            project = share.overhead_item.project
            effective_end = project.end_date
            if project.extension_planning_date and project.extension_planning_date > project.end_date:
                effective_end = project.extension_planning_date

            month_count = (
                (effective_end.year - project.start_date.year) * 12
                + (effective_end.month - project.start_date.month)
                + 1
            )
            if month_count <= 0:
                continue

            institute_name = share.institute.short_name
            institute_names.add(institute_name)

            yearly_share = (share.overhead_item.amount * share.percentage / Decimal("100")) / Decimal(month_count)
            current = project.start_date.replace(day=1)
            end_month = effective_end.replace(day=1)

            while current <= end_month:
                year = str(current.year)
                overhead_by_year_and_institute.setdefault(year, {})
                overhead_by_year_and_institute[year][institute_name] = (
                    overhead_by_year_and_institute[year].get(institute_name, Decimal("0.00")) + yearly_share
                )
                current += relativedelta(months=1)

        overhead_institutes = sorted(institute_names)
        overhead_years = sorted(overhead_by_year_and_institute.keys())
        overhead_institute_totals = {name: Decimal("0.00") for name in overhead_institutes}

        for year in overhead_years:
            values = []
            row_total = Decimal("0.00")
            year_data = overhead_by_year_and_institute.get(year, {})
            for institute_name in overhead_institutes:
                value = year_data.get(institute_name, Decimal("0.00")).quantize(Decimal("0.01"))
                values.append(value)
                row_total += value
                overhead_institute_totals[institute_name] += value

            overhead_rows.append({
                "year": year,
                "values": values,
                "total": row_total.quantize(Decimal("0.01")),
            })

        overhead_totals_list = [
            overhead_institute_totals[name].quantize(Decimal("0.01"))
            for name in overhead_institutes
        ]
        overhead_overall_total = sum(overhead_totals_list, Decimal("0.00")).quantize(Decimal("0.01"))

    return render(request, "controlling/statistics.html", {
        "statistics_years": sorted_years,
        "statistics_yearly_values": [float(value) for value in yearly_values],
        "statistics_cumulative_values": [float(value) for value in cumulative_values],
        "statistics_total_third_party_funds": total_third_party_funds,
        "overhead_institutes": overhead_institutes,
        "overhead_rows": overhead_rows,
        "overhead_totals": overhead_totals_list,
        "overhead_overall_total": overhead_overall_total,
    })


def annual_pools(request):
    pools = AnnualPool.objects.prefetch_related("annualpoolbudget_set").order_by("title")
    pools_data = []

    for pool in pools:
        budgets = list(pool.annualpoolbudget_set.all().order_by("year"))
        rows = []
        total_assigned = Decimal("0.00")
        total_spent = Decimal("0.00")

        for budget in budgets:
            allocations = StaffFundingAllocation.objects.filter(
                annual_pool_budget=budget
            ).select_related("employment", "employment__staff_member")
            linked_staff = {}

            spent = Decimal("0.00")
            for allocation in allocations:
                employment = allocation.employment
                linked_staff[employment.staff_member.id] = str(employment.staff_member)
                if employment.percentage == 0:
                    continue

                salaries_by_month = get_salaries_by_month(employment)
                allocation_end = allocation.end_date or employment.end_date
                overlap_start = max(allocation.start_date, employment.start_date, date(budget.year, 1, 1))
                overlap_end = min(allocation_end, employment.end_date, date(budget.year, 12, 31))
                if overlap_end < overlap_start:
                    continue

                ratio = Decimal(allocation.percentage) / Decimal(employment.percentage)
                for month_start in _iterate_month_starts(overlap_start, overlap_end):
                    month_key = month_start.strftime("%Y-%m")
                    spent += salaries_by_month.get(month_key, Decimal("0.00")) * ratio

            spent = spent.quantize(Decimal("0.01"))
            assigned = budget.amount_assigned.quantize(Decimal("0.01"))
            remaining = (assigned - spent).quantize(Decimal("0.01"))
            utilization = Decimal("0.00") if assigned == 0 else ((spent / assigned) * Decimal("100.00")).quantize(Decimal("0.01"))

            rows.append({
                "year": budget.year,
                "assigned": assigned,
                "spent": spent,
                "remaining": remaining,
                "utilization": utilization,
                "linked_staff": [
                    {"id": staff_id, "name": name}
                    for staff_id, name in sorted(linked_staff.items(), key=lambda item: item[1])
                ],
            })

            total_assigned += assigned
            total_spent += spent

        pools_data.append({
            "pool": pool,
            "rows": rows,
            "total_assigned": total_assigned.quantize(Decimal("0.01")),
            "total_spent": total_spent.quantize(Decimal("0.01")),
            "total_remaining": (total_assigned - total_spent).quantize(Decimal("0.01")),
        })

    return render(request, "controlling/annual_pools.html", {
        "pools_data": pools_data,
    })

# Create your views here.
def main(request):
    today = timezone.now().date()
    projects = Project.objects.filter(
        Q(extension_planning_date__isnull=False, extension_planning_date__gt=today)
        | Q(extension_planning_date__isnull=True, end_date__gt=today)
    ).order_by('start_date')
    staff = StaffMember.objects.all()

    budgets_per_year = {}
    for project in projects:
        if project.budget_total is None:
            continue
        months = (project.end_date.year - project.start_date.year) * 12 + (project.end_date.month - project.start_date.month + 1)

        budgets_per_year.setdefault(str(project.start_date.year), {})[project.acronym] = (project.budget_total * (12 - project.start_date.month) / months).quantize(Decimal('0.01'))
        for year in range(project.start_date.year + 1, project.end_date.year):
            budgets_per_year.setdefault(str(year), {})[project.acronym] = (12 * project.budget_total / months).quantize(Decimal('0.01'))
        budgets_per_year.setdefault(str(project.end_date.year), {})[project.acronym] = (project.budget_total * project.end_date.month / months).quantize(Decimal('0.01'))

    for key, value in budgets_per_year.items():
        budgets_per_year[key]["total"] = sum(value.values())

    landesstellen = Landesstelle.objects.all().order_by('start_date') if settings.LANDESSTELLEN_ENABLED else Landesstelle.objects.none()

    employments = Employment.objects.exclude(staff_member__status='alumni').select_related('staff_member').prefetch_related(
        'stafffundingallocation_set__budget_item__project',
        'stafffundingallocation_set__landesstelle__institute',
        'stafffundingallocation_set__annual_pool_budget__annual_pool',
    ).order_by('staff_member__last_name', 'staff_member__first_name', 'start_date')

    staff_timeline_entries_hiwis = []
    staff_timeline_entries_employees = []
    
    for employment in employments:
        staff_name = str(employment.staff_member)
        allocation_lines = []
        allocations = employment.stafffundingallocation_set.all().order_by('start_date')
        for allocation in allocations:
            if allocation.budget_item_id:
                source = f"Projekt {allocation.budget_item.project.acronym}"
            elif allocation.annual_pool_budget_id:
                source = f"Annual Pool {allocation.annual_pool_budget.annual_pool.title} ({allocation.annual_pool_budget.year})"
            else:
                institute = f" ({allocation.landesstelle.institute.short_name})" if allocation.landesstelle.institute_id else ""
                source = f"Landesstelle {allocation.landesstelle.title}{institute}"

            alloc_end = allocation.end_date or employment.end_date
            if alloc_end < allocation.start_date:
                continue

            allocation_lines.append(
                f"- {source}: {allocation.percentage}% ({allocation.start_date} - {alloc_end})"
            )

        allocation_html = "<br>".join(allocation_lines) if allocation_lines else "Keine Zuordnungen"

        entry = {
            'staff': staff_name,
            'staff_id': employment.staff_member.id,
            'percentage': employment.percentage,
            'label': f"Vertrag {employment.percentage}%",
            'tooltip_html': (
                f"<strong>{staff_name}</strong><br>"
                f"Vertrag: {employment.get_category()} ({employment.percentage}%)<br>"
                f"{employment.start_date} - {employment.end_date}<br><br>"
                f"<strong>Zuordnungen:</strong><br>{allocation_html}"
            ),
            'start': employment.start_date,
            'end': employment.end_date,
        }
        
        # Separate by category: HiWis vs. Employees
        if employment.category == 'student':
            staff_timeline_entries_hiwis.append(entry)
        else:
            staff_timeline_entries_employees.append(entry)

    # Load project milestones
    milestones = []
    for project in projects:
        for milestone in project.milestones.all():
            milestones.append({
                'project': project.acronym,
                'project_id': project.id,
                'date': milestone.date,
                'title': milestone.title,
            })

    return render(request, "controlling/main.html", {
        "projects": projects,
        "staff_list": staff,
        "budgets_per_year": budgets_per_year,
        "landesstellen": landesstellen,
        "staff_timeline_entries_hiwis": staff_timeline_entries_hiwis,
        "staff_timeline_entries_employees": staff_timeline_entries_employees,
        "project_milestones": milestones,
    })


def send_test_email(request):
    """Temporary view to test email configuration"""
    if request.method == 'POST':
        # Try to get email from POST data or current user
        recipient_email = request.POST.get('email')
        if not recipient_email and request.user.is_authenticated:
            recipient_email = request.user.email
        
        # If still no email, try to find an admin user
        if not recipient_email:
            admin_user = StaffMember.objects.filter(is_leadership=True, email__isnull=False).exclude(email='').first()
            if admin_user:
                recipient_email = admin_user.email
        
        if not recipient_email:
            return JsonResponse({
                'success': False,
                'message': 'Keine E-Mail-Adresse gefunden. Bitte einen Leiter mit E-Mail in Admin konfigurieren.'
            })
        
        try:
            subject = 'Test-E-Mail - Research Group Planning Tool'
            message = f"""Hallo,

Dies ist eine Test-E-Mail vom Research Group Planning Tool.

Die E-Mail-Konfiguration funktioniert korrekt!

Testdatum: {timezone.now().strftime('%d.%m.%Y %H:%M:%S')}

Grüße,
Das Research Group Planning System"""
            
            send_mail(
                subject,
                message,
                from_email=None,  # Use DEFAULT_FROM_EMAIL from settings
                recipient_list=[recipient_email],
                fail_silently=False,
            )
            
            return JsonResponse({
                'success': True,
                'message': f'Test-E-Mail erfolgreich an {recipient_email} gesendet!'
            })
        except Exception as e:
            return JsonResponse({
                'success': False,
                'message': f'Fehler beim Versenden: {str(e)}'
            })
    
    return JsonResponse({'success': False, 'message': 'POST erforderlich'})
