from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from projects.models import Project, StaffBudgetItem
from staffing.models import (
    Employment,
    EmploymentSalaries,
    SalaryCategory,
    SalaryTable,
    SalaryTableEntry,
    StaffFundingAllocation,
    StaffMember,
)
from staffing.utils import get_salaries_by_month, level_defaults


STATIC_STORAGE = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(STORAGES=STATIC_STORAGE)
class PlanEmploymentTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user("user", password="x"))
        self.project = Project.objects.create(
            acronym="FUTURE", start_date=date(2026, 1, 1), end_date=date(2028, 12, 31), budget_total=Decimal("500000"),
        )
        self.budget_item = StaffBudgetItem.objects.create(project=self.project, title="WiMi", amount=Decimal("300000"))
        self.e13 = SalaryCategory.objects.create(name="E13")
        table = SalaryTable.objects.create(name="TV-L 2026", valid_from=date(2026, 4, 1))
        SalaryTableEntry.objects.create(table=table, salary_category=self.e13, level=1, amount=Decimal("6000.00"))
        SalaryTableEntry.objects.create(table=table, salary_category=self.e13, level=2, amount=Decimal("6400.00"))
        self.url = reverse("staffing:plan_employment")

    def post(self, **overrides):
        data = {
            "first_name": "Neue", "last_name": "Person", "budget_item": self.budget_item.id, "percentage": "50",
            "start_date": "2026-11-15", "end_date": "2028-06-30", "category": "researcher", "status": "planned",
            "salary_category": self.e13.id, "start_level": "1", "statutory_health_insurance": "on",
        }
        data.update(overrides)
        return self.client.post(self.url, data)

    def test_creates_planned_employment_allocation_and_salaries(self):
        response = self.post()

        member = StaffMember.objects.get(last_name="Person")
        self.assertRedirects(response, reverse("staffing:details", args=[member.id]))
        self.assertEqual(member.status, "in_hire")
        employment = Employment.objects.get(staff_member=member)
        self.assertEqual(employment.status, "planned")
        self.assertEqual(employment.level_start_date, date(2026, 11, 15))
        allocation = StaffFundingAllocation.objects.get(employment=employment)
        self.assertEqual((allocation.budget_item, allocation.percentage), (self.budget_item, Decimal("50")))
        salaries = get_salaries_by_month(employment)
        # Stufe 1 at 50 %; first month prorated from the 15th; Stufe 2 from 01.11.2027.
        self.assertEqual(salaries["2026-12"], Decimal("3000.00"))
        self.assertEqual(salaries["2026-11"], Decimal("1600.00"))
        self.assertEqual(salaries["2027-11"], Decimal("3200.00"))
        # Partial first month (exact amount), Stufe 1 run, Stufe 2 run.
        self.assertEqual(EmploymentSalaries.objects.filter(employment=employment).count(), 3)

    def test_existing_person_without_salary_category(self):
        member = StaffMember.objects.create(first_name="Erika", last_name="Muster")

        self.post(staff_member=member.id, first_name="", last_name="", salary_category="", start_level="")

        employment = Employment.objects.get(staff_member=member)
        self.assertFalse(employment.employmentsalaries_set.exists())

    def test_person_required(self):
        response = self.post(first_name="", last_name="")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Employment.objects.exists())

    def test_project_preselects_budget_items(self):
        other = Project.objects.create(
            acronym="OTHER", start_date=date(2026, 1, 1), end_date=date(2028, 12, 31), budget_total=Decimal("1"),
        )
        StaffBudgetItem.objects.create(project=other, title="Fremd", amount=Decimal("1"))

        response = self.client.get(self.url, {"project": "FUTURE"})

        self.assertEqual(list(response.context["form"].fields["budget_item"].queryset), [self.budget_item])


@override_settings(STORAGES=STATIC_STORAGE)
class LevelDefaultsTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user("user", password="x"))
        self.e13 = SalaryCategory.objects.create(name="E13")
        self.member = StaffMember.objects.create(first_name="Erika", last_name="Muster")

    def employment(self, start, end, **kwargs):
        return Employment.objects.create(
            staff_member=self.member, start_date=start, end_date=end, percentage=Decimal("100"), **kwargs,
        )

    def test_continues_latest_employment_with_level(self):
        self.employment(date(2022, 1, 1), date(2022, 12, 31), salary_category=self.e13, start_level=1,
                        level_start_date=date(2022, 1, 1))
        self.employment(date(2024, 3, 1), date(2025, 2, 28), salary_category=self.e13, start_level=2,
                        level_start_date=date(2023, 1, 1))

        self.assertEqual(
            level_defaults(self.member),
            {"start_level": 2, "level_start_date": "2023-01-01", "salary_category": self.e13.id},
        )

    def test_without_level_data_starts_with_first_employment(self):
        self.employment(date(2023, 4, 1), date(2023, 12, 31))
        self.employment(date(2021, 10, 1), date(2022, 9, 30), salary_category=self.e13)

        self.assertEqual(
            level_defaults(self.member),
            {"start_level": 1, "level_start_date": "2021-10-01", "salary_category": self.e13.id},
        )

    def test_new_person_has_no_defaults(self):
        self.assertIsNone(level_defaults(self.member))

    def test_preselected_staff_prefills_form(self):
        self.employment(date(2024, 3, 1), date(2025, 2, 28), salary_category=self.e13, start_level=3,
                        level_start_date=date(2022, 6, 1))

        response = self.client.get(reverse("staffing:plan_employment"), {"staff": self.member.id})

        form = response.context["form"]
        self.assertEqual(form.initial["start_level"], 3)
        self.assertEqual(form.initial["level_start_date"], "2022-06-01")
        self.assertEqual(response.context["staff_defaults"][self.member.id]["start_level"], 3)
