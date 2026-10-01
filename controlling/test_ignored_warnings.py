from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from controlling.models import IgnoredWarning
from staffing.models import Employment, StaffMember


STATIC_STORAGE = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(STORAGES=STATIC_STORAGE, SAP_ENABLED=False, SAP_GM_IMPORT_ENABLED=False)
class IgnoredWarningTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user("user", password="x"))
        member = StaffMember.objects.create(first_name="Erika", last_name="Muster")
        self.employment = Employment.objects.create(
            staff_member=member, start_date=date(2026, 1, 1), end_date=date(2026, 6, 30), percentage=Decimal("50"),
        )
        self.title = f"Keine Zuordnung für {member}"

    def warning(self, warnings):
        return next(w for w in warnings if w["title"] == self.title)

    def test_ignored_warning_moves_to_ignored_list(self):
        warning = self.warning(self.client.get(reverse("warnings")).context["warnings_list"])

        self.client.post(reverse("ignore_warning"), {"key": warning["key"], "comment": "Bekannt"})

        response = self.client.get(reverse("warnings"))
        self.assertNotIn(self.title, [w["title"] for w in response.context["warnings_list"]])
        self.assertEqual(self.warning(response.context["ignored_list"])["key"], warning["key"])

    def test_changed_warning_shows_again(self):
        warning = self.warning(self.client.get(reverse("warnings")).context["warnings_list"])
        self.client.post(reverse("ignore_warning"), {"key": warning["key"], "comment": "Bekannt"})

        self.employment.percentage = Decimal("75")
        self.employment.save()

        response = self.client.get(reverse("warnings"))
        self.assertIn(self.title, [w["title"] for w in response.context["warnings_list"]])

    def test_unignore_shows_warning_again(self):
        warning = self.warning(self.client.get(reverse("warnings")).context["warnings_list"])
        self.client.post(reverse("ignore_warning"), {"key": warning["key"], "comment": "Bekannt"})

        self.client.post(reverse("unignore_warning"), {"key": warning["key"]})

        self.assertFalse(IgnoredWarning.objects.exists())
        response = self.client.get(reverse("warnings"))
        self.assertIn(self.title, [w["title"] for w in response.context["warnings_list"]])

    def test_comment_is_required(self):
        warning = self.warning(self.client.get(reverse("warnings")).context["warnings_list"])

        self.client.post(reverse("ignore_warning"), {"key": warning["key"], "comment": "  "})

        self.assertFalse(IgnoredWarning.objects.exists())

    def test_comment_is_stored(self):
        warning = self.warning(self.client.get(reverse("warnings")).context["warnings_list"])

        self.client.post(reverse("ignore_warning"), {"key": warning["key"], "comment": "Vertrag folgt"})

        self.assertEqual(IgnoredWarning.objects.get().comment, "Vertrag folgt")
        self.assertContains(self.client.get(reverse("warnings")), "Vertrag folgt")
