from decimal import Decimal
from .utils import render

from projects.models import Landesstelle, Project
from staffing.models import Employment, StaffMember
from django.utils import timezone

from projects.checks import full_checks as projects_full_checks
from staffing.checks import full_checks as staffing_full_checks

# Create your views here.
def main(request):
    projects_full_checks(request)
    staffing_full_checks(request)

    projects = Project.objects.filter(end_date__gt=timezone.now()).order_by('start_date')
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