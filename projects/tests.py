from datetime import date
from decimal import Decimal

from django.contrib import admin
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from .admin import AnnualPoolSAPFundInlineAdmin, ProjectSAPFundInlineAdmin
from .models import AnnualPool, Project, SAPFund


class SAPFundModelTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            acronym="TEST",
            start_date=date(2026, 1, 1),
            end_date=date(2027, 12, 31),
            budget_total=Decimal("1000.00"),
        )
        self.annual_pool = AnnualPool.objects.create(title="Pool 2026")

    def test_fund_can_belong_to_project(self):
        fund = SAPFund.objects.create(fund_number="P-100", project=self.project)

        self.assertEqual(list(self.project.sap_funds.all()), [fund])

    def test_fund_can_belong_to_annual_pool(self):
        fund = SAPFund.objects.create(fund_number="A-100", annual_pool=self.annual_pool)

        self.assertEqual(list(self.annual_pool.sap_funds.all()), [fund])

    def test_fund_can_be_a_universal_project(self):
        fund = SAPFund.objects.create(fund_number="UNIVERSAL", is_universal=True)

        self.assertIsNone(fund.project)
        self.assertIsNone(fund.annual_pool)

    def test_validation_rejects_missing_owner(self):
        with self.assertRaises(ValidationError):
            SAPFund(fund_number="INVALID").full_clean()

    def test_database_rejects_two_owners(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            SAPFund.objects.create(
                fund_number="INVALID",
                project=self.project,
                annual_pool=self.annual_pool,
            )

    def test_database_rejects_universal_project_with_owner(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            SAPFund.objects.create(
                fund_number="INVALID-UNIVERSAL",
                is_universal=True,
                project=self.project,
            )

    def test_fund_number_is_globally_unique(self):
        SAPFund.objects.create(fund_number="SHARED", project=self.project)

        with self.assertRaises(IntegrityError), transaction.atomic():
            SAPFund.objects.create(fund_number="SHARED", annual_pool=self.annual_pool)

    def test_label_is_used_in_display_name(self):
        fund = SAPFund(fund_number="P-100", label="Personalmittel", project=self.project)

        self.assertEqual(str(fund), "P-100 – Personalmittel")

    def test_negative_actuals_are_not_funding_by_default(self):
        fund = SAPFund.objects.create(fund_number="P-200", project=self.project)

        self.assertFalse(fund.treat_negative_actuals_as_funding)


class SAPFundAdminTests(TestCase):
    def test_project_admin_contains_fund_inline(self):
        project_admin = admin.site._registry[Project]

        self.assertIn(ProjectSAPFundInlineAdmin, project_admin.inlines)
        self.assertIn(
            "treat_negative_actuals_as_funding",
            ProjectSAPFundInlineAdmin.fields,
        )

    def test_annual_pool_admin_contains_fund_inline(self):
        annual_pool_admin = admin.site._registry[AnnualPool]

        self.assertIn(AnnualPoolSAPFundInlineAdmin, annual_pool_admin.inlines)
        self.assertIn(
            "treat_negative_actuals_as_funding",
            AnnualPoolSAPFundInlineAdmin.fields,
        )
