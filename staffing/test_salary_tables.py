from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from staffing.models import (
    Employment,
    SalaryCategory,
    SalaryTable,
    SalaryTableEntry,
    StaffMember,
    tariff_amount,
)


class SalaryTableTests(TestCase):
    def setUp(self):
        self.e13 = SalaryCategory.objects.create(name="E13")
        self.old = SalaryTable.objects.create(name="TV-L 2024", valid_from=date(2024, 11, 1), valid_until=date(2025, 1, 31))
        self.new = SalaryTable.objects.create(name="TV-L 2025", valid_from=date(2025, 2, 1))
        SalaryTableEntry.objects.create(table=self.old, salary_category=self.e13, level=2, amount=Decimal("5000.00"))
        SalaryTableEntry.objects.create(table=self.new, salary_category=self.e13, level=2, amount=Decimal("5200.00"))
        SalaryTableEntry.objects.create(table=self.new, salary_category=self.e13, level=3, amount=Decimal("5500.00"))

    def test_amount_from_table_valid_on_day(self):
        self.assertEqual(tariff_amount(self.e13, 2, date(2025, 1, 31)), Decimal("5000.00"))
        self.assertEqual(tariff_amount(self.e13, 2, date(2025, 2, 1)), Decimal("5200.00"))
        self.assertIsNone(tariff_amount(self.e13, 2, date(2024, 10, 31)))
        self.assertIsNone(tariff_amount(self.e13, 4, date(2025, 2, 1)))

    def test_overlapping_tables_are_rejected(self):
        with self.assertRaises(ValidationError):
            SalaryTable(name="Doppelt", valid_from=date(2025, 6, 1)).clean()
        with self.assertRaises(ValidationError):
            SalaryTable(name="Davor", valid_from=date(2024, 1, 1), valid_until=date(2024, 11, 1)).clean()

    def test_end_before_start_is_rejected(self):
        with self.assertRaises(ValidationError):
            SalaryTable(name="Falsch", valid_from=date(2023, 5, 1), valid_until=date(2023, 4, 1)).clean()

    def test_employment_uses_level_and_table(self):
        member = StaffMember.objects.create(first_name="Erika", last_name="Muster")
        employment = Employment(
            staff_member=member, start_date=date(2025, 2, 15), end_date=date(2028, 12, 31), percentage=Decimal("100"),
            salary_category=self.e13, start_level=2, level_start_date=date(2024, 5, 1),
        )

        self.assertEqual(employment.tariff_amount_at(date(2025, 3, 1)), Decimal("5200.00"))
        self.assertEqual(employment.tariff_amount_at(date(2026, 5, 1)), Decimal("5500.00"))

    def test_part_time_scales_full_time_amount(self):
        member = StaffMember.objects.create(first_name="Max", last_name="Teilzeit")
        employment = Employment(
            staff_member=member, start_date=date(2025, 2, 15), end_date=date(2028, 12, 31), percentage=Decimal("65"),
            salary_category=self.e13, start_level=2, level_start_date=date(2024, 5, 1),
        )

        self.assertEqual(employment.tariff_amount_at(date(2025, 3, 1)), Decimal("3380.00"))
