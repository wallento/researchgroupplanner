from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from projects.models import Project, StaffBudgetItem
from staffing.models import Employment, Rebooking, StaffFundingAllocation, StaffMember
from staffing.utils import rebooking_cost_deltas


STATIC_STORAGE = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(STORAGES=STATIC_STORAGE)
class AllocationUiTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user("user", password="x"))
        old = Project.objects.create(acronym="OLD", start_date=date(2025, 1, 1), end_date=date(2027, 12, 31),
                                     budget_total=Decimal("100000"))
        new = Project.objects.create(acronym="NEW", start_date=date(2025, 1, 1), end_date=date(2027, 12, 31),
                                     budget_total=Decimal("100000"))
        self.old_item = StaffBudgetItem.objects.create(project=old, title="WiMi", amount=Decimal("50000"))
        self.new_item = StaffBudgetItem.objects.create(project=new, title="WiMi", amount=Decimal("50000"))
        self.member = StaffMember.objects.create(first_name="Erika", last_name="Muster")
        self.employment = Employment.objects.create(
            staff_member=self.member, start_date=date(2026, 1, 1), end_date=date(2026, 12, 31), percentage=Decimal("100"),
        )
        self.allocation = StaffFundingAllocation.objects.create(
            employment=self.employment, budget_item=self.old_item, percentage=Decimal("100"),
            start_date=date(2026, 1, 1), sap_reference="4001234",
        )

    def test_edit_allocation(self):
        response = self.client.post(reverse("staffing:edit_allocation", args=[self.allocation.id]), {
            "budget_item": self.new_item.id, "percentage": "50", "start_date": "2026-02-01", "end_date": "",
        })

        self.assertRedirects(response, reverse("staffing:details", args=[self.member.id]))
        self.allocation.refresh_from_db()
        self.assertEqual(
            (self.allocation.budget_item, self.allocation.percentage, self.allocation.start_date),
            (self.new_item, Decimal("50"), date(2026, 2, 1)),
        )

    def rebook(self, **overrides):
        data = {"budget_item": self.new_item.id, "percentage": "100", "start_date": "2026-07-01", "end_date": ""}
        data.update(overrides)
        return self.client.post(reverse("staffing:rebook_allocation", args=[self.allocation.id]), data)

    def test_rebook_creates_rebooking_and_keeps_allocation(self):
        self.rebook()

        self.allocation.refresh_from_db()
        self.assertEqual((self.allocation.budget_item, self.allocation.end_date), (self.old_item, None))
        rebooking = Rebooking.objects.get()
        self.assertEqual((rebooking.budget_item, rebooking.start_date, rebooking.end), (self.new_item, date(2026, 7, 1), date(2026, 12, 31)))

    def test_rebook_date_outside_allocation_rejected(self):
        response = self.rebook(start_date="2027-02-01")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Rebooking.objects.exists())

    def test_delete_rebooking_leaves_allocation_untouched(self):
        self.rebook()

        self.client.post(reverse("staffing:delete_rebooking", args=[Rebooking.objects.get().id]))

        self.assertFalse(Rebooking.objects.exists())
        self.assertEqual(StaffFundingAllocation.objects.count(), 1)
        self.allocation.refresh_from_db()
        self.assertEqual((self.allocation.budget_item, self.allocation.end_date), (self.old_item, None))

    def test_complete_splits_allocation_around_period(self):
        self.rebook(start_date="2026-04-01", end_date="2026-06-30", percentage="60")

        self.client.post(reverse("staffing:complete_rebooking", args=[Rebooking.objects.get().id]))

        self.assertFalse(Rebooking.objects.exists())
        parts = list(StaffFundingAllocation.objects.order_by("start_date", "budget_item__project__acronym"))
        # The remaining 40 % stay on the old budget during the rebooked period.
        self.assertEqual(
            [(p.budget_item, p.percentage, p.start_date, p.end_date, p.is_rebooking) for p in parts],
            [
                (self.old_item, Decimal("100"), date(2026, 1, 1), date(2026, 3, 31), False),
                (self.new_item, Decimal("60"), date(2026, 4, 1), date(2026, 6, 30), True),
                (self.old_item, Decimal("40"), date(2026, 4, 1), date(2026, 6, 30), False),
                (self.old_item, Decimal("100"), date(2026, 7, 1), None, False),
            ],
        )
        self.assertEqual({p.sap_reference for p in parts if p.budget_item == self.old_item}, {"4001234"})

    def test_complete_from_start_moves_allocation(self):
        self.rebook(start_date="2026-01-01")

        self.client.post(reverse("staffing:complete_rebooking", args=[Rebooking.objects.get().id]))

        self.allocation.refresh_from_db()
        self.assertEqual(StaffFundingAllocation.objects.count(), 1)
        self.assertEqual((self.allocation.budget_item, self.allocation.end_date), (self.new_item, None))
        self.assertTrue(self.allocation.is_rebooking)

    def test_cost_deltas_move_cost_between_budgets(self):
        from staffing.models import EmploymentSalaries

        EmploymentSalaries.objects.create(
            employment=self.employment, salary=Decimal("1000"), start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
        )
        self.rebook(percentage="50")

        deltas = rebooking_cost_deltas()

        # July to December, 50 % of 1.000 € per month move to the new budget.
        self.assertEqual(deltas[self.old_item.id], Decimal("-3000.00"))
        self.assertEqual(deltas[self.new_item.id], Decimal("3000.00"))

    def test_pages_show_rebooking(self):
        self.rebook()
        rebooking = Rebooking.objects.get()

        details = self.client.get(reverse("staffing:details", args=[self.member.id]))
        listing = self.client.get(reverse("staffing:rebookings"))

        self.assertContains(details, reverse("staffing:delete_rebooking", args=[rebooking.id]))
        self.assertContains(details, f'"id": "rebooking-{rebooking.id}"')
        self.assertContains(details, f'"id": "rebooking-out-{rebooking.id}"')
        self.assertContains(details, '"content": "\\u2192 NEW')
        self.assertContains(details, '"content": "\\u2190 OLD')
        self.assertEqual(list(listing.context["rebookings"]), [rebooking])

    def test_project_sums_show_rebooking_effect(self):
        from staffing.models import EmploymentSalaries

        EmploymentSalaries.objects.create(
            employment=self.employment, salary=Decimal("1000"), start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
        )
        self.rebook()

        old = self.client.get(reverse("projects:details", args=["OLD"])).context
        new = self.client.get(reverse("projects:details", args=["NEW"])).context

        self.assertEqual(old["budget_totals"]["rebooked_projected"], old["budget_totals"]["projected"] - Decimal("6000.00"))
        self.assertEqual(new["budget_totals"]["rebooked_projected"], new["budget_totals"]["projected"] + Decimal("6000.00"))
        self.assertEqual(new["rebooked_allocated"], new["allocated_sum"] + Decimal("6000.00"))

    def test_rebooking_person_month_deltas(self):
        from staffing.utils import rebooking_person_month_deltas

        self.rebook(percentage="50")

        deltas = rebooking_person_month_deltas()

        # July to December at 50 %: 3 PM move from the old to the new budget.
        self.assertEqual(deltas[self.old_item.id], Decimal("-3"))
        self.assertEqual(deltas[self.new_item.id], Decimal("3"))

    def two_half_rebookings(self):
        third = StaffBudgetItem.objects.create(
            project=Project.objects.create(acronym="THIRD", start_date=date(2025, 1, 1), end_date=date(2027, 12, 31),
                                           budget_total=Decimal("100000")),
            title="WiMi", amount=Decimal("50000"),
        )
        self.rebook(percentage="50")
        self.rebook(percentage="50", budget_item=third.id)
        return third

    def test_split_rebookings_move_each_share(self):
        from staffing.models import EmploymentSalaries
        from staffing.utils import rebooking_person_month_deltas

        EmploymentSalaries.objects.create(
            employment=self.employment, salary=Decimal("1000"), start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
        )
        third = self.two_half_rebookings()

        costs, pms = rebooking_cost_deltas(), rebooking_person_month_deltas()

        self.assertEqual(costs[self.old_item.id], Decimal("-6000.00"))
        self.assertEqual((costs[self.new_item.id], costs[third.id]), (Decimal("3000.00"), Decimal("3000.00")))
        self.assertEqual((pms[self.old_item.id], pms[self.new_item.id], pms[third.id]), (Decimal("-6"), Decimal("3"), Decimal("3")))

    def test_rebookings_cannot_exceed_allocation(self):
        self.two_half_rebookings()

        response = self.rebook(percentage="10")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Rebooking.objects.count(), 2)

    def test_completing_split_rebookings_one_after_another(self):
        third = self.two_half_rebookings()
        first, second = Rebooking.objects.order_by("pk")

        self.client.post(reverse("staffing:complete_rebooking", args=[first.id]))
        second.refresh_from_db()
        self.assertEqual((second.allocation.percentage, second.allocation.start_date), (Decimal("50"), date(2026, 7, 1)))
        self.client.post(reverse("staffing:complete_rebooking", args=[second.id]))

        parts = list(StaffFundingAllocation.objects.order_by("start_date", "budget_item__project__acronym"))
        self.assertEqual(
            [(p.budget_item, p.percentage, p.start_date, p.end_date) for p in parts],
            [
                (self.old_item, Decimal("100"), date(2026, 1, 1), date(2026, 6, 30)),
                (self.new_item, Decimal("50"), date(2026, 7, 1), None),
                (third, Decimal("50"), date(2026, 7, 1), None),
            ],
        )
        self.assertFalse(Rebooking.objects.exists())

    def test_project_page_marks_rebookings_both_ways(self):
        self.rebook(percentage="50")

        old = self.client.get(reverse("projects:details", args=["OLD"]))
        new = self.client.get(reverse("projects:details", args=["NEW"]))

        self.assertContains(old, "→ NEW · 50")
        self.assertContains(old, "allocation-rebook-out")
        self.assertContains(old, "⇄ Umbuchung (offen) → NEW")
        self.assertContains(new, "← OLD · 50")
        self.assertContains(new, "⇄ Umbuchung (offen) ← OLD")

    def test_rebookings_show_psp_elements(self):
        from projects.models import SAPFund

        SAPFund.objects.create(fund_number="PSP-OLD-1", project=self.old_item.project)
        SAPFund.objects.create(fund_number="PSP-NEW-1", project=self.new_item.project)
        self.rebook()

        listing = self.client.get(reverse("staffing:rebookings"))
        details = self.client.get(reverse("staffing:details", args=[self.member.id]))

        self.assertContains(listing, "PSP PSP-OLD-1")
        self.assertContains(listing, "PSP PSP-NEW-1")
        self.assertContains(details, "(PSP PSP-OLD-1 → PSP-NEW-1)")
