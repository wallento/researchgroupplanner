from decimal import Decimal
from .utils import render
from dateutil.relativedelta import relativedelta

from projects.models import Landesstelle, Project, StaffBudgetItem
from staffing.models import Employment, EmploymentSalaries, StaffAssignment, StaffFundingAllocation, StaffMember
from django.utils import timezone
from django.db.models import Q
from django.db.models import Sum

from projects.checks import full_checks as projects_full_checks
from projects.utils import calculate_salary_for_assignment
from staffing.checks import full_checks as staffing_full_checks


def _month_iter(start_date, end_date):
    current = start_date.replace(day=1)
    last = end_date.replace(day=1)
    while current <= last:
        yield current
        current += relativedelta(months=1)


def _month_key(date_obj):
    return date_obj.strftime("%Y-%m")


def warnings(request):
    today = timezone.now().date()
    warnings_list = []

    # 1) Budget checks on staff budget items.
    staff_budget_items = StaffBudgetItem.objects.select_related("project")
    for budget_item in staff_budget_items:
        projected_sum = Decimal("0.00")
        for assignment in StaffAssignment.objects.filter(budget_item=budget_item).select_related("employment__staff_member"):
            projected_sum += calculate_salary_for_assignment(assignment).salary_sum

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
        staff_sum = Decimal("0.00")
        for assignment in StaffAssignment.objects.filter(budget_item__project=project):
            staff_sum += calculate_salary_for_assignment(assignment).salary_sum

        other_sum = project.otherbudgetitem_set.aggregate(total=Sum("otherbudgetitemtransaction__amount"))["total"] or Decimal("0.00")
        overhead_sum = project.overheadbudgetitem_set.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
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

        if project.overheadbudgetitem_set.count() == 0:
            warnings_list.append({
                "severity": "warning",
                "title": f"Projekt ohne Overheadposition: {project.acronym}",
                "detail": "Es ist keine Overhead-Position hinterlegt.",
                "link": f"/projects/details/{project.acronym}/",
            })

    # 3) Overlapping salary periods per employment.
    for employment in Employment.objects.select_related("staff_member"):
        salaries = list(EmploymentSalaries.objects.filter(employment=employment).order_by("start_date", "end_date"))
        for i in range(len(salaries) - 1):
            current = salaries[i]
            following = salaries[i + 1]
            if following.start_date <= current.end_date:
                warnings_list.append({
                    "severity": "warning",
                    "title": f"Überlappende Gehaltssätze: {employment.staff_member}",
                    "detail": (
                        f"Gehalt {current.start_date} - {current.end_date} überlappt mit "
                        f"{following.start_date} - {following.end_date}."
                    ),
                    "link": f"/staffing/details/{employment.staff_member.id}/",
                })

    employments = Employment.objects.select_related("staff_member").prefetch_related(
        "stafffundingallocation_set__budget_item__project",
        "stafffundingallocation_set__landesstelle",
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
            elif allocation.landesstelle_id:
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

    return render(request, "controlling/statistics.html", {
        "statistics_years": sorted_years,
        "statistics_yearly_values": [float(value) for value in yearly_values],
        "statistics_cumulative_values": [float(value) for value in cumulative_values],
        "statistics_total_third_party_funds": total_third_party_funds,
    })

# Create your views here.
def main(request):
    projects_full_checks(request)
    staffing_full_checks(request)

    today = timezone.now().date()
    projects = Project.objects.filter(
        Q(extension_planning_date__isnull=False, extension_planning_date__gt=today)
        | Q(extension_planning_date__isnull=True, end_date__gt=today)
    ).order_by('start_date')
    staff = StaffMember.objects.all()

    budgets_per_year = {}
    for project in projects:
        months = (project.end_date.year - project.start_date.year) * 12 + (project.end_date.month - project.start_date.month + 1)

        budgets_per_year.setdefault(str(project.start_date.year), {})[project.acronym] = (project.budget_total * (12 - project.start_date.month) / months).quantize(Decimal('0.01'))
        for year in range(project.start_date.year + 1, project.end_date.year):
            budgets_per_year.setdefault(str(year), {})[project.acronym] = (12 * project.budget_total / months).quantize(Decimal('0.01'))
        budgets_per_year.setdefault(str(project.end_date.year), {})[project.acronym] = (project.budget_total * project.end_date.month / months).quantize(Decimal('0.01'))

    for key, value in budgets_per_year.items():
        budgets_per_year[key]["total"] = sum(value.values())

    landesstellen = Landesstelle.objects.all().order_by('start_date')

    employments = Employment.objects.select_related('staff_member').prefetch_related(
        'stafffundingallocation_set__budget_item__project',
        'stafffundingallocation_set__landesstelle__institute',
    ).order_by('staff_member__last_name', 'staff_member__first_name', 'start_date')

    staff_timeline_entries = []
    for employment in employments:
        staff_name = str(employment.staff_member)
        allocation_lines = []
        allocations = employment.stafffundingallocation_set.all().order_by('start_date')
        for allocation in allocations:
            if allocation.budget_item_id:
                source = f"Projekt {allocation.budget_item.project.acronym}"
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

        staff_timeline_entries.append({
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
        })

    return render(request, "controlling/main.html", {
        "projects": projects,
        "staff_list": staff,
        "budgets_per_year": budgets_per_year,
        "landesstellen": landesstellen,
        "staff_timeline_entries": staff_timeline_entries,
    })