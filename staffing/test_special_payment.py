from datetime import date
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from staffing.models import Employment, EmploymentSalaries, SalaryCategory, StaffMember
from staffing.tvl import special_payment_rate_for
from staffing.utils import apply_tariff_salaries, get_salaries_by_month, special_payment


STATIC_STORAGE = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


class SpecialPaymentRateTests(TestCase):
    def test_rates_by_group_name(self):
        self.assertEqual(special_payment_rate_for("E13"), Decimal("46.47"))
        self.assertEqual(special_payment_rate_for("E 9b"), Decimal("74.35"))
        self.assertEqual(special_payment_rate_for("E13Ü"), Decimal("46.47"))
        self.assertEqual(special_payment_rate_for("E14"), Decimal("32.53"))
        self.assertIsNone(special_payment_rate_for("W2"))


@override_settings(STORAGES=STATIC_STORAGE)
class SpecialPaymentTests(TestCase):
    def setUp(self):
        self.e13 = SalaryCategory.objects.create(name="E13", special_payment_rate=Decimal("46.47"))
        call_command("load_tvl_tables", stdout=StringIO())
        self.member = StaffMember.objects.create(first_name="Arefeh", last_name="Test")

    def employment(self, start, end, **kwargs):
        defaults = dict(
            staff_member=self.member, start_date=start, end_date=end, percentage=Decimal("100"),
            salary_category=self.e13, start_level=1, level_start_date=start,
        )
        defaults.update(kwargs)
        return Employment.objects.create(**defaults)

    def test_reproduces_sap_november_2025(self):
        employment = self.employment(date(2025, 2, 15), date(2026, 10, 31))

        bonus = special_payment(employment, 2025)

        # SAP: 1.972,15 € Jahressonderzahlung, 2.518,25 € incl. Arbeitgeberanteile.
        self.assertEqual(bonus["gross"], Decimal("1972.15"))
        self.assertEqual(bonus["cost"], Decimal("2518.25"))

    def test_start_after_august_uses_first_full_month(self):
        employment = self.employment(date(2026, 10, 15), date(2028, 12, 31))

        bonus = special_payment(employment, 2026)

        # E13 Stufe 1 in November 2026: 4.759,37 €; October to December paid.
        expected = (Decimal("4759.37") * Decimal("46.47") / 100 * 3 / 12).quantize(Decimal("0.01"))
        self.assertEqual(bonus["gross"], expected)

    def test_no_payment_without_employment_on_first_december(self):
        employment = self.employment(date(2026, 1, 1), date(2026, 11, 30))

        self.assertIsNone(special_payment(employment, 2026))

    def test_consecutive_employments_count_as_one(self):
        self.employment(date(2025, 1, 1), date(2025, 6, 30))
        second = self.employment(date(2025, 7, 1), date(2025, 12, 31), start_level=1, level_start_date=date(2025, 1, 1))

        bonus = special_payment(second, 2025)

        expected = (Decimal("4629.74") * Decimal("46.47") / 100).quantize(Decimal("0.01"))
        self.assertEqual(bonus["gross"], expected)

    def test_estimate_keeps_earlier_months_and_adds_bonus_in_november(self):
        employment = self.employment(date(2025, 2, 15), date(2026, 10, 31))
        EmploymentSalaries.objects.create(
            employment=employment, salary=Decimal("5000.00"), start_date=date(2025, 2, 15), end_date=date(2026, 10, 31),
        )

        apply_tariff_salaries(employment, from_month="2025-06")

        salaries = get_salaries_by_month(employment)
        self.assertEqual(salaries["2025-05"], Decimal("5000.00"))
        self.assertEqual(salaries["2025-06"], Decimal("5917.69"))
        self.assertEqual(salaries["2025-11"], Decimal("5917.69") + Decimal("2518.25"))
        self.assertEqual(salaries["2026-04"], Decimal("6531.77"))

    def test_estimate_view(self):
        self.client.force_login(get_user_model().objects.create_user("user", password="x"))
        employment = self.employment(date(2026, 1, 1), date(2026, 12, 31))

        response = self.client.post(reverse("staffing:estimate_salaries", args=[employment.id]), {"from_month": "2026-01"})

        self.assertRedirects(response, reverse("staffing:details", args=[self.member.id]))
        self.assertEqual(get_salaries_by_month(employment)["2026-04"], Decimal("6087.27"))

    def test_estimate_view_sets_missing_pay_grade(self):
        self.client.force_login(get_user_model().objects.create_user("user", password="x"))
        employment = Employment.objects.create(
            staff_member=self.member, start_date=date(2026, 1, 1), end_date=date(2026, 12, 31), percentage=Decimal("100"),
        )

        details = self.client.get(reverse("staffing:details", args=[self.member.id]))
        self.assertContains(details, f'id="estimate-form-{employment.id}"')

        self.client.post(reverse("staffing:estimate_salaries", args=[employment.id]), {
            "salary_category": self.e13.id, "start_level": "1", "level_start_date": "2026-01-01", "from_month": "2026-01",
        })

        employment.refresh_from_db()
        self.assertEqual((employment.salary_category, employment.start_level), (self.e13, 1))
        self.assertEqual(get_salaries_by_month(employment)["2026-04"], Decimal("6087.27"))

    def test_estimate_view_rejects_level_start_after_employment(self):
        self.client.force_login(get_user_model().objects.create_user("user", password="x"))
        employment = Employment.objects.create(
            staff_member=self.member, start_date=date(2026, 1, 1), end_date=date(2026, 12, 31), percentage=Decimal("100"),
        )

        self.client.post(reverse("staffing:estimate_salaries", args=[employment.id]), {
            "salary_category": self.e13.id, "start_level": "1", "level_start_date": "2026-03-01",
        })

        employment.refresh_from_db()
        self.assertIsNone(employment.salary_category)
        self.assertFalse(employment.employmentsalaries_set.exists())
