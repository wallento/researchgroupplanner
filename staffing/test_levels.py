from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase

from staffing.models import Employment, StaffMember, level_progression


class LevelProgressionTests(SimpleTestCase):
    def test_waiting_times_and_month_start(self):
        self.assertEqual(
            level_progression(1, date(2020, 3, 15)),
            [
                (1, date(2020, 3, 15)),
                (2, date(2021, 3, 1)),
                (3, date(2023, 3, 1)),
                (4, date(2026, 3, 1)),
                (5, date(2030, 3, 1)),
                (6, date(2035, 3, 1)),
            ],
        )

    def test_final_level_has_no_further_steps(self):
        self.assertEqual(level_progression(6, date(2020, 1, 1)), [(6, date(2020, 1, 1))])


class EmploymentLevelTests(TestCase):
    def employment(self, **kwargs):
        member = StaffMember.objects.create(first_name="Erika", last_name="Muster")
        defaults = dict(
            staff_member=member, start_date=date(2025, 2, 15), end_date=date(2028, 12, 31), percentage=Decimal("100"),
            start_level=2, level_start_date=date(2024, 5, 1),
        )
        defaults.update(kwargs)
        return Employment(**defaults)

    def test_level_steps_within_employment(self):
        employment = self.employment()

        self.assertEqual(employment.level_steps(), [(2, date(2025, 2, 15)), (3, date(2026, 5, 1))])
        self.assertEqual(employment.level_at(date(2026, 4, 30)), 2)
        self.assertEqual(employment.level_at(date(2026, 5, 1)), 3)

    def test_without_level_data(self):
        employment = self.employment(start_level=None, level_start_date=None)

        self.assertEqual(employment.level_steps(), [])
        self.assertIsNone(employment.level_at(date(2026, 1, 1)))

    def test_level_and_date_required_together(self):
        with self.assertRaises(ValidationError):
            self.employment(level_start_date=None).clean()

    def test_level_start_not_after_employment_start(self):
        with self.assertRaises(ValidationError):
            self.employment(level_start_date=date(2025, 3, 1)).clean()
