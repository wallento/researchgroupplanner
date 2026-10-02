from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from projects.models import Project, StaffBudgetItem
from projects.utils import calculate_salary_for_allocation
from staffing.models import Employment, EmploymentSalaries, SalaryCategory, StaffFundingAllocation, StaffMember
from staffing.utils import employment_merge_candidates, employment_merge_check, merge_employments


STATIC_STORAGE = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(STORAGES=STATIC_STORAGE, SAP_ENABLED=False, SAP_GM_IMPORT_ENABLED=False)
class MergeEmploymentTests(TestCase):
    def setUp(self):
        project = Project.objects.create(acronym="P", start_date=date(2025, 1, 1), end_date=date(2027, 12, 31),
                                         budget_total=Decimal("500000"))
        self.item_a = StaffBudgetItem.objects.create(project=project, title="A", amount=Decimal("100000"))
        self.item_b = StaffBudgetItem.objects.create(project=project, title="B", amount=Decimal("100000"))
        self.member = StaffMember.objects.create(first_name="Frans", last_name="Test")
        self.first = self.employment(date(2025, 9, 1), date(2026, 4, 30))
        self.second = self.employment(date(2026, 5, 1), date(2026, 12, 31))
        self.alloc_a = StaffFundingAllocation.objects.create(
            employment=self.first, budget_item=self.item_a, percentage=Decimal("100"), start_date=date(2025, 9, 1),
        )
        self.alloc_b = StaffFundingAllocation.objects.create(
            employment=self.second, budget_item=self.item_b, percentage=Decimal("100"), start_date=date(2026, 5, 1),
        )
        EmploymentSalaries.objects.create(employment=self.first, salary=Decimal("5000"),
                                          start_date=date(2025, 9, 1), end_date=date(2026, 4, 30))
        EmploymentSalaries.objects.create(employment=self.second, salary=Decimal("6000"),
                                          start_date=date(2026, 5, 1), end_date=date(2026, 12, 31))

    def employment(self, start, end, **kwargs):
        return Employment.objects.create(staff_member=self.member, start_date=start, end_date=end,
                                         percentage=Decimal("100"), **kwargs)

    def costs(self):
        return {a.budget_item_id: calculate_salary_for_allocation(a).salary_sum
                for a in StaffFundingAllocation.objects.all()}

    def test_merge_keeps_costs_and_moves_everything(self):
        before = self.costs()

        merged = merge_employments(self.first, self.second)

        self.assertEqual((merged.start_date, merged.end_date), (date(2025, 9, 1), date(2026, 12, 31)))
        self.assertFalse(Employment.objects.filter(id=self.second.id).exists())
        self.assertEqual(set(merged.stafffundingallocation_set.values_list("id", flat=True)), {self.alloc_a.id, self.alloc_b.id})
        self.assertEqual(merged.employmentsalaries_set.count(), 2)
        self.alloc_a.refresh_from_db()
        self.assertEqual(self.alloc_a.end_date, date(2026, 4, 30))
        self.assertEqual(self.costs(), before)

    def test_takes_level_data_from_one_side(self):
        e13 = SalaryCategory.objects.create(name="E13")
        self.second.salary_category, self.second.start_level, self.second.level_start_date = e13, 2, date(2025, 1, 1)
        self.second.save()

        merged = merge_employments(self.first, self.second)

        self.assertEqual((merged.salary_category, merged.start_level, merged.level_start_date), (e13, 2, date(2025, 1, 1)))

    def test_not_mergeable_reasons(self):
        self.second.status = "planned"
        self.assertIn("Status", employment_merge_check(self.first, self.second)[1])
        self.second.status = "contract"
        self.second.start_level, self.second.level_start_date = 1, date(2026, 5, 1)
        self.assertIn("Stufenbeginn", employment_merge_check(self.first, self.second)[1])
        gap = Employment(staff_member=self.member, start_date=date(2027, 1, 2), end_date=date(2027, 6, 30),
                         percentage=Decimal("100"))
        self.assertFalse(employment_merge_check(self.second, gap)[0])

    def test_candidates_only_back_to_back(self):
        self.employment(date(2027, 2, 1), date(2027, 6, 30))

        candidates = employment_merge_candidates(Employment.objects.all())

        self.assertEqual([(c[0].id, c[1].id, c[2]) for c in candidates], [(self.first.id, self.second.id, True)])

    def test_merge_view_and_warning(self):
        self.client.force_login(get_user_model().objects.create_user("user", password="x"))

        warnings = self.client.get(reverse("warnings")).context["warnings_list"]
        entry = next(w for w in warnings if w["title"] == f"Anstellungen zusammenführbar: {self.member}")
        self.assertEqual(entry["merge_employment_ids"], (self.first.id, self.second.id))
        self.assertContains(self.client.get(reverse("staffing:details", args=[self.member.id])), "Zusammenführen")

        response = self.client.post(reverse("staffing:merge_employments", args=[self.first.id, self.second.id]),
                                    {"next": reverse("warnings")})

        self.assertRedirects(response, reverse("warnings"))
        self.assertEqual(Employment.objects.count(), 1)
