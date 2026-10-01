import base64
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from django.conf import settings as django_settings
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from controlling.backups import (
    BackupResult,
    NextcloudPublicShareClient,
    backups_to_remove,
    perform_database_backup,
)


class SQLiteBackupTests(SimpleTestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.database_path = self.root / "source.sqlite3"
        with sqlite3.connect(self.database_path) as connection:
            connection.execute("CREATE TABLE example (value TEXT)")
            connection.execute("INSERT INTO example VALUES ('preserved')")

    def test_backup_is_a_valid_plain_sqlite_database(self):
        backup_dir = self.root / "backups"
        database_settings = {
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": self.database_path,
            }
        }
        with patch.object(django_settings, "DATABASES", database_settings):
            with self.settings(
                DB_BACKUP_DIR=backup_dir,
                DB_BACKUP_KEEP_DAILY=7,
                DB_BACKUP_KEEP_MONTHLY=12,
                DB_BACKUP_KEEP_YEARLY=1,
                NEXTCLOUD_BACKUP_SHARE_URL="",
            ):
                result = perform_database_backup(
                    kind="manual",
                    now=datetime(2026, 7, 21, 12, 0),
                )

        self.assertTrue(result.path.is_file())
        self.assertEqual(result.path.stat().st_mode & 0o777, 0o600)
        with sqlite3.connect(result.path) as connection:
            value = connection.execute("SELECT value FROM example").fetchone()
            integrity = connection.execute("PRAGMA quick_check").fetchone()
        self.assertEqual(value, ("preserved",))
        self.assertEqual(integrity, ("ok",))

    def test_rotation_keeps_recent_manual_and_scheduled_backups(self):
        now = datetime(2026, 7, 21, 12, 0)

        def name(kind, timestamp):
            return f"db-{kind}-{timestamp:%Y%m%d-%H%M%S-%f}.sqlite3"

        recent = {
            name("manual", now),
            name("scheduled", now - timedelta(hours=2)),
            name("manual", now - timedelta(days=6)),
        }
        monthly = {
            name(
                "scheduled",
                datetime(2026 if month <= 6 else 2025, month, 28, 2, 0),
            )
            for month in list(range(6, 0, -1)) + list(range(12, 6, -1))
        }
        yearly = {name("scheduled", datetime(2024, 12, 31, 2, 0))}
        obsolete = {name("scheduled", datetime(2023, 12, 31, 2, 0))}
        all_names = recent | monthly | yearly | obsolete | {"unrelated.txt"}

        removed = set(backups_to_remove(all_names, now, 7, 12, 1))

        self.assertTrue(recent.isdisjoint(removed))
        self.assertTrue(monthly.isdisjoint(removed))
        self.assertTrue(yearly.isdisjoint(removed))
        self.assertEqual(removed, obsolete)
        self.assertNotIn("unrelated.txt", removed)


class _FakeResponse:
    def __init__(self, body=b""):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return self.body


class NextcloudBackupTests(SimpleTestCase):
    def test_password_protected_share_uses_webdav_and_lists_only_backups(self):
        listing = b"""<?xml version="1.0"?>
            <d:multistatus xmlns:d="DAV:">
              <d:response><d:href>/public.php/dav/files/token/</d:href></d:response>
              <d:response><d:href>/public.php/dav/files/token/db-scheduled-20260721-020000-000000.sqlite3</d:href></d:response>
              <d:response><d:href>/public.php/dav/files/token/notes.txt</d:href></d:response>
            </d:multistatus>"""
        client = NextcloudPublicShareClient(
            "https://cloud.example.com/s/token",
            "secret",
        )

        with patch(
            "controlling.backups.urlopen",
            return_value=_FakeResponse(listing),
        ) as mocked_urlopen:
            names = client.list_backup_names()

        self.assertEqual(
            names,
            {"db-scheduled-20260721-020000-000000.sqlite3"},
        )
        request = mocked_urlopen.call_args.args[0]
        self.assertEqual(
            request.full_url,
            "https://cloud.example.com/public.php/dav/files/token",
        )
        expected_auth = base64.b64encode(b"anonymous:secret").decode("ascii")
        self.assertEqual(request.get_header("Authorization"), f"Basic {expected_auth}")


class ManualBackupViewTests(TestCase):
    def setUp(self):
        self.staff_user = get_user_model().objects.create_user(
            username="backup-admin",
            password="test-password",
            is_staff=True,
        )
        self.client.force_login(self.staff_user)

    @patch("controlling.views.perform_database_backup")
    def test_statistics_button_creates_manual_backup(self, mocked_backup):
        mocked_backup.return_value = BackupResult(
            path=Path("/data/backups/db-manual-test.sqlite3"),
            uploaded_to_nextcloud=True,
            local_files_removed=0,
            remote_files_removed=0,
        )

        with self.settings(
            DB_BACKUP_ENABLED=True,
            NEXTCLOUD_BACKUP_SHARE_URL="https://cloud.example.com/s/token",
        ):
            statistics_response = self.client.get(reverse("statistics"))
            backup_response = self.client.post(reverse("create_manual_backup"))

        self.assertContains(statistics_response, "Backup jetzt erstellen")
        self.assertEqual(backup_response.status_code, 200)
        self.assertTrue(backup_response.json()["success"])
        self.assertIn("lokal und in Nextcloud", backup_response.json()["message"])
        mocked_backup.assert_called_once_with(kind="manual")

    def test_manual_backup_endpoint_is_disabled_by_default(self):
        response = self.client.post(reverse("create_manual_backup"))

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["success"])
