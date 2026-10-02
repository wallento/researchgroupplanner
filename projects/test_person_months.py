from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from projects.models import Project, StaffBudgetItem
from projects.utils import get_allocation_person_months
from staffing.models import Employment, StaffFundingAllocation, StaffMember


STATIC_STORAGE = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(STORAGES=STATIC_STORAGE)
class PersonMonthTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            acronym="PM", start_date=date(2025, 1, 1), end_date=date(2026, 12, 31), budget_total=Decimal("100000"),
        )
        self.item = StaffBudgetItem.objects.create(project=self.project, title="WiMi", amount=Decimal("80000"))
        member = StaffMember.objects.create(first_name="Erika", last_name="Muster")
        self.employment = Employment.objects.create(
            staff_member=member, start_date=date(2025, 4, 1), end_date=date(2026, 3, 31), percentage=Decimal("100"),
        )

    def allocation(self, **kwargs):
        defaults = dict(employment=self.employment, budget_item=self.item, percentage=Decimal("50"),
                        start_date=date(2025, 4, 1))
        defaults.update(kwargs)
        return StaffFundingAllocation.objects.create(**defaults)

    def test_full_months_times_percentage(self):
        pm = get_allocation_person_months(self.allocation())

        self.assertEqual(pm, {"2025": Decimal("4.5"), "2026": Decimal("1.5")})

    def test_partial_month_by_days(self):
        pm = get_allocation_person_months(self.allocation(
            percentage=Decimal("100"), start_date=date(2025, 4, 16), end_date=date(2025, 4, 30),
        ))

        self.assertEqual(pm, {"2025": Decimal("0.5")})

    def test_limited_to_employment(self):
        pm = get_allocation_person_months(self.allocation(percentage=Decimal("100"), end_date=date(2026, 12, 31)))

        self.assertEqual(sum(pm.values()), Decimal("12"))

    def test_project_details_show_person_months(self):
        self.allocation()
        self.client.force_login(get_user_model().objects.create_user("user", password="x"))

        response = self.client.get(reverse("projects:details", args=["PM"]))

        item = response.context["staff_budget_items"][0]
        self.assertEqual(item.pm_total, Decimal("6"))
        self.assertEqual(item.year_cells[0][1], Decimal("4.5"))
        self.assertContains(response, "6,0&nbsp;PM")
