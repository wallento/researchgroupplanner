from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from projects.models import OtherBudgetItem, OtherBudgetItemTransaction, Project, ReportingPeriod, StaffBudgetItem
from projects.utils import amount_in_period
from staffing.models import Employment, EmploymentSalaries, StaffFundingAllocation, StaffMember


STATIC_STORAGE = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(STORAGES=STATIC_STORAGE)
class ReportingPeriodTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user("user", password="x"))
        self.project = Project.objects.create(acronym="RP", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
                                              budget_total=Decimal("100000"))
        staff_item = StaffBudgetItem.objects.create(project=self.project, title="WiMi", amount=Decimal("60000"))
        travel = OtherBudgetItem.objects.create(project=self.project, title="Reisen", amount=Decimal("5000"))
        OtherBudgetItemTransaction.objects.create(budget_item=travel, date=date(2026, 3, 10), amount=Decimal("700"))
        OtherBudgetItemTransaction.objects.create(budget_item=travel, date=date(2026, 9, 1), amount=Decimal("300"))
        member = StaffMember.objects.create(first_name="Erika", last_name="Muster")
        employment = Employment.objects.create(staff_member=member, start_date=date(2026, 1, 1),
                                               end_date=date(2026, 12, 31), percentage=Decimal("100"))
        EmploymentSalaries.objects.create(employment=employment, salary=Decimal("3000"),
                                          start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
        StaffFundingAllocation.objects.create(employment=employment, budget_item=staff_item,
                                              percentage=Decimal("100"), start_date=date(2026, 1, 1))

    def test_amount_in_period_prorates_partial_months(self):
        months = {"2026-04": Decimal("3000"), "2026-05": Decimal("3100")}

        self.assertEqual(amount_in_period(months, date(2026, 4, 16), date(2026, 5, 31)), Decimal("4600.00"))

    def test_details_show_periods_with_costs_and_pm(self):
        ReportingPeriod.objects.create(project=self.project, title="ZB 1", start_date=date(2026, 1, 1),
                                       end_date=date(2026, 6, 30))
        ReportingPeriod.objects.create(project=self.project, start_date=date(2026, 7, 1), end_date=date(2026, 12, 31))

        response = self.client.get(reverse("projects:details", args=["RP"]))

        reporting = response.context["reporting"]
        staff_row, travel_row = reporting["rows"]
        self.assertEqual(staff_row["cells"], [(Decimal("18000.00"), Decimal("6")), (Decimal("18000.00"), Decimal("6"))])
        self.assertEqual([cell[0] for cell in travel_row["cells"]], [Decimal("700"), Decimal("300")])
        self.assertEqual(reporting["totals"][0], (Decimal("18700.00"), Decimal("6")))
        self.assertContains(response, "ZB 1")
        self.assertContains(response, "reporting_period_")

    def test_without_periods_no_section(self):
        response = self.client.get(reverse("projects:details", args=["RP"]))

        self.assertIsNone(response.context["reporting"])
        self.assertNotContains(response, "<h2>Berichtszeiträume</h2>")

    def test_end_before_start_rejected(self):
        with self.assertRaises(ValidationError):
            ReportingPeriod(project=self.project, start_date=date(2026, 5, 1), end_date=date(2026, 4, 1)).clean()
