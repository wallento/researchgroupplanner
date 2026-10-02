from datetime import date
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from staffing.models import Employment, SalaryCategory, SalaryTable, SalaryTableEntry, StaffMember
from staffing.tvl import GROSS_TABLES, RATES_2025, RATES_2026, employer_cost


class EmployerCostTests(SimpleTestCase):
    def test_reproduces_sap_payroll(self):
        # Arefeh Mahdavi, E13: SAP Personalkosten per month.
        self.assertAlmostEqual(employer_cost(Decimal("4629.74"), RATES_2025), Decimal("5917.70"), delta=Decimal("0.02"))
        self.assertAlmostEqual(employer_cost(Decimal("4967.01"), RATES_2026), Decimal("6353.47"), delta=Decimal("0.02"))
        self.assertAlmostEqual(employer_cost(Decimal("5106.09"), RATES_2026), Decimal("6531.78"), delta=Decimal("0.02"))

    def test_health_insurance_capped_at_ceiling(self):
        gross = Decimal("7194.48")
        below_cap = employer_cost(gross, RATES_2026)
        uncapped = RATES_2026.__class__(**{**RATES_2026.__dict__, "ceiling_health": Decimal("99999")})
        self.assertLess(below_cap, employer_cost(gross, uncapped))

    def test_tables_follow_agreed_raises(self):
        for group, amounts in GROSS_TABLES["2027"].items():
            for old, new in zip(GROSS_TABLES["2026"][group].split(), amounts.split()):
                self.assertEqual((Decimal(old) * Decimal("1.02")).quantize(Decimal("0.01")), Decimal(new))


class LoadTvlTablesTests(TestCase):
    def test_fills_tables_for_existing_categories(self):
        e13 = SalaryCategory.objects.create(name="E13")

        call_command("load_tvl_tables", stdout=StringIO())
        call_command("load_tvl_tables", stdout=StringIO())

        self.assertEqual(SalaryTable.objects.count(), 5)
        self.assertEqual(SalaryTableEntry.objects.count(), 5 * 6)
        member = StaffMember.objects.create(first_name="Arefeh", last_name="Test")
        employment = Employment(
            staff_member=member, start_date=date(2025, 2, 15), end_date=date(2026, 10, 31), percentage=Decimal("100"),
            salary_category=e13, start_level=1, level_start_date=date(2025, 2, 15),
        )
        self.assertAlmostEqual(employment.tariff_amount_at(date(2026, 4, 1)), Decimal("6531.78"), delta=Decimal("0.02"))
