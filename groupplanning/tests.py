from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.test import TestCase, override_settings
from django.urls import reverse


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    }
)
class AuthenticationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="planner-user",
            password="safe-test-password",
        )

    def test_anonymous_user_is_redirected_from_start_page(self):
        response = self.client.get(reverse("main"))

        self.assertRedirects(response, f"{reverse('login')}?next={reverse('main')}")

    def test_custom_login_page_is_public(self):
        response = self.client.get(reverse("login"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Research Group Planner")
        self.assertContains(response, "Anmelden")
        self.assertContains(response, "controlling/img/Logo-EN.svg")
        self.assertNotContains(response, ">Admin<", html=False)

    def test_login_logo_exists_in_static_files(self):
        self.assertIsNotNone(finders.find("controlling/img/Logo-EN.svg"))

    def test_login_returns_user_to_requested_page(self):
        response = self.client.post(
            reverse("login"),
            {
                "username": "planner-user",
                "password": "safe-test-password",
                "next": reverse("projects:index"),
            },
        )

        self.assertRedirects(response, reverse("projects:index"), fetch_redirect_response=False)

    def test_authenticated_user_can_open_start_page(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("main"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "planner-user")

    def test_logout_ends_session(self):
        self.client.force_login(self.user)

        response = self.client.post(reverse("logout"))

        self.assertRedirects(response, reverse("login"))
        protected_response = self.client.get(reverse("main"))
        self.assertRedirects(
            protected_response,
            f"{reverse('login')}?next={reverse('main')}",
        )
