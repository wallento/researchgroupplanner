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
