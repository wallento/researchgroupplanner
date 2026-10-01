import json
import tempfile
from calendar import monthrange
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from projects.models import Project, SAPFund, StaffBudgetItem, StaffBudgetItemEligibility
from staffing.models import Employment, EmploymentSalaries, StaffFundingAllocation, StaffMember
from sap_integration.salary_sync import (
    apply_salary_comparison as apply_salary_value,
    build_salary_comparisons,
)


class BudgetWarningTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(
            username="warning-user",
            password="test-password",
        )
        self.client.force_login(user)

    def test_staff_budget_overrun_shows_difference(self):
        project = Project.objects.create(
            acronym="DIFF",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
            budget_total=Decimal("100.00"),
            no_overhead=True,
        )
        budget_item = StaffBudgetItem.objects.create(
            project=project,
            title="Personnel",
            amount=Decimal("100.00"),
        )
        StaffBudgetItemEligibility.objects.create(
            budget_item=budget_item,
            eligible_employment="researcher",
        )
        staff_member = StaffMember.objects.create(
            first_name="Test",
            last_name="Person",
        )
        employment = Employment.objects.create(
            staff_member=staff_member,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
            percentage=Decimal("100.00"),
        )
        EmploymentSalaries.objects.create(
            employment=employment,
            salary=Decimal("110.00"),
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
        )
        StaffFundingAllocation.objects.create(
            employment=employment,
            budget_item=budget_item,
            percentage=Decimal("100.00"),
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
        )

        response = self.client.get(reverse("warnings"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Differenz: 10.00 EUR")

    def test_warning_money_values_are_limited_to_two_decimal_places(self):
        project = Project.objects.create(
            acronym="ROUND",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
            budget_total=Decimal("1000.00"),
            no_overhead=True,
        )
        budget_item = StaffBudgetItem.objects.create(
            project=project,
            title="Personnel",
            amount=Decimal("1000.00"),
        )
        StaffBudgetItemEligibility.objects.create(
            budget_item=budget_item,
            eligible_employment="researcher",
        )
        staff_member = StaffMember.objects.create(
            first_name="Rounding",
            last_name="Person",
        )
        employment = Employment.objects.create(
            staff_member=staff_member,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
            percentage=Decimal("75.00"),
        )
        EmploymentSalaries.objects.create(
            employment=employment,
            salary=Decimal("100.00"),
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
        )
        StaffFundingAllocation.objects.create(
            employment=employment,
            budget_item=budget_item,
            percentage=Decimal("50.00"),
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
        )

        response = self.client.get(reverse("warnings"))

        self.assertContains(response, "933.33 EUR Restbudget")
        self.assertContains(response, "allokiert/gebunden: 66.67 EUR")
        self.assertNotContains(response, "666666666")


class StaffStatusWarningTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(
            username="staff-warning-user",
            password="test-password",
        )
        self.client.force_login(user)

    def test_leadership_without_employment_has_no_status_warning(self):
        staff_member = StaffMember.objects.create(
            first_name="Leadership",
            last_name="Person",
            is_leadership=True,
            status="active",
        )

        response = self.client.get(reverse("warnings"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(
            response,
            f"Status-Inkonsistenz bei {staff_member}",
        )

    def test_non_leadership_without_employment_keeps_status_warning(self):
        staff_member = StaffMember.objects.create(
            first_name="Regular",
            last_name="Person",
            is_leadership=False,
            status="active",
        )

        response = self.client.get(reverse("warnings"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f"Status-Inkonsistenz bei {staff_member}",
        )


class FundingAllocationCoverageWarningTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(
            username="allocation-warning-user",
            password="test-password",
        )
        self.client.force_login(user)
        self.staff_member = StaffMember.objects.create(
            first_name="Daily",
            last_name="Allocation",
            status="active",
        )
        self.employment = Employment.objects.create(
            staff_member=self.staff_member,
            start_date=date(2024, 4, 1),
            end_date=date(2025, 2, 16),
            percentage=Decimal("100.00"),
        )

    def _allocation(self, start_date, end_date):
        return StaffFundingAllocation.objects.create(
            employment=self.employment,
            is_universal=True,
            percentage=Decimal("100.00"),
            start_date=start_date,
            end_date=end_date,
        )

    def test_adjacent_allocations_in_same_month_do_not_overlap(self):
        self._allocation(date(2024, 4, 1), date(2024, 8, 15))
        self._allocation(date(2024, 8, 16), date(2025, 2, 16))

        response = self.client.get(reverse("warnings"))

        self.assertNotContains(
            response,
            f"Überallokation bei {self.staff_member}",
        )
        self.assertNotContains(
            response,
            f"Unterallokation bei {self.staff_member}",
        )

    def test_real_single_day_overlap_is_reported_exactly(self):
        self._allocation(date(2024, 4, 1), date(2024, 8, 16))
        self._allocation(date(2024, 8, 16), date(2025, 2, 16))

        response = self.client.get(reverse("warnings"))

        self.assertContains(response, f"Überallokation bei {self.staff_member}")
        self.assertContains(response, "16.08.2024 (200.00%)")

    def test_real_single_day_gap_is_reported_exactly(self):
        self._allocation(date(2024, 4, 1), date(2024, 8, 14))
        self._allocation(date(2024, 8, 16), date(2025, 2, 16))

        response = self.client.get(reverse("warnings"))

        self.assertContains(response, f"Unterallokation bei {self.staff_member}")
        self.assertContains(response, "15.08.2024 (0.00%)")


class SAPSalaryWarningTests(TestCase):
    def setUp(self):
        self.data_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.data_directory.cleanup)
        self.data_dir = Path(self.data_directory.name)
        user = get_user_model().objects.create_user(
            username="sap-salary-user",
            password="test-password",
        )
        self.client.force_login(user)
        self.project = Project.objects.create(
            acronym="SAP-SALARY",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            budget_total=Decimal("12000.00"),
            no_overhead=True,
        )
        self.fund = SAPFund.objects.create(
            fund_number="SALARY-FUND",
            project=self.project,
        )
        self.staff_member = StaffMember.objects.create(
            first_name="Test",
            last_name="Person",
            status="active",
        )
        self.employment = Employment.objects.create(
            staff_member=self.staff_member,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 3, 31),
            percentage=Decimal("100.00"),
        )
        EmploymentSalaries.objects.create(
            employment=self.employment,
            salary=Decimal("1000.00"),
            start_date=date(2026, 1, 1),
            end_date=date(2026, 3, 31),
        )
        _write_sap_salary_cache(
            self.data_dir,
            self.fund.fund_number,
            business_partner="Person, Test",
            amount="1100.00",
        )

    def test_warning_offers_sap_actual_salary_update(self):
        with self.settings(SAP_ENABLED=True, SAP_DATA_DIR=self.data_dir):
            response = self.client.get(reverse("warnings"))

        self.assertContains(response, "SAP-Gehaltsabweichungen bei Test Person")
        self.assertContains(response, "SAP-Ist 1100.00 EUR")
        self.assertContains(response, "Planung 1000.00 EUR")
        self.assertContains(response, "Alle SAP-Werte in Planung übernehmen")

    def test_sap_update_changes_only_selected_month(self):
        _write_sap_salary_cache(
            self.data_dir,
            self.fund.fund_number,
            business_partner="Person, Test",
            amount="1100.00",
            month_name="Februar",
        )
        url = reverse(
            "apply_sap_salary",
            args=[self.staff_member.id, 2026, 2],
        )
        with self.settings(SAP_ENABLED=True, SAP_DATA_DIR=self.data_dir):
            response = self.client.post(url, follow=True)

        self.assertEqual(response.status_code, 200)
        salaries = list(
            self.employment.employmentsalaries_set.order_by("start_date").values_list(
                "start_date",
                "end_date",
                "salary",
            )
        )
        self.assertEqual(
            salaries,
            [
                (date(2026, 1, 1), date(2026, 1, 31), Decimal("1000.00")),
                (date(2026, 2, 1), date(2026, 2, 28), Decimal("1100.00")),
                (date(2026, 3, 1), date(2026, 3, 31), Decimal("1000.00")),
            ],
        )
        self.assertContains(response, "wurde in die Planung übernommen")
        self.assertNotContains(response, "SAP-Gehaltsabweichungen bei Test Person")

    def test_consecutive_sap_updates_with_same_salary_are_merged(self):
        january_url = reverse(
            "apply_sap_salary",
            args=[self.staff_member.id, 2026, 1],
        )
        with self.settings(SAP_ENABLED=True, SAP_DATA_DIR=self.data_dir):
            self.client.post(january_url)

        _write_sap_salary_cache(
            self.data_dir,
            self.fund.fund_number,
            business_partner="Person, Test",
            amount="1100.00",
            month_name="Februar",
        )
        february_url = reverse(
            "apply_sap_salary",
            args=[self.staff_member.id, 2026, 2],
        )
        with self.settings(SAP_ENABLED=True, SAP_DATA_DIR=self.data_dir):
            self.client.post(february_url)

        salaries = list(
            self.employment.employmentsalaries_set.order_by("start_date").values_list(
                "start_date",
                "end_date",
                "salary",
            )
        )
        self.assertEqual(
            salaries,
            [
                (date(2026, 1, 1), date(2026, 2, 28), Decimal("1100.00")),
                (date(2026, 3, 1), date(2026, 3, 31), Decimal("1000.00")),
            ],
        )

    def test_sap_commitment_can_be_applied_to_planning(self):
        _write_sap_salary_cache(
            self.data_dir,
            self.fund.fund_number,
            business_partner="Person, Test",
            amount="1200.00",
            month_name="Februar",
            transaction_type="commitment",
        )

        with self.settings(SAP_ENABLED=True, SAP_DATA_DIR=self.data_dir):
            warning_response = self.client.get(reverse("warnings"))

        self.assertContains(warning_response, "SAP-Obligo 1200.00 EUR")
        self.assertContains(warning_response, "Alle SAP-Werte in Planung übernehmen")

        url = reverse(
            "apply_sap_salary",
            args=[self.staff_member.id, 2026, 2],
        )
        with self.settings(SAP_ENABLED=True, SAP_DATA_DIR=self.data_dir):
            update_response = self.client.post(url, follow=True)

        february_salary = self.employment.employmentsalaries_set.get(
            start_date=date(2026, 2, 1),
            end_date=date(2026, 2, 28),
        )
        self.assertEqual(february_salary.salary, Decimal("1200.00"))
        self.assertContains(update_response, "SAP-Obligo für Test Person")

    def test_equal_actual_and_commitment_prefer_actual(self):
        _write_sap_salary_cache(
            self.data_dir,
            self.fund.fund_number,
            business_partner="Person, Test",
            amount="1100.00",
            additional_commitment_amount="1100.00",
        )

        with self.settings(SAP_ENABLED=True, SAP_DATA_DIR=self.data_dir):
            response = self.client.get(reverse("warnings"))

        self.assertContains(response, "SAP-Ist 1100.00 EUR")
        self.assertContains(response, "Alle SAP-Werte in Planung übernehmen")
        self.assertNotContains(response, "unterschiedliche SAP-Ist-")

    def test_different_actual_and_commitment_block_update(self):
        _write_sap_salary_cache(
            self.data_dir,
            self.fund.fund_number,
            business_partner="Person, Test",
            amount="1100.00",
            additional_commitment_amount="1200.00",
        )

        with self.settings(SAP_ENABLED=True, SAP_DATA_DIR=self.data_dir):
            response = self.client.get(reverse("warnings"))

        self.assertContains(response, "SAP-Ist 1100.00 EUR")
        self.assertContains(response, "SAP-Obligo 1200.00 EUR")
        self.assertContains(response, "unterschiedliche SAP-Ist- und SAP-Obligo-Werte")
        self.assertNotContains(response, "in Planung übernehmen")

    def test_explicit_sap_partner_name_enables_mapping(self):
        self.staff_member.first_name = "Different"
        self.staff_member.last_name = "Planning Name"
        self.staff_member.sap_business_partner = "Person, Test"
        self.staff_member.save(
            update_fields=["first_name", "last_name", "sap_business_partner"]
        )

        with self.settings(SAP_ENABLED=True, SAP_DATA_DIR=self.data_dir):
            response = self.client.get(reverse("warnings"))

        self.assertContains(
            response,
            "SAP-Gehaltsabweichungen bei Different Planning Name",
        )
        self.assertNotContains(response, "SAP-Geschäftspartner nicht zugeordnet")

    def test_unmatched_sap_partner_has_mapping_warning_without_update(self):
        self.staff_member.first_name = "Different"
        self.staff_member.last_name = "Planning Name"
        self.staff_member.save(update_fields=["first_name", "last_name"])

        with self.settings(SAP_ENABLED=True, SAP_DATA_DIR=self.data_dir):
            response = self.client.get(reverse("warnings"))

        self.assertContains(response, "SAP-Geschäftspartner nicht zugeordnet")
        self.assertNotContains(response, "Alle SAP-Werte in Planung übernehmen")

    def test_partial_employment_month_is_applied_as_exact_amount(self):
        self.employment.start_date = date(2026, 1, 15)
        self.employment.save(update_fields=["start_date"])
        self.employment.employmentsalaries_set.update(
            start_date=date(2026, 1, 15)
        )

        with self.settings(SAP_ENABLED=True, SAP_DATA_DIR=self.data_dir):
            response = self.client.get(reverse("warnings"))

        self.assertContains(response, "Planung 548.39 EUR")
        self.assertContains(response, "Alle SAP-Werte in Planung übernehmen")

        url = reverse("apply_sap_salary", args=[self.staff_member.id, 2026, 1])
        with self.settings(SAP_ENABLED=True, SAP_DATA_DIR=self.data_dir):
            update_response = self.client.post(url, follow=True)

        salaries = list(
            self.employment.employmentsalaries_set.order_by("start_date").values_list(
                "start_date",
                "end_date",
                "salary",
                "is_exact_amount",
            )
        )
        self.assertEqual(
            salaries,
            [
                (
                    date(2026, 1, 15),
                    date(2026, 1, 31),
                    Decimal("1100.00"),
                    True,
                ),
                (
                    date(2026, 2, 1),
                    date(2026, 3, 31),
                    Decimal("1000.00"),
                    False,
                ),
            ],
        )
        self.assertNotContains(
            update_response,
            "SAP-Gehaltsabweichungen bei Test Person",
        )

    def test_partial_commitment_period_is_applied_as_exact_amount(self):
        _write_sap_salary_cache(
            self.data_dir,
            self.fund.fund_number,
            business_partner="Person, Test",
            amount="550.00",
            transaction_type="commitment",
            commitment_start_day=15,
        )

        with self.settings(SAP_ENABLED=True, SAP_DATA_DIR=self.data_dir):
            warning_response = self.client.get(reverse("warnings"))

        self.assertContains(warning_response, "Planung 548.39 EUR")
        self.assertContains(warning_response, "SAP-Obligo 550.00 EUR")

        url = reverse("apply_sap_salary", args=[self.staff_member.id, 2026, 1])
        with self.settings(SAP_ENABLED=True, SAP_DATA_DIR=self.data_dir):
            self.client.post(url)

        salaries = list(
            self.employment.employmentsalaries_set.order_by("start_date").values_list(
                "start_date",
                "end_date",
                "salary",
                "is_exact_amount",
            )
        )
        self.assertEqual(
            salaries,
            [
                (
                    date(2026, 1, 1),
                    date(2026, 1, 14),
                    Decimal("1000.00"),
                    False,
                ),
                (
                    date(2026, 1, 15),
                    date(2026, 1, 31),
                    Decimal("550.00"),
                    True,
                ),
                (
                    date(2026, 2, 1),
                    date(2026, 3, 31),
                    Decimal("1000.00"),
                    False,
                ),
            ],
        )

    def test_fund_allocation_resolves_employment_change_within_month(self):
        self.employment.end_date = date(2026, 2, 15)
        self.employment.save(update_fields=["end_date"])
        self.employment.employmentsalaries_set.all().delete()
        EmploymentSalaries.objects.create(
            employment=self.employment,
            salary=Decimal("550.00"),
            is_exact_amount=True,
            start_date=date(2026, 2, 1),
            end_date=date(2026, 2, 15),
        )
        budget_item = StaffBudgetItem.objects.create(
            project=self.project,
            title="Old employment",
            amount=Decimal("1000.00"),
        )
        StaffFundingAllocation.objects.create(
            employment=self.employment,
            budget_item=budget_item,
            percentage=Decimal("100.00"),
            start_date=date(2026, 2, 1),
            end_date=date(2026, 2, 15),
        )
        new_employment = Employment.objects.create(
            staff_member=self.staff_member,
            start_date=date(2026, 2, 16),
            end_date=date(2026, 3, 31),
            percentage=Decimal("50.00"),
        )
        EmploymentSalaries.objects.create(
            employment=new_employment,
            salary=Decimal("2000.00"),
            start_date=date(2026, 2, 16),
            end_date=date(2026, 3, 31),
        )
        StaffFundingAllocation.objects.create(
            employment=new_employment,
            is_universal=True,
            percentage=Decimal("50.00"),
            start_date=date(2026, 2, 16),
            end_date=date(2026, 3, 31),
        )
        _write_sap_salary_cache(
            self.data_dir,
            self.fund.fund_number,
            business_partner="Person, Test",
            amount="550.00",
            month_name="Februar",
        )

        comparison = build_salary_comparisons(self.data_dir).comparisons[0]

        self.assertEqual(comparison.employment, self.employment)
        self.assertEqual(comparison.planned, Decimal("550.00"))
        self.assertIsNone(comparison.blocking_reason)
        with self.settings(SAP_ENABLED=True, SAP_DATA_DIR=self.data_dir):
            response = self.client.get(reverse("warnings"))
        self.assertNotContains(
            response,
            "SAP-Gehaltsabweichungen bei Test Person",
        )

    def test_fund_allocation_keeps_genuinely_ambiguous_employments_blocked(self):
        self.employment.end_date = date(2026, 2, 15)
        self.employment.save(update_fields=["end_date"])
        budget_item = StaffBudgetItem.objects.create(
            project=self.project,
            title="Shared project",
            amount=Decimal("5000.00"),
        )
        StaffFundingAllocation.objects.create(
            employment=self.employment,
            budget_item=budget_item,
            percentage=Decimal("100.00"),
            start_date=date(2026, 1, 1),
            end_date=date(2026, 2, 15),
        )
        new_employment = Employment.objects.create(
            staff_member=self.staff_member,
            start_date=date(2026, 2, 16),
            end_date=date(2026, 3, 31),
            percentage=Decimal("100.00"),
        )
        EmploymentSalaries.objects.create(
            employment=new_employment,
            salary=Decimal("1200.00"),
            start_date=date(2026, 2, 16),
            end_date=date(2026, 3, 31),
        )
        StaffFundingAllocation.objects.create(
            employment=new_employment,
            budget_item=budget_item,
            percentage=Decimal("100.00"),
            start_date=date(2026, 2, 16),
            end_date=date(2026, 3, 31),
        )
        _write_sap_salary_cache(
            self.data_dir,
            self.fund.fund_number,
            business_partner="Person, Test",
            amount="1100.00",
            month_name="Februar",
        )

        comparison = build_salary_comparisons(self.data_dir).comparisons[0]

        self.assertIsNone(comparison.employment)
        self.assertEqual(
            comparison.blocking_reason,
            "Für diesen Monat wurden mehrere passende Anstellungen gefunden.",
        )

    def test_multiple_months_are_grouped_and_applied_together(self):
        _write_sap_salary_cache(
            self.data_dir,
            self.fund.fund_number,
            business_partner="Person, Test",
            amount="1100.00",
            additional_salary_rows=[
                ("Februar", "1100.00", "commitment"),
                ("März", "1200.00", "commitment"),
            ],
        )

        with self.settings(SAP_ENABLED=True, SAP_DATA_DIR=self.data_dir):
            warning_response = self.client.get(reverse("warnings"))

        self.assertContains(
            warning_response,
            "SAP-Gehaltsabweichungen bei Test Person",
            count=1,
        )
        self.assertContains(warning_response, "Januar 2026: SAP-Ist 1100.00 EUR")
        self.assertContains(warning_response, "Februar 2026: SAP-Obligo 1100.00 EUR")
        self.assertContains(warning_response, "März 2026: SAP-Obligo 1200.00 EUR")
        self.assertContains(
            warning_response,
            "Gesamt Januar 2026 bis März 2026:",
        )
        self.assertContains(warning_response, "SAP 3400.00 EUR")
        self.assertContains(warning_response, "Planung 3000.00 EUR")
        self.assertContains(warning_response, "Gesamtabweichung 400.00 EUR")
        self.assertContains(
            warning_response,
            "Alle SAP-Werte in Planung übernehmen",
            count=1,
        )

        url = reverse("apply_all_sap_salaries", args=[self.staff_member.id])
        with self.settings(SAP_ENABLED=True, SAP_DATA_DIR=self.data_dir):
            update_response = self.client.post(url, follow=True)

        salaries = list(
            self.employment.employmentsalaries_set.order_by("start_date").values_list(
                "start_date",
                "end_date",
                "salary",
            )
        )
        self.assertEqual(
            salaries,
            [
                (date(2026, 1, 1), date(2026, 2, 28), Decimal("1100.00")),
                (date(2026, 3, 1), date(2026, 3, 31), Decimal("1200.00")),
            ],
        )
        self.assertContains(update_response, "3 SAP-Gehaltsmonat(e)")
        self.assertNotContains(
            update_response,
            "SAP-Gehaltsabweichungen bei Test Person",
        )

    def test_bulk_update_rolls_back_all_months_if_one_update_fails(self):
        _write_sap_salary_cache(
            self.data_dir,
            self.fund.fund_number,
            business_partner="Person, Test",
            amount="1100.00",
            additional_salary_rows=[
                ("Februar", "1200.00", "commitment"),
            ],
        )
        call_count = 0

        def apply_then_fail(comparison):
            nonlocal call_count
            call_count += 1
            apply_salary_value(comparison)
            if call_count == 2:
                raise ValueError("Simulierter Fehler")

        url = reverse("apply_all_sap_salaries", args=[self.staff_member.id])
        with self.settings(SAP_ENABLED=True, SAP_DATA_DIR=self.data_dir):
            with patch(
                "controlling.views.apply_salary_comparison",
                side_effect=apply_then_fail,
            ):
                response = self.client.post(url, follow=True)

        salaries = list(
            self.employment.employmentsalaries_set.values_list(
                "start_date",
                "end_date",
                "salary",
            )
        )
        self.assertEqual(
            salaries,
            [(date(2026, 1, 1), date(2026, 3, 31), Decimal("1000.00"))],
        )
        self.assertContains(response, "Simulierter Fehler")


def _write_sap_salary_cache(
    data_dir,
    fund_number,
    business_partner,
    amount,
    month_name="Januar",
    transaction_type="actual",
    additional_commitment_amount=None,
    additional_salary_rows=None,
    commitment_start_day=1,
    commitment_end_day=None,
):
    processed_dir = Path(data_dir) / "processed"
    processed_dir.mkdir(parents=True, exist_ok=True)
    month_number = {
        "Januar": 1,
        "Februar": 2,
        "März": 3,
        "April": 4,
        "Mai": 5,
        "Juni": 6,
        "Juli": 7,
        "August": 8,
        "September": 9,
        "Oktober": 10,
        "November": 11,
        "Dezember": 12,
    }[month_name]
    commitment_end_day = commitment_end_day or monthrange(2026, month_number)[1]
    commitment_position = (
        f"Mittelbindung von {commitment_start_day:02d}.{month_number:02d}.2026 bis "
        f"{commitment_end_day:02d}.{month_number:02d}.2026"
    )
    transactions = [
        {
            "type": transaction_type,
            "business_partner": business_partner,
            "position": (
                f"Gehalt {month_name} 2026"
                if transaction_type == "actual"
                else commitment_position
            ),
            "amount": amount,
            "booking_date": "2026-01-31",
        }
    ]
    if additional_commitment_amount is not None:
        transactions.append(
            {
                "type": "commitment",
                "business_partner": business_partner,
                "position": commitment_position,
                "amount": additional_commitment_amount,
                "booking_date": "2026-01-31",
            }
        )
    for extra_month_name, extra_amount, extra_type in additional_salary_rows or []:
        extra_month_number = {
            "Januar": 1,
            "Februar": 2,
            "März": 3,
            "April": 4,
            "Mai": 5,
            "Juni": 6,
            "Juli": 7,
            "August": 8,
            "September": 9,
            "Oktober": 10,
            "November": 11,
            "Dezember": 12,
        }[extra_month_name]
        extra_position = (
            f"Gehalt {extra_month_name} 2026"
            if extra_type == "actual"
            else (
                f"Mittelbindung von 01.{extra_month_number:02d}.2026 bis "
                f"{monthrange(2026, extra_month_number)[1]:02d}."
                f"{extra_month_number:02d}.2026"
            )
        )
        transactions.append(
            {
                "type": extra_type,
                "business_partner": business_partner,
                "position": extra_position,
                "amount": extra_amount,
                "booking_date": "2026-01-31",
            }
        )
    actual_total = Decimal(amount) if transaction_type == "actual" else Decimal("0")
    commitments_total = (
        Decimal(amount) if transaction_type == "commitment" else Decimal("0")
    )
    if additional_commitment_amount is not None:
        commitments_total += Decimal(additional_commitment_amount)
    for _, extra_amount, extra_type in additional_salary_rows or []:
        if extra_type == "actual":
            actual_total += Decimal(extra_amount)
        else:
            commitments_total += Decimal(extra_amount)
    combined_total = actual_total + commitments_total
    payload = {
        "schema_version": 1,
        "year": 2026,
        "generated_at": "2026-07-21T10:00:00+00:00",
        "funds": {
            fund_number: {
                "fund_number": fund_number,
                "has_budget": True,
                "budget": "12000.00",
                "actual_total": str(actual_total),
                "commitments_total": str(commitments_total),
                "combined_total": str(combined_total),
                "remaining": str(Decimal("12000.00") - combined_total),
                "transactions": transactions,
            }
        },
    }
    (processed_dir / "2026.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )
