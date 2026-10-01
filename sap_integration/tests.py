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

from projects.models import AnnualPool, Project, SAPFund
from sap_integration.cache import fund_values, load_year
from sap_integration.cleaning import clean_fund_values, clean_transactions
from sap_integration.config import SAPConfig, SAPConfigurationError
from sap_integration.backends.wuerzburg import WuerzburgWebGUIBackend
from sap_integration.parser import parse_downloaded_reports
from sap_integration.sync import SAPSyncResult, run_sync
from sap_integration.views import _project_time_percentage
from sap_integration.workbooks import same_nonempty_workbook_content


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
        budget_path = download_dir / "budget.xlsx"
        actual_path = download_dir / "actual.xlsx"
        commitments_path = download_dir / "commitments.xlsx"
        _write_workbook(budget_path, ["Fonds", "Betrag"], [["FUND", year]])
        _write_workbook(actual_path, ["Fonds", "Betrag"], [["FUND", 10]])
        _write_workbook(commitments_path, ["Fonds", "Betrag"], [["FUND", 20]])
        return {
            "budget": budget_path,
            "actual": actual_path,
            "commitments": commitments_path,
        }


class DuplicateTransactionSAPBackend(FakeSAPBackend):
    def download(self, year, download_dir):
        reports = super().download(year, download_dir)
        _write_workbook(
            reports["commitments"],
            ["Fonds", "Betrag"],
            [["FUND", 10]],
        )
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

    def test_identical_nonempty_transaction_exports_are_detected(self):
        with tempfile.TemporaryDirectory() as data_dir:
            first = Path(data_dir) / "actual.xlsx"
            second = Path(data_dir) / "commitments.xlsx"
            headers = ["Fonds", "Betrag"]
            rows = [["FUND", 10]]
            _write_workbook(first, headers, rows)
            _write_workbook(second, headers, rows)

            self.assertTrue(same_nonempty_workbook_content(first, second))

    def test_empty_transaction_exports_may_be_identical(self):
        with tempfile.TemporaryDirectory() as data_dir:
            first = Path(data_dir) / "actual.xlsx"
            second = Path(data_dir) / "commitments.xlsx"
            headers = ["Fonds", "Betrag"]
            _write_workbook(first, headers, [])
            _write_workbook(second, headers, [])

            self.assertFalse(same_nonempty_workbook_content(first, second))

    def test_sync_does_not_publish_identical_transaction_exports(self):
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
                backend=(
                    "sap_integration.tests.DuplicateTransactionSAPBackend"
                ),
            )

            with self.assertRaisesMessage(ValueError, "denselben nicht-leeren Export"):
                run_sync(config, 2026)

            self.assertFalse((Path(data_dir) / "raw" / "2026").exists())


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


class SAPCleaningTests(SimpleTestCase):
    def test_removes_exact_counter_bookings_and_groups_equal_positions(self):
        transactions = [
            _transaction("actual", "", "Falsche Finanzstelle", "-135.30"),
            _transaction("actual", "Partner", "Korrigierte Buchung", "135.30"),
            _transaction("commitment", "Person", "Gehalt April", "1903.75"),
            _transaction("commitment", "Person", "Gehalt April", "531.17"),
        ]

        cleaned = clean_transactions(transactions)

        self.assertEqual(len(cleaned), 1)
        self.assertEqual(cleaned[0]["type"], "commitment")
        self.assertEqual(cleaned[0]["business_partner"], "Person")
        self.assertEqual(cleaned[0]["position"], "Gehalt April")
        self.assertEqual(cleaned[0]["amount"], Decimal("2434.92"))

    def test_does_not_cancel_actual_against_commitment(self):
        transactions = [
            _transaction("actual", "Partner", "Bezahlt", "-100.00"),
            _transaction("commitment", "Partner", "Vorgemerkt", "100.00"),
        ]

        self.assertEqual(len(clean_transactions(transactions)), 2)

    def test_negative_actuals_become_funding_after_counter_bookings(self):
        values = {
            "has_budget": True,
            "budget": Decimal("1000.00"),
            "transactions": [
                _transaction("actual", "", "Fehlbuchung", "-100.00"),
                _transaction("actual", "Partner", "Korrektur", "100.00"),
                _transaction("actual", "Förderer", "Zahlungsanforderung", "-500.00"),
                _transaction("actual", "Partner", "Ausgabe", "200.00"),
                _transaction("commitment", "Partner", "Vorgemerkt", "50.00"),
            ],
        }

        cleaned = clean_fund_values(
            values,
            treat_negative_actuals_as_funding=True,
        )

        self.assertEqual(cleaned["actual_total"], Decimal("200.00"))
        self.assertEqual(cleaned["funding_total"], Decimal("500.00"))
        self.assertEqual(cleaned["commitments_total"], Decimal("50.00"))
        self.assertEqual(cleaned["combined_total"], Decimal("250.00"))
        self.assertEqual(cleaned["remaining"], Decimal("750.00"))
        self.assertEqual(len(cleaned["transactions"]), 3)
        funding = [row for row in cleaned["transactions"] if row["is_funding"]]
        self.assertEqual(len(funding), 1)
        self.assertEqual(funding[0]["amount"], Decimal("-500.00"))


class SAPProjectTimeTests(SimpleTestCase):
    def test_time_percentage_is_bounded_and_uses_project_dates(self):
        project = Project(
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 11),
            budget_total=Decimal("1000.00"),
        )

        self.assertEqual(
            _project_time_percentage(project, date(2025, 12, 31)),
            Decimal("0.00"),
        )
        self.assertEqual(
            _project_time_percentage(project, date(2026, 1, 6)),
            Decimal("50.00"),
        )
        self.assertEqual(
            _project_time_percentage(project, date(2026, 1, 20)),
            Decimal("100.00"),
        )

    def test_time_percentage_uses_cost_neutral_extension(self):
        project = Project(
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 11),
            extension_planning_date=date(2026, 1, 21),
            budget_total=Decimal("1000.00"),
        )

        self.assertEqual(
            _project_time_percentage(project, date(2026, 1, 11)),
            Decimal("50.00"),
        )


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
        self.assertContains(response, "Gesamtbudget")
        self.assertContains(response, "Auslastung")
        self.assertContains(response, 'id="overview-year-dropdown"', html=False)
        self.assertContains(response, "Geschäftsjahr 2026")
        self.assertNotContains(response, "NO-DATA")

        lifetime = response.context["rows"][0]["lifetime"]
        self.assertEqual(lifetime["budget"], Decimal("1000.00"))
        self.assertEqual(lifetime["used"], Decimal("300.00"))
        self.assertEqual(lifetime["utilization"], Decimal("30.00"))
        self.assertIn("time_percentage", lifetime)
        self.assertEqual(lifetime["utilization_bar_width"], "30.00")
        self.assertContains(response, "Auslastung / Laufzeit")
        self.assertContains(response, "Finanzielle Auslastung")
        self.assertContains(response, "Vergangene Projektlaufzeit")
        self.assertContains(response, 'class="table table-hover table-bordered align-top"', html=False)
        self.assertNotContains(response, "bis 31.12.2026")

    def test_project_utilization_combines_all_years_and_funds(self):
        second_fund = SAPFund.objects.create(
            fund_number="WEB-SECOND",
            project=self.project,
        )
        _write_processed_cache(
            self.data_dir,
            self.fund.fund_number,
            year=2025,
            transactions=[
                _transaction("actual", "Partner", "Alt", "50.00"),
                _transaction("commitment", "Partner", "Alt vorgemerkt", "100.00"),
            ],
        )
        _write_processed_cache(
            self.data_dir,
            second_fund.fund_number,
            transactions=[
                _transaction("actual", "Partner", "Zweiter Fonds", "100.00"),
            ],
        )

        with self.settings(SAP_ENABLED=True, SAP_DATA_DIR=self.data_dir):
            response = self.client.get(
                reverse("sap_integration:overview_year", args=[2026])
            )

        project_rows = [
            row for row in response.context["rows"] if row["fund"].project_id
        ]
        self.assertEqual(len(project_rows), 2)
        for row in project_rows:
            self.assertEqual(row["lifetime"]["used"], Decimal("550.00"))
            self.assertEqual(row["lifetime"]["utilization"], Decimal("55.00"))

    def test_non_project_fund_has_no_lifetime_budget_or_utilization(self):
        annual_pool = AnnualPool.objects.create(title="Pool")
        pool_fund = SAPFund.objects.create(
            fund_number="POOL-FUND",
            annual_pool=annual_pool,
        )
        _write_processed_cache(
            self.data_dir,
            pool_fund.fund_number,
            transactions=[
                _transaction("actual", "Partner", "Pool-Ausgabe", "25.00"),
            ],
        )

        with self.settings(SAP_ENABLED=True, SAP_DATA_DIR=self.data_dir):
            response = self.client.get(reverse("sap_integration:overview"))

        pool_row = next(
            row for row in response.context["rows"] if row["fund"] == pool_fund
        )
        self.assertIsNone(pool_row["lifetime"])

    def test_fund_detail_displays_actual_and_grey_commitment(self):
        with self.settings(SAP_ENABLED=True, SAP_DATA_DIR=self.data_dir):
            response = self.client.get(
                reverse("sap_integration:fund_detail", args=[2026, self.fund.id])
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Kontoauszug WEB-FUND")
        self.assertContains(response, "Geschäftspartner")
        self.assertContains(response, "Bereinigen")
        self.assertContains(response, 'id="statement-year-dropdown"', html=False)
        self.assertContains(response, "Geschäftsjahr 2026")
        self.assertContains(response, 'class="table-secondary"', html=False)

    def test_clean_year_dropdown_preserves_clean_mode(self):
        _write_processed_cache(
            self.data_dir,
            self.fund.fund_number,
            year=2025,
        )
        with self.settings(SAP_ENABLED=True, SAP_DATA_DIR=self.data_dir):
            response = self.client.get(
                reverse("sap_integration:fund_detail", args=[2026, self.fund.id]),
                {"clean": "1"},
            )

        target = reverse(
            "sap_integration:fund_detail",
            args=[2025, self.fund.id],
        )
        self.assertContains(response, f'href="{target}?clean=1"', html=False)

    def test_clean_fund_detail_uses_cleaned_transactions(self):
        _write_processed_cache(
            self.data_dir,
            self.fund.fund_number,
            transactions=[
                _transaction("actual", "", "Falsche Buchung", "-100.00"),
                _transaction("actual", "Partner", "Korrektur", "100.00"),
                _transaction("commitment", "Person", "Gehalt April", "100.00"),
                _transaction("commitment", "Person", "Gehalt April", "50.00"),
            ],
        )
        with self.settings(SAP_ENABLED=True, SAP_DATA_DIR=self.data_dir):
            response = self.client.get(
                reverse("sap_integration:fund_detail", args=[2026, self.fund.id]),
                {"clean": "1"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Bereinigte Ansicht")
        self.assertContains(response, "Original anzeigen")
        self.assertContains(response, "Gehalt April", count=1)
        self.assertContains(response, "150,00 €")
        self.assertNotContains(response, "Falsche Buchung")
        self.assertNotContains(response, "Korrektur")

    def test_configured_fund_displays_and_excludes_funding_in_clean_view(self):
        self.fund.treat_negative_actuals_as_funding = True
        self.fund.save(update_fields=["treat_negative_actuals_as_funding"])
        transactions = [
            _transaction("actual", "", "Fehlbuchung", "-100.00"),
            _transaction("actual", "Partner", "Korrektur", "100.00"),
            _transaction("actual", "Förderer", "Zahlungsanforderung", "-500.00"),
            _transaction("actual", "Partner", "Ausgabe", "200.00"),
            _transaction("commitment", "Partner", "Vorgemerkt", "50.00"),
        ]
        _write_processed_cache(
            self.data_dir,
            self.fund.fund_number,
            transactions=transactions,
        )

        with self.settings(SAP_ENABLED=True, SAP_DATA_DIR=self.data_dir):
            response = self.client.get(
                reverse("sap_integration:fund_detail", args=[2026, self.fund.id]),
                {"clean": "1"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["values"]["actual_total"], Decimal("200.00"))
        self.assertEqual(response.context["values"]["funding_total"], Decimal("500.00"))
        self.assertEqual(response.context["values"]["remaining"], Decimal("750.00"))
        self.assertContains(response, 'class="table-success"', html=False)
        self.assertContains(response, "Mittelzufluss")
        self.assertContains(response, "500,00 €")

    def test_overview_uses_adjusted_values_for_configured_fund(self):
        self.fund.treat_negative_actuals_as_funding = True
        self.fund.save(update_fields=["treat_negative_actuals_as_funding"])
        _write_processed_cache(
            self.data_dir,
            self.fund.fund_number,
            transactions=[
                _transaction("actual", "Förderer", "Zahlungsanforderung", "-500.00"),
                _transaction("actual", "Partner", "Ausgabe", "200.00"),
                _transaction("commitment", "Partner", "Vorgemerkt", "50.00"),
            ],
        )

        with self.settings(SAP_ENABLED=True, SAP_DATA_DIR=self.data_dir):
            response = self.client.get(reverse("sap_integration:overview"))

        values = response.context["rows"][0]["values"]
        lifetime = response.context["rows"][0]["lifetime"]
        self.assertEqual(values["actual_total"], Decimal("200.00"))
        self.assertEqual(values["funding_total"], Decimal("500.00"))
        self.assertEqual(values["remaining"], Decimal("750.00"))
        self.assertEqual(lifetime["used"], Decimal("250.00"))
        self.assertEqual(lifetime["utilization"], Decimal("25.00"))
        self.assertContains(response, "Mittelzuflüsse bereinigt")
        self.assertNotContains(response, "Mittelzuflüsse nicht eingerechnet")

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


def _transaction(transaction_type, business_partner, position, amount):
    return {
        "type": transaction_type,
        "business_partner": business_partner,
        "position": position,
        "amount": amount,
        "booking_date": None,
    }


def _write_processed_cache(data_dir, fund_number, transactions=None, year=2026):
    processed_dir = data_dir / "processed"
    processed_dir.mkdir(parents=True, exist_ok=True)
    transactions = transactions or [
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
    ]
    actual_total = sum(
        (Decimal(row["amount"]) for row in transactions if row["type"] == "actual"),
        Decimal("0"),
    )
    commitments_total = sum(
        (
            Decimal(row["amount"])
            for row in transactions
            if row["type"] == "commitment"
        ),
        Decimal("0"),
    )
    combined_total = actual_total + commitments_total
    cache_path = processed_dir / f"{year}.json"
    payload = (
        json.loads(cache_path.read_text(encoding="utf-8"))
        if cache_path.exists()
        else {
            "schema_version": 1,
            "year": year,
            "generated_at": "2026-07-17T10:00:00+00:00",
            "funds": {},
        }
    )
    payload["funds"][fund_number] = {
        "fund_number": fund_number,
        "has_budget": True,
        "budget": "1000.00",
        "actual_total": str(actual_total),
        "commitments_total": str(commitments_total),
        "combined_total": str(combined_total),
        "remaining": str(Decimal("1000.00") - combined_total),
        "transactions": transactions,
    }
    cache_path.write_text(json.dumps(payload), encoding="utf-8")
