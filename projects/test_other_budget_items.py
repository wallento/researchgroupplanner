from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from projects.models import OtherBudgetItem, OtherBudgetItemTransaction, Project


STATIC_STORAGE = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(STORAGES=STATIC_STORAGE)
class OtherBudgetItemsPageTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user("user", password="x"))
        self.project = Project.objects.create(
            acronym="TEST", start_date=date(2025, 1, 1), end_date=date(2026, 12, 31), budget_total=Decimal("10000"),
        )
        self.travel = OtherBudgetItem.objects.create(project=self.project, title="Reisen", amount=Decimal("1000"))
        self.conference = OtherBudgetItemTransaction.objects.create(
            budget_item=self.travel, date=date(2025, 3, 1), amount=Decimal("400"), description="Konferenz",
        )
        OtherBudgetItemTransaction.objects.create(
            budget_item=self.travel, date=date(2027, 2, 1), amount=Decimal("300"), description="Nach Laufzeit",
        )

    def test_lists_transactions_and_counts_only_project_years(self):
        response = self.client.get(reverse("projects:other_budget_items", args=["TEST"]))

        item = response.context["budget_items"][0]
        self.assertEqual(item.used, Decimal("400"))
        self.assertEqual(item.remain, Decimal("600"))
        self.assertContains(response, "Konferenz")
        self.assertContains(response, "außerhalb Laufzeit")

    def test_details_page_links_to_overview(self):
        response = self.client.get(reverse("projects:details", args=["TEST"]))

        self.assertContains(response, reverse("projects:other_budget_items", args=["TEST"]))

    def test_transaction_description_can_be_edited(self):
        url = reverse("projects:other_budget_transaction_description", args=["TEST", self.conference.id])

        response = self.client.post(url, {"description": " Konferenz Berlin "})

        self.assertRedirects(
            response, reverse("projects:other_budget_items", args=["TEST"]) + f"#transaction-{self.conference.id}",
            fetch_redirect_response=False,
        )
        self.conference.refresh_from_db()
        self.assertEqual(self.conference.description, "Konferenz Berlin")
        self.assertContains(self.client.get(reverse("projects:other_budget_items", args=["TEST"])), "Konferenz Berlin")

    def test_transaction_description_requires_matching_project(self):
        Project.objects.create(
            acronym="OTHER", start_date=date(2025, 1, 1), end_date=date(2026, 12, 31), budget_total=Decimal("1000"),
        )

        response = self.client.post(
            reverse("projects:other_budget_transaction_description", args=["OTHER", self.conference.id]), {"description": "x"},
        )

        self.assertEqual(response.status_code, 404)
