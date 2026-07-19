import json
import tempfile
from datetime import date
from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.urls import reverse
from django.test import SimpleTestCase, TestCase, override_settings
from openpyxl import Workbook
from selenium.common.exceptions import StaleElementReferenceException

from projects.models import Project, SAPFund
from sap_integration.cache import fund_values, load_year
from sap_integration.config import SAPConfig, SAPConfigurationError
from sap_integration.backends.wuerzburg import WuerzburgWebGUIBackend
from sap_integration.parser import parse_downloaded_reports
from sap_integration.sync import SAPSyncResult, run_sync


SAP_TEST_SETTINGS = {
    "SAP_ENABLED": True,
    "SAP_URL": "https://sap.example.test/webgui",
    "SAP_USER": "test-user",
    "SAP_PASSWORD": "test-password",
    "SAP_FINANZSTELLE": "1234",
    "SAP_BROWSER": "chrome",
    "SAP_BROWSER_BINARY": "",
    "SAP_HEADLESS": True,
    "SAP_TIMEOUT": 30,
    "SAP_ACTION_DELAY": 0,
    "SAP_BACKEND": "sap_integration.tests.FakeSAPBackend",
}


class FakeSAPBackend:
    def __init__(self, config):
        self.config = config

    def download(self, year, download_dir):
        reports = {}
        for report_name in ("budget", "actual", "commitments"):
            report_path = download_dir / f"{report_name}.xlsx"
            report_path.write_bytes(f"{report_name}-{year}".encode())
            reports[report_name] = report_path
        return reports


class SAPConfigTests(SimpleTestCase):
    @override_settings(SAP_ENABLED=False)
    def test_disabled_integration_is_rejected(self):
        with self.assertRaisesMessage(SAPConfigurationError, "deaktiviert"):
            SAPConfig.from_settings()

    @override_settings(**(SAP_TEST_SETTINGS | {"SAP_PASSWORD": ""}))
    def test_missing_credentials_are_reported(self):
        with self.assertRaisesMessage(SAPConfigurationError, "SAP_PASSWORD"):
            SAPConfig.from_settings()

    @override_settings(**(SAP_TEST_SETTINGS | {"SAP_BROWSER": "safari"}))
    def test_unknown_browser_is_rejected(self):
        with self.assertRaisesMessage(SAPConfigurationError, "SAP_BROWSER"):
            SAPConfig.from_settings()


class SAPSyncTests(SimpleTestCase):
    def test_stale_sap_element_is_retried(self):
        calls = 0

        def stale_once():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise StaleElementReferenceException()
            return "clicked"

        result = WuerzburgWebGUIBackend._retry_stale(stale_once)

        self.assertEqual(result, "clicked")
        self.assertEqual(calls, 2)

    def test_sync_publishes_all_reports_and_status_atomically(self):
        with tempfile.TemporaryDirectory() as data_dir:
            config = SAPConfig(
                enabled=True,
                url=SAP_TEST_SETTINGS["SAP_URL"],
                user=SAP_TEST_SETTINGS["SAP_USER"],
                password=SAP_TEST_SETTINGS["SAP_PASSWORD"],
                finanzstelle=SAP_TEST_SETTINGS["SAP_FINANZSTELLE"],
                data_dir=Path(data_dir),
                browser="chrome",
                browser_binary="",
                headless=True,
                timeout=30,
                action_delay=0,
                backend=SAP_TEST_SETTINGS["SAP_BACKEND"],
            )

            result = run_sync(config, 2026)

            self.assertEqual(set(result.report_paths), {"budget", "actual", "commitments"})
            for report_path in result.report_paths.values():
                self.assertTrue(report_path.is_file())
                self.assertEqual(report_path.parent, Path(data_dir) / "raw" / "2026")

            status = json.loads((Path(data_dir) / "last_download.json").read_text())
            self.assertEqual(status["year"], 2026)
            self.assertNotIn("test-password", json.dumps(status))


class SyncSAPCommandTests(TestCase):
    @override_settings(SAP_ENABLED=False)
    def test_command_refuses_to_run_when_integration_is_disabled(self):
        with self.assertRaisesMessage(CommandError, "deaktiviert"):
            call_command("sync_sap")

    @override_settings(**SAP_TEST_SETTINGS)
    @patch("sap_integration.management.commands.sync_sap.parse_downloaded_reports")
    @patch("sap_integration.management.commands.sync_sap.run_sync")
    def test_command_runs_configured_backend(self, run_sync_mock, parse_mock):
        run_sync_mock.return_value = SAPSyncResult(
            year=2025,
            report_paths={
                "budget": Path("budget.xlsx"),
                "actual": Path("actual.xlsx"),
                "commitments": Path("commitments.xlsx"),
            },
            completed_at="2026-07-17T10:00:00+00:00",
        )
        parse_mock.return_value = Path("processed/2025.json")
        stdout = StringIO()

        call_command("sync_sap", year=2025, stdout=stdout)

        run_sync_mock.assert_called_once()
        self.assertIn("3 Exporte gespeichert", stdout.getvalue())

    @override_settings(**SAP_TEST_SETTINGS)
    def test_command_rejects_invalid_year(self):
        with self.assertRaisesMessage(CommandError, "zwischen 2000 und 2100"):
            call_command("sync_sap", year=1999)


class SAPParserTests(SimpleTestCase):
    def test_parser_builds_budget_statement_and_budgetless_fund(self):
        with tempfile.TemporaryDirectory() as data_dir:
            raw_dir = Path(data_dir) / "raw" / "2026"
            raw_dir.mkdir(parents=True)
            _write_workbook(
                raw_dir / "budget.xlsx",
                ["Fonds", "Betrag"],
                [["WITH-BUDGET", 1000], ["IGNORED", 9999]],
            )
            transaction_headers = [
                "Fonds",
                "Name des Geschäftspartners",
                "Betrag",
                "Belegkopftext",
                "Positionstext",
                "Buchungsdatum",
            ]
            _write_workbook(
                raw_dir / "actual.xlsx",
                transaction_headers,
                [
                    ["WITH-BUDGET", "Partner", 125.5, "Kopf", "Position", date(2026, 2, 3)],
                    ["NO-BUDGET", "", -500, "Zuweisung", "", date(2026, 1, 1)],
                    ["NO-BUDGET", "Partner", 100, "Ausgabe", "Text", date(2026, 2, 1)],
                    ["IGNORED", "Secret", 9999, "Nicht", "anzeigen", None],
                ],
            )
            _write_workbook(
                raw_dir / "commitments.xlsx",
                transaction_headers,
                [["WITH-BUDGET", "Partner", 200, "Bindung", "Mai", None]],
            )

            target = parse_downloaded_reports(
                data_dir,
                2026,
                ["WITH-BUDGET", "NO-BUDGET"],
            )
            payload = load_year(data_dir, 2026)
            with_budget = fund_values(payload["funds"]["WITH-BUDGET"])
            without_budget = fund_values(payload["funds"]["NO-BUDGET"])

            self.assertTrue(target.is_file())
            self.assertEqual(with_budget["budget"], Decimal("1000.00"))
            self.assertEqual(with_budget["actual_total"], Decimal("125.50"))
            self.assertEqual(with_budget["commitments_total"], Decimal("200.00"))
            self.assertEqual(with_budget["remaining"], Decimal("674.50"))
            self.assertEqual(with_budget["transactions"][0]["position"], "Kopf Position")
            self.assertEqual(with_budget["transactions"][0]["booking_date"], "2026-02-03")
            self.assertFalse(without_budget["has_budget"])
            self.assertEqual(without_budget["actual_total"], Decimal("-400.00"))
            self.assertIsNone(without_budget["remaining"])
            self.assertNotIn("IGNORED", payload["funds"])

    def test_parser_reports_missing_required_column(self):
        with tempfile.TemporaryDirectory() as data_dir:
            raw_dir = Path(data_dir) / "raw" / "2026"
            raw_dir.mkdir(parents=True)
            _write_workbook(raw_dir / "budget.xlsx", ["Fonds"], [["FUND"]])
            _write_workbook(raw_dir / "actual.xlsx", [], [])
            _write_workbook(raw_dir / "commitments.xlsx", [], [])

            with self.assertRaisesMessage(ValueError, "Betrag"):
                parse_downloaded_reports(data_dir, 2026, ["FUND"])


class SAPViewsTests(TestCase):
    def setUp(self):
        self.data_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.data_directory.cleanup)
        self.data_dir = Path(self.data_directory.name)
        self.project = Project.objects.create(
            acronym="WEB",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            budget_total=Decimal("1000.00"),
        )
        self.fund = SAPFund.objects.create(
            fund_number="WEB-FUND",
            label="Webfonds",
            project=self.project,
        )
        self.empty_fund = SAPFund.objects.create(
            fund_number="NO-DATA",
            label="Noch nicht vorhanden",
            project=self.project,
        )
        self.user = get_user_model().objects.create_user(
            username="sap-admin",
            password="test-password",
            is_staff=True,
        )
        self.client.force_login(self.user)
        _write_processed_cache(self.data_dir, self.fund.fund_number)

    def test_overview_lists_fund_and_year(self):
        with self.settings(SAP_ENABLED=True, SAP_DATA_DIR=self.data_dir):
            response = self.client.get(reverse("sap_integration:overview"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "WEB-FUND")
        self.assertContains(response, "2026")
        self.assertContains(response, "Obligo")
        self.assertNotContains(response, "NO-DATA")

    def test_fund_detail_displays_actual_and_grey_commitment(self):
        with self.settings(SAP_ENABLED=True, SAP_DATA_DIR=self.data_dir):
            response = self.client.get(
                reverse("sap_integration:fund_detail", args=[2026, self.fund.id])
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Kontoauszug WEB-FUND")
        self.assertContains(response, "Geschäftspartner")
        self.assertContains(response, 'class="table-secondary"', html=False)

    def test_fund_without_entries_is_not_available_for_year(self):
        with self.settings(SAP_ENABLED=True, SAP_DATA_DIR=self.data_dir):
            response = self.client.get(
                reverse("sap_integration:fund_detail", args=[2026, self.empty_fund.id])
            )

        self.assertEqual(response.status_code, 404)

    def test_pages_are_not_available_when_feature_is_disabled(self):
        with self.settings(SAP_ENABLED=False, SAP_DATA_DIR=self.data_dir):
            response = self.client.get(reverse("sap_integration:overview"))

        self.assertEqual(response.status_code, 404)


def _write_workbook(path, headers, rows):
    workbook = Workbook()
    worksheet = workbook.active
    if headers:
        worksheet.append(headers)
    for row in rows:
        worksheet.append(row)
    workbook.save(path)


def _write_processed_cache(data_dir, fund_number):
    processed_dir = data_dir / "processed"
    processed_dir.mkdir(parents=True)
    payload = {
        "schema_version": 1,
        "year": 2026,
        "generated_at": "2026-07-17T10:00:00+00:00",
        "funds": {
            fund_number: {
                "fund_number": fund_number,
                "has_budget": True,
                "budget": "1000.00",
                "actual_total": "100.00",
                "commitments_total": "200.00",
                "combined_total": "300.00",
                "remaining": "700.00",
                "transactions": [
                    {
                        "type": "actual",
                        "business_partner": "Partner",
                        "position": "Bezahlt",
                        "amount": "100.00",
                        "booking_date": "2026-01-01",
                    },
                    {
                        "type": "commitment",
                        "business_partner": "Partner",
                        "position": "Vorgemerkt",
                        "amount": "200.00",
                        "booking_date": None,
                    },
                ],
            }
        },
    }
    (processed_dir / "2026.json").write_text(json.dumps(payload), encoding="utf-8")
