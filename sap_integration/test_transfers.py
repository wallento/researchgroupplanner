from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse

from projects.models import Project, SAPFund, StaffBudgetItem
from projects.utils import calculate_salary_for_allocation
from sap_integration.crosscheck import Status
from sap_integration.models import SAPImport, SAPPosition, SAPPositionKind
from sap_integration.transform import apply_monthly_salaries
from sap_integration.test_gm_export import STATIC_STORAGE, ReconciliationTestBase
from staffing.models import Employment, StaffFundingAllocation, StaffMember
from staffing.utils import get_salaries_by_month, get_sap_actuals_by_month


def _transfer(sap_import, reference="3125000668", amount="968.05"):
    return SAPPosition.objects.create(
        sap_import=sap_import,
        reference=reference,
        kind=SAPPositionKind.STAFF,
        cost_type="PERSONALKOSTEN",
        title="UMB_Muster_JSZ 2025",
        actual=Decimal(amount),
        monthly_actuals={"2025-12": amount},
        bookings=[{
            "date": "2025-12-11", "value_type": "66", "commitment": False, "cost_type": "PERSONALKOSTEN",
            "document": reference, "partner": "", "text": "UMB_Muster_JSZ 2025", "amount": amount,
        }],
    )


class TransferPositionTests(ReconciliationTestBase):
    def test_transfer_is_information_only(self):
        _transfer(self.sap_import)

        check = self.check("3125000668")

        self.assertEqual(check.status, Status.INFO)
        self.assertEqual(check.allocations, [])

    def test_regular_staff_position_is_no_transfer(self):
        self.assertFalse(self.check("4000100").position.is_transfer)


class SAPActualsByMonthTests(ReconciliationTestBase):
    def test_only_months_covered_by_referencing_allocations(self):
        member = StaffMember.objects.create(first_name="Erika", last_name="Muster-Frau")
        first = Employment.objects.create(
            staff_member=member, start_date=date(2026, 1, 1), end_date=date(2026, 1, 31), percentage=Decimal("50"),
        )
        second = Employment.objects.create(
            staff_member=member, start_date=date(2026, 2, 1), end_date=date(2026, 6, 30), percentage=Decimal("50"),
        )
        for employment in (first, second):
            StaffFundingAllocation.objects.create(
                employment=employment, budget_item=self.staff_item, percentage=Decimal("50"),
                start_date=employment.start_date, end_date=employment.end_date, sap_reference="4000100",
            )

        first_actuals = get_sap_actuals_by_month(first.stafffundingallocation_set.all())
        second_actuals = get_sap_actuals_by_month(second.stafffundingallocation_set.all())

        self.assertEqual(sorted(first_actuals), ["2026-01"])
        self.assertEqual(sorted(second_actuals), ["2026-02"])

    def test_uses_latest_import_per_fund(self):
        member = StaffMember.objects.create(first_name="Erika", last_name="Muster-Frau")
        employment = Employment.objects.create(
            staff_member=member, start_date=date(2026, 1, 1), end_date=date(2026, 6, 30), percentage=Decimal("50"),
        )
        StaffFundingAllocation.objects.create(
            employment=employment, budget_item=self.staff_item, percentage=Decimal("50"),
            start_date=employment.start_date, end_date=employment.end_date, sap_reference="4000100",
        )
        other_fund = SAPFund.objects.create(fund_number="OTHER", project=self.project)
        other_import = SAPImport.objects.create(fund=other_fund, file_name="other.xlsx")
        SAPPosition.objects.create(
            sap_import=other_import, reference="4000100", kind=SAPPositionKind.STAFF, cost_type="7221",
            monthly_actuals={"2026-01": "-500.00"},
        )

        actuals = get_sap_actuals_by_month(employment.stafffundingallocation_set.all())

        self.assertEqual(sum(entry["amount"] for entry in actuals["2026-01"]), Decimal("2500.00"))
        self.assertEqual(len(actuals["2026-01"]), 2)


@override_settings(SAP_GM_IMPORT_ENABLED=True, STORAGES=STATIC_STORAGE)
class DeleteOrphanTests(ReconciliationTestBase):
    def setUp(self):
        super().setUp()
        user = get_user_model().objects.create_user("staff", password="x", is_staff=True)
        self.client.force_login(user)
        member = StaffMember.objects.create(first_name="Max", last_name="Mustermann")
        employment = Employment.objects.create(
            staff_member=member, start_date=date(2026, 1, 1), end_date=date(2026, 6, 30), percentage=Decimal("50"),
        )
        self.orphan = StaffFundingAllocation.objects.create(
            employment=employment, budget_item=self.staff_item, percentage=Decimal("50"),
            start_date=date(2026, 1, 1), end_date=date(2026, 6, 30), sap_reference="4999999",
        )
        self.url = reverse("sap_integration:delete_orphan", args=[self.fund.id])

    def test_deletes_orphan_allocation(self):
        response = self.client.post(self.url, {"kind": "allocation", "id": self.orphan.id})

        self.assertRedirects(response, reverse("sap_integration:reconciliation", args=[self.fund.id]))
        self.assertFalse(StaffFundingAllocation.objects.filter(id=self.orphan.id).exists())

    def test_keeps_allocation_linked_to_sap_position(self):
        self.orphan.sap_reference = "4000100"
        self.orphan.save()

        self.client.post(self.url, {"kind": "allocation", "id": self.orphan.id})

        self.assertTrue(StaffFundingAllocation.objects.filter(id=self.orphan.id).exists())

    def test_reconciliation_shows_delete_button(self):
        response = self.client.get(reverse("sap_integration:reconciliation", args=[self.fund.id]))

        self.assertContains(response, self.url)


class ApplySalariesSplitMonthTests(ReconciliationTestBase):
    def test_month_split_over_two_allocations_keeps_monthly_rate(self):
        member = StaffMember.objects.create(first_name="Erika", last_name="Muster-Frau")
        employment = Employment.objects.create(
            staff_member=member, start_date=date(2026, 1, 1), end_date=date(2026, 6, 30), percentage=Decimal("50"),
        )
        first = StaffFundingAllocation.objects.create(
            employment=employment, budget_item=self.staff_item, percentage=Decimal("50"),
            start_date=date(2026, 1, 1), end_date=date(2026, 2, 14), sap_reference="4000100",
        )
        second = StaffFundingAllocation.objects.create(
            employment=employment, budget_item=self.staff_item, percentage=Decimal("50"),
            start_date=date(2026, 2, 15), end_date=date(2026, 6, 30), sap_reference="4000100",
        )
        costs = {"2026-01": Decimal("3000.00"), "2026-02": Decimal("3000.00")}

        for allocation in (first, second):
            apply_monthly_salaries(allocation, costs)

        self.assertEqual(get_salaries_by_month(employment)["2026-02"], Decimal("3000.00"))
        planned = sum(
            (calculate_salary_for_allocation(a).months.get("2026-02", 0) for a in (first, second)),
            Decimal("0"),
        )
        self.assertEqual(planned, Decimal("3000.00"))

    def test_partial_first_month_is_scaled_to_full_rate(self):
        member = StaffMember.objects.create(first_name="Erika", last_name="Muster-Frau")
        employment = Employment.objects.create(
            staff_member=member, start_date=date(2026, 1, 1), end_date=date(2026, 6, 30), percentage=Decimal("100"),
        )
        allocation = StaffFundingAllocation.objects.create(
            employment=employment, budget_item=self.staff_item, percentage=Decimal("50"),
            start_date=date(2026, 2, 15), end_date=date(2026, 6, 30), sap_reference="4000100",
        )

        apply_monthly_salaries(allocation, {"2026-02": Decimal("700.00")})

        self.assertEqual(get_salaries_by_month(employment)["2026-02"], Decimal("2800.00"))


class ApplySalariesMultiFundMonthTests(ReconciliationTestBase):
    def test_month_paid_from_two_funds_uses_combined_amount(self):
        member = StaffMember.objects.create(first_name="Erika", last_name="Muster-Frau")
        employment = Employment.objects.create(
            staff_member=member, start_date=date(2026, 1, 1), end_date=date(2026, 6, 30), percentage=Decimal("100"),
        )
        other_item = StaffBudgetItem.objects.create(
            project=Project.objects.create(
                acronym="OTHER", start_date=date(2025, 1, 1), end_date=date(2027, 12, 31), budget_total=Decimal("1000"),
            ),
            title="Personal", amount=Decimal("1000"),
        )
        first = StaffFundingAllocation.objects.create(
            employment=employment, budget_item=self.staff_item, percentage=Decimal("100"),
            start_date=date(2026, 1, 1), end_date=date(2026, 1, 27), sap_reference="4000100",
        )
        StaffFundingAllocation.objects.create(
            employment=employment, budget_item=other_item, percentage=Decimal("100"),
            start_date=date(2026, 1, 28), end_date=date(2026, 6, 30), sap_reference="4000100",
        )
        other_fund = SAPFund.objects.create(fund_number="OTHER", project=other_item.project)
        other_import = SAPImport.objects.create(fund=other_fund, file_name="other.xlsx")
        SAPPosition.objects.create(
            sap_import=other_import, reference="4000100", kind=SAPPositionKind.STAFF, cost_type="7221",
            monthly_actuals={"2026-01": "1000.00"},
        )

        apply_monthly_salaries(first, {"2026-01": Decimal("3000.00")})

        # 3000 from the imported sample fund plus 1000 from the other fund.
        self.assertEqual(get_salaries_by_month(employment)["2026-01"], Decimal("4000.00"))


class StaffPlanningContinuityTests(ReconciliationTestBase):
    def position(self, periods):
        return SAPPosition.objects.create(
            sap_import=self.sap_import, reference="4777777", kind=SAPPositionKind.STAFF, cost_type="7221",
            person_name="Kontinuierlich, Karl", contract_periods=periods,
        )

    def test_back_to_back_periods_become_one_employment(self):
        from sap_integration.transform import create_staff_planning

        position = self.position([["2026-01-01", "2026-03-31"], ["2026-04-01", "2026-06-30"]])

        member, allocations = create_staff_planning(
            position, budget_item=self.staff_item, category="researcher", percentage=Decimal("100"),
            first_name="Karl", last_name="Kontinuierlich", create_salaries=False,
        )

        self.assertEqual(member.employment_set.count(), 1)
        self.assertEqual(member.employment_set.get().end_date, date(2026, 6, 30))
        self.assertEqual(len(allocations), 2)

    def test_preceding_employment_is_extended(self):
        from sap_integration.transform import create_staff_planning

        member = StaffMember.objects.create(first_name="Karl", last_name="Kontinuierlich")
        employment = Employment.objects.create(staff_member=member, start_date=date(2025, 7, 1),
                                               end_date=date(2025, 12, 31), percentage=Decimal("100"))
        position = self.position([["2026-01-01", "2026-06-30"]])

        create_staff_planning(
            position, budget_item=self.staff_item, category="researcher", percentage=Decimal("100"),
            staff_member=member, employment=employment, create_salaries=False,
        )

        employment.refresh_from_db()
        self.assertEqual(employment.end_date, date(2026, 6, 30))
        self.assertEqual(member.employment_set.count(), 1)

    def test_taking_over_again_does_not_duplicate_allocations(self):
        from sap_integration.transform import create_staff_planning

        member = StaffMember.objects.create(first_name="Karl", last_name="Kontinuierlich")
        employment = Employment.objects.create(staff_member=member, start_date=date(2026, 1, 1),
                                               end_date=date(2026, 6, 30), percentage=Decimal("100"))
        position = self.position([["2026-01-01", "2026-06-30"]])
        kwargs = dict(budget_item=self.staff_item, category="researcher", percentage=Decimal("100"),
                      staff_member=member, employment=employment, create_salaries=False)

        create_staff_planning(position, **kwargs)
        _, allocations = create_staff_planning(position, **kwargs)

        self.assertEqual(employment.stafffundingallocation_set.count(), 1)
        self.assertEqual(allocations[0].sap_reference, "4777777")


class StaffObligoTests(ReconciliationTestBase):
    def check_for(self, periods, actuals, commitment, planned=None, findings=None):
        from sap_integration.crosscheck import PositionCheck

        position = SAPPosition(
            sap_import=self.sap_import, reference="4888888", kind=SAPPositionKind.STAFF, cost_type="7221",
            contract_periods=periods, monthly_actuals=actuals, commitment=Decimal(commitment),
        )
        return PositionCheck(position=position, planned_months=planned or {}, findings=list(findings or []))

    def test_obligo_after_contract_end_is_reported(self):
        from sap_integration.crosscheck import _compare_obligo

        check = self.check_for([["2026-01-01", "2026-02-28"]], {"2026-01": "3000", "2026-02": "3000"}, "3000")
        _compare_obligo(check, [])

        self.assertIn("Obligo nach Vertragsende nicht ausgebucht: 3,000.00 €", check.findings[0])
        self.assertEqual(check.obligo_issues[0]["amount"], Decimal("3000"))

    def test_unplanned_contract_months_are_estimated(self):
        from sap_integration.crosscheck import _compare_obligo

        check = self.check_for(
            [["2026-01-01", "2026-06-30"]], {"2026-01": "3000", "2026-02": "3000"}, "12000",
            findings=["SAP-Vertrag nicht geplant: 2026-05 – 2026-06"],
        )
        _compare_obligo(check, ["2026-05", "2026-06"])

        self.assertEqual(len(check.findings), 1)
        self.assertIn("Vertrag in SAP reserviert, nicht geplant: 2026-05 – 2026-06 (≈ 6,000.00 €", check.findings[0])

    def test_difference_to_planned_remaining_cost(self):
        from sap_integration.crosscheck import _compare_obligo

        planned = {"2026-03": Decimal("1000"), "2026-04": Decimal("1000")}
        check = self.check_for([["2026-01-01", "2026-04-30"]], {"2026-01": "3000", "2026-02": "3000"}, "6000", planned)
        _compare_obligo(check, [])
        self.assertIn("weicht von den geplanten Restkosten", check.findings[0])

        close = self.check_for([["2026-01-01", "2026-04-30"]], {"2026-01": "3000", "2026-02": "3000"}, "2500", planned)
        _compare_obligo(close, [])
        self.assertEqual(close.findings, [])

    def test_travel_on_personnel_cost_type_is_travel(self):
        from sap_integration.gm_export import _classify

        rows = [{"E/A-Art": "PERSONALKOSTEN", "Finanzposition": ""}]
        self.assertEqual(_classify(rows, "PERSONALKOSTEN", ["RKE Seoul 13.-21.11.25", ""]), SAPPositionKind.TRAVEL)
        self.assertEqual(_classify(rows, "PERSONALKOSTEN", ["AV, Muster, Erika", ""]), SAPPositionKind.STAFF)


@override_settings(SAP_ENABLED=False, SAP_GM_IMPORT_ENABLED=True, STORAGES=STATIC_STORAGE)
class ObligoWarningTests(ReconciliationTestBase):
    def test_missing_staff_position_with_obligo_is_warned(self):
        self.client.force_login(get_user_model().objects.create_user("user", password="x"))

        warnings = self.client.get(reverse("warnings")).context["warnings_list"]

        entry = next(w for w in warnings if w["title"].startswith("SAP-Obligo nicht in Planung"))
        self.assertIn("Position fehlt in der Planung", entry["details"][0])
        self.assertTrue(entry["correction_links"][0]["url"].startswith("/"))


class CrossFundShiftTests(ReconciliationTestBase):
    def test_shift_between_funds_is_not_a_salary_mismatch(self):
        from projects.models import Project, SAPFund, StaffBudgetItem
        from sap_integration.crosscheck import build_reconciliation
        from staffing.models import EmploymentSalaries

        member = StaffMember.objects.create(first_name="Erika", last_name="Muster-Frau")
        employment = Employment.objects.create(staff_member=member, start_date=date(2026, 1, 1),
                                               end_date=date(2026, 2, 28), percentage=Decimal("50"))
        EmploymentSalaries.objects.create(employment=employment, salary=Decimal("3000"),
                                          start_date=date(2026, 1, 1), end_date=date(2026, 2, 28))
        StaffFundingAllocation.objects.create(employment=employment, budget_item=self.staff_item, percentage=Decimal("50"),
                                              start_date=date(2026, 1, 1), end_date=date(2026, 2, 28), sap_reference="4000100")
        # Another fund books -100 € for January (moved here, +100 € in the sample fund would be needed).
        other_project = Project.objects.create(acronym="OTHER", start_date=date(2025, 1, 1), end_date=date(2027, 12, 31),
                                               budget_total=Decimal("1000"))
        other_fund = SAPFund.objects.create(fund_number="OTHER", project=other_project)
        other_import = SAPImport.objects.create(fund=other_fund, file_name="other.xlsx")
        SAPPosition.objects.create(sap_import=other_import, reference="4000100", kind=SAPPositionKind.STAFF,
                                   cost_type="7221", monthly_actuals={"2026-01": "-100.00"})
        position = self.check("4000100").position
        position.monthly_actuals = {"2026-01": "3100.00", "2026-02": "3000.00"}
        position.save()

        check = next(c for c in build_reconciliation(self.fund).checks if c.position.reference == "4000100")

        self.assertEqual(check.salary_mismatch_months, [])
        self.assertTrue(any("Verschiebung zwischen Fonds" in note for note in check.notes))


@override_settings(SAP_ENABLED=False, SAP_GM_IMPORT_ENABLED=True, STORAGES=STATIC_STORAGE)
class StaffReservationTests(ReconciliationTestBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(get_user_model().objects.create_user("user", password="x"))
        self.member = StaffMember.objects.create(first_name="Erika", last_name="Muster-Frau")
        employment = Employment.objects.create(staff_member=self.member, start_date=date(2026, 1, 1),
                                               end_date=date(2026, 3, 31), percentage=Decimal("50"))
        self.allocation = StaffFundingAllocation.objects.create(
            employment=employment, budget_item=self.staff_item, percentage=Decimal("50"), start_date=date(2026, 1, 1),
        )

    def test_reservation_matched_by_name_can_be_linked(self):
        from staffing.utils import staff_reservations

        reservation = next(r for r in staff_reservations(self.member) if r["position"].reference == "4000100")
        self.assertFalse(reservation["linked"])
        self.assertEqual(reservation["link_candidates"], [self.allocation])
        details = self.client.get(reverse("staffing:details", args=[self.member.id]))
        self.assertContains(details, "über Namen zugeordnet")
        self.assertContains(details, '"sap_contract": true')

        self.client.post(reverse("staffing:link_reservation", args=[self.member.id, reservation["position"].id]))

        self.allocation.refresh_from_db()
        self.assertEqual(self.allocation.sap_reference, "4000100")
        reservation = next(r for r in staff_reservations(self.member) if r["position"].reference == "4000100")
        self.assertTrue(reservation["linked"])

    def test_other_people_do_not_see_the_reservation(self):
        from staffing.utils import staff_reservations

        other = StaffMember.objects.create(first_name="Max", last_name="Andere")

        self.assertEqual(staff_reservations(other), [])


class TransactionDateTests(ReconciliationTestBase):
    def test_transaction_uses_actual_booking_date(self):
        from sap_integration.transform import create_transaction, update_transaction_amount

        position = self.check("8000001").position  # travel: reservation 2026-01-31, payment 2026-02-10

        planner_transaction = create_transaction(position, self.travel_item)
        self.assertEqual(planner_transaction.date, date(2026, 2, 10))

        planner_transaction.date = date(2026, 1, 1)
        planner_transaction.save()
        update_transaction_amount(position, planner_transaction)
        planner_transaction.refresh_from_db()
        self.assertEqual(planner_transaction.date, date(2026, 2, 10))

    def test_without_payment_falls_back_to_first_booking(self):
        position = self.check("4000300").position  # test reservation without amount

        self.assertIsNone(position.actual_date)


class YearlyComparisonTests(ReconciliationTestBase):
    def test_sap_actuals_by_year_without_income(self):
        from sap_integration.crosscheck import sap_actuals_by_year

        # Staff 2 × 3.000 €, travel 480 €, purchase 100 €; the income (Mittelabruf) is left out.
        self.assertEqual(sap_actuals_by_year(), {"2026": Decimal("6580.00")})

    def test_planned_expenses_by_year(self):
        from projects.models import OtherBudgetItemTransaction, OverheadBudgetItem
        from projects.utils import planned_expenses_by_year
        from staffing.models import EmploymentSalaries

        member = StaffMember.objects.create(first_name="Erika", last_name="Muster-Frau")
        employment = Employment.objects.create(staff_member=member, start_date=date(2026, 11, 1),
                                               end_date=date(2027, 2, 28), percentage=Decimal("100"))
        EmploymentSalaries.objects.create(employment=employment, salary=Decimal("1000"),
                                          start_date=date(2026, 11, 1), end_date=date(2027, 2, 28))
        StaffFundingAllocation.objects.create(employment=employment, budget_item=self.staff_item,
                                              percentage=Decimal("100"), start_date=date(2026, 11, 1))
        OtherBudgetItemTransaction.objects.create(budget_item=self.travel_item, date=date(2027, 3, 1), amount=Decimal("250"))
        OverheadBudgetItem.objects.create(project=self.project, amount=Decimal("1200"))

        planned = planned_expenses_by_year([self.project])

        # Staff 4 × 1.000 € (Nov 2026 – Feb 2027), travel 250 €, overhead 1.200 € spread over the duration.
        self.assertEqual(planned["2026"]["staff"], Decimal("2000.00"))
        self.assertEqual(planned["2027"]["staff"], Decimal("2000.00"))
        self.assertEqual(planned["2027"]["other"], Decimal("250.00"))
        self.assertAlmostEqual(sum(p["overhead"] for p in planned.values()), Decimal("1200.00"), delta=Decimal("0.10"))
