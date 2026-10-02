import tempfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from openpyxl import Workbook

from projects.models import (
    OtherBudgetItem,
    OtherBudgetItemTransaction,
    Project,
    SAPFund,
    StaffBudgetItem,
    StaffBudgetItemEligibility,
)
from projects.utils import calculate_salary_for_allocation
from sap_integration.crosscheck import Status, build_reconciliation
from sap_integration.gm_export import import_gm_export, parse_contract_texts, parse_gm_export
from sap_integration.models import SAPCostTypeMapping, SAPIgnoredPosition, SAPPersonMapping
from sap_integration.names import match_staff_member, name_key
from sap_integration.transform import apply_monthly_salaries, create_staff_planning, create_transaction
from staffing.models import Employment, EmploymentSalaries, StaffFundingAllocation, StaffMember


PSP = "1/070009999"
FONDS = "1539930000"
HEADERS = [
    "PSP-Element", "Fonds", "E/A-Art", "Geschäftsjahr", "Buchungsperiode", "Belegnummer",
    "Referenzbelegnummer", "Text", "Buchungsdatum", "Transaktionswährung", "Transaktionswährg.",
    "Referenzposition", "Name 1", "Vorg. Referenzschl.", "FMM-Werttyp", "Werttyp Text",
    "Finanzposition", "Kostenstelle", "ReferenzSchl(Kopf) 1", "Belegkopftext", "Erfassungsdatum",
    "Vollständiger Name",
]
STATIC_STORAGE = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


def _row(reference, amount, value_type, cost_type="7221", text="", header="", predecessor="",
         booked=date(2026, 1, 31), psp=PSP, partner="", budget_title=""):
    values = {
        "PSP-Element": psp,
        "Fonds": FONDS,
        "E/A-Art": cost_type,
        "Geschäftsjahr": str(booked.year),
        "Buchungsperiode": str(booked.month),
        "Referenzbelegnummer": reference,
        "Text": text,
        "Buchungsdatum": datetime(booked.year, booked.month, booked.day),
        "Transaktionswährung": amount,
        "Transaktionswährg.": "EUR",
        "Name 1": partner,
        "Vorg. Referenzschl.": predecessor,
        "FMM-Werttyp": value_type,
        "Belegkopftext": header,
        "Finanzposition": f"1539.41.{budget_title}" if budget_title else "",
    }
    return [values.get(column, "") for column in HEADERS]


def _subtotal(reference, amount):
    values = {"Referenzbelegnummer": reference, "Transaktionswährung": amount, "Transaktionswährg.": "EUR"}
    return [values.get(column, "") for column in HEADERS]


def sample_rows():
    contract = "AV; 01.01.26 - 31.12.26; 50%; Storno zum 31.03.26"
    return [
        # Staff reservation (Mittelreservierung) and two monthly payroll payments.
        _row("4000100", 9000.0, "81", text=contract, header="AV, Muster-Frau, Erika", booked=date(2026, 1, 5)),
        _row("4000100", -3000.0, "81", text=contract, header="AV, Muster-Frau, Erika", booked=date(2026, 1, 31)),
        _row("4000100", -3000.0, "81", text=contract, header="AV, Muster-Frau, Erika", booked=date(2026, 2, 28)),
        _subtotal("4000100", 3000.0),
        _row("7126000001", 3000.0, "99", text="VIVA 012026 Muster-Frau Erika 01021990",
             header="KLIA_01.txt", predecessor="0004000100", booked=date(2026, 1, 31)),
        _row("7126000002", 3000.0, "99", cost_type="PERSONALKOSTEN",
             text="VIVA 022026 Muster-Frau Erika 01021990", header="KLIA_02.txt",
             predecessor="0004000100", booked=date(2026, 2, 28)),
        _subtotal("7126000002", 6000.0),
        # Student assistant with month-name contract period.
        _row("4000200", 1200.0, "81", text="SHK-oA; Jan.26 - März 26; 10 SWS", header="SHK; Wißkirchen, Lionel"),
        # Test reservation without amount.
        _row("4000300", 0.0, "81", header="TEST_MR_01"),
        # Travel reservation, payment referencing it.
        _row("8000001", 500.0, "81", cost_type="7464", text="RKE Berlin Muster", header="RKE Berlin"),
        _row("8000001", -500.0, "81", cost_type="7464", text="RKE Berlin Muster", header="RKE Berlin",
             booked=date(2026, 2, 10)),
        _row("7226000001", 480.0, "99", cost_type="7464", text="Berlin", predecessor="0008000001",
             booked=date(2026, 2, 10)),
        # Purchase chain: requisition -> order -> invoice.
        _row("10000001", 100.0, "50", cost_type="7444", text="Devboard"),
        _row("4500000001", 100.0, "51", cost_type="7444", text="Devboard", predecessor="0010000001"),
        _row("5100000001", 100.0, "99", cost_type="7444", text="Devboard", predecessor="4500000001",
             partner="Distributor"),
        _row("4500000001", -100.0, "51", cost_type="7444", text="Devboard", predecessor="0010000001"),
        # Income and budget (budget rows have no PSP-Element).
        _row("1826000001", -20000.0, "99", cost_type="7999", text="1. Mittelabruf"),
        _row("100000001", 60000.0, "R1", psp=""),
        _row("100000002", 2000.0, "R1", cost_type="7464", psp=""),
        _subtotal("", 0),
    ]


def write_export(path, rows=None, headers=HEADERS):
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Data"
    worksheet.append(headers)
    for row in rows if rows is not None else sample_rows():
        worksheet.append(row)
    workbook.save(path)
    return path


class TemporaryExportMixin:
    def export_path(self, rows=None, headers=HEADERS):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return write_export(Path(directory.name) / "EXPORT.xlsx", rows, headers)


class GMExportParserTests(TemporaryExportMixin, SimpleTestCase):
    def test_parses_positions_budget_and_skips_subtotals(self):
        parsed = parse_gm_export(self.export_path())

        self.assertEqual(parsed.psp_element, PSP)
        self.assertEqual(parsed.cost_types["7221"]["budget"], "60000.00")
        self.assertEqual(parsed.cost_types["7464"]["budget"], "2000.00")
        self.assertEqual(parsed.cost_types["7464"]["actual"], "480.00")
        positions = {position.reference: position for position in parsed.positions}
        self.assertEqual(
            set(positions),
            {"4000100", "4000200", "4000300", "8000001", "10000001", "1826000001"},
        )

        staff = positions["4000100"]
        self.assertEqual(staff.kind, "staff")
        self.assertEqual(staff.person_name, "Muster-Frau, Erika")
        self.assertEqual(staff.contract_type, "AV")
        self.assertEqual(staff.percentage, Decimal("50"))
        self.assertEqual(staff.contract_periods, [["2026-01-01", "2026-03-31"]])
        self.assertEqual(staff.actual, Decimal("6000.00"))
        self.assertEqual(staff.commitment, Decimal("3000.00"))
        self.assertEqual(staff.monthly_actuals, {"2026-01": "3000.00", "2026-02": "3000.00"})
        payroll_texts = [b["text"] for b in staff.bookings if b["text"].startswith("VIVA")]
        self.assertEqual(payroll_texts, ["VIVA 012026 Muster-Frau Erika", "VIVA 022026 Muster-Frau Erika"])

        student = positions["4000200"]
        self.assertEqual(student.contract_type, "SHK")
        self.assertEqual(student.weekly_hours, Decimal("10"))
        self.assertEqual(student.contract_periods, [["2026-01-01", "2026-03-31"]])

        self.assertEqual(positions["8000001"].kind, "travel")
        self.assertEqual(positions["8000001"].actual, Decimal("480.00"))
        self.assertEqual(positions["8000001"].commitment, Decimal("0.00"))
        self.assertEqual(positions["10000001"].kind, "other")
        self.assertEqual(positions["10000001"].actual, Decimal("100.00"))
        self.assertEqual(positions["10000001"].commitment, Decimal("100.00"))
        self.assertEqual(positions["1826000001"].kind, "income")

    def test_classifies_funder_cost_types_by_budget_title_and_text(self):
        rows = [
            _row("4000500", 2000.0, "81", cost_type="0812", text="AV 01.05.2026 - 30.04.2027, 100 %",
                 header="AV, Kirschke, Patrick", budget_title="42941"),
            _row("7126000500", 1000.0, "99", cost_type="0812", text="VIVA 052026 Kirschke Patrick",
                 predecessor="0004000500", budget_title="42941"),
            _row("8000500", 300.0, "81", cost_type="0899", text="RKE_Gent_10.-15.09.26",
                 header="RKE_Gent", budget_title="54741"),
            _row("5000500", 900.0, "81", cost_type="0835", text="Auftrag", header="Auftrag",
                 budget_title="54741"),
            _row("1826000500", -5000.0, "99", cost_type="0864", text="1. MA 2026", budget_title="28241"),
        ]
        positions = {p.reference: p for p in parse_gm_export(self.export_path(rows)).positions}

        self.assertEqual(positions["4000500"].kind, "staff")
        self.assertEqual(positions["4000500"].person_name, "Kirschke, Patrick")
        self.assertEqual(positions["4000500"].monthly_actuals, {"2026-05": "1000.00"})
        self.assertEqual(positions["8000500"].kind, "travel")
        self.assertEqual(positions["5000500"].kind, "other")
        self.assertEqual(positions["1826000500"].kind, "income")

    def test_rejects_missing_columns(self):
        path = self.export_path(rows=[], headers=["PSP-Element", "Fonds"])
        with self.assertRaisesMessage(ValueError, "Fehlende Spalten"):
            parse_gm_export(path)

    def test_rejects_multiple_psp_elements(self):
        rows = sample_rows() + [_row("4009999", 1.0, "81", psp="1/070000001")]
        with self.assertRaisesMessage(ValueError, "genau ein PSP-Element"):
            parse_gm_export(self.export_path(rows))

    def test_rejects_unknown_value_types(self):
        rows = sample_rows() + [_row("4009999", 1.0, "XX")]
        with self.assertRaisesMessage(ValueError, "XX"):
            parse_gm_export(self.export_path(rows))

    def test_contract_texts_merge_periods_and_apply_storno(self):
        periods, percentage, weekly_hours = parse_contract_texts([
            "AV; 01.10.23 - 30.04.26; 100%; Storno zum 31.12.24",
            "AV  01.01.2026 - 30.04.2026, 100 %",
        ])
        self.assertEqual(
            periods,
            [[date(2023, 10, 1), date(2024, 12, 31)], [date(2026, 1, 1), date(2026, 4, 30)]],
        )
        self.assertEqual(percentage, Decimal("100"))
        self.assertIsNone(weekly_hours)

        periods, _, weekly_hours = parse_contract_texts(["SHK-oA;  Juni 24 - Dez.24; 8 SWS"])
        self.assertEqual(periods, [[date(2024, 6, 1), date(2024, 12, 31)]])
        self.assertEqual(weekly_hours, Decimal("8"))


class NameMatchingTests(TestCase):
    def test_name_key_ignores_order_umlauts_and_punctuation(self):
        self.assertEqual(name_key("Wißkirchen, Lionel"), name_key("Lionel Wisskirchen"))
        self.assertEqual(name_key("El Ahmad, Mohamad"), name_key("Mohamad El-Ahmad"))

    def test_stored_mapping_wins_over_name_comparison(self):
        member = StaffMember.objects.create(first_name="Erika", last_name="Mustermann")
        SAPPersonMapping.objects.create(sap_name="Muster-Frau, Erika", staff_member=member)
        self.assertEqual(match_staff_member("Erika Muster-Frau", StaffMember.objects.all()), member)


class ReconciliationTestBase(TemporaryExportMixin, TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            acronym="GMTEST",
            start_date=date(2025, 1, 1),
            end_date=date(2027, 12, 31),
            budget_total=Decimal("100000.00"),
        )
        self.fund = SAPFund.objects.create(fund_number=PSP, project=self.project)
        self.staff_item = StaffBudgetItem.objects.create(project=self.project, title="Personal", amount=Decimal("60000"))
        StaffBudgetItemEligibility.objects.create(budget_item=self.staff_item, eligible_employment="researcher")
        self.student_item = StaffBudgetItem.objects.create(project=self.project, title="Hilfskräfte", amount=Decimal("5000"))
        StaffBudgetItemEligibility.objects.create(budget_item=self.student_item, eligible_employment="student")
        self.travel_item = OtherBudgetItem.objects.create(project=self.project, title="Reisen", amount=Decimal("2000"))
        self.material_item = OtherBudgetItem.objects.create(project=self.project, title="Material", amount=Decimal("1000"))
        self.sap_import = import_gm_export(self.export_path())

    def check(self, reference):
        result = build_reconciliation(self.fund)
        return next(check for check in result.checks if check.position.reference == reference)


class GMImportTests(ReconciliationTestBase):
    def test_import_requires_matching_fund(self):
        self.fund.fund_number = "OTHER"
        self.fund.save()
        with self.assertRaisesMessage(ValueError, "kein SAP-Fonds"):
            import_gm_export(self.export_path())

    def test_reimport_replaces_positions_and_keeps_ignored(self):
        SAPIgnoredPosition.objects.create(fund=self.fund, reference="4000200")
        import_gm_export(self.export_path())

        self.assertEqual(self.fund.sap_imports.count(), 1)
        self.assertEqual(self.check("4000200").status, Status.IGNORED)


class CrossCheckTests(ReconciliationTestBase):
    def test_statuses_without_planning(self):
        self.assertEqual(self.check("4000100").status, Status.MISSING)
        self.assertEqual(self.check("4000300").status, Status.NEUTRAL)
        self.assertEqual(self.check("1826000001").status, Status.INFO)
        travel = self.check("8000001")
        self.assertEqual(travel.status, Status.MISSING)
        self.assertIn("keinem Sachmittelbudget", travel.findings[0])
        self.assertEqual(self.check("4000200").budget_item, self.student_item)

    def test_staff_matched_by_name_reports_differences(self):
        member = StaffMember.objects.create(first_name="Erika", last_name="Muster-Frau")
        employment = Employment.objects.create(
            staff_member=member, start_date=date(2026, 1, 1), end_date=date(2026, 6, 30),
            percentage=Decimal("50"),
        )
        EmploymentSalaries.objects.create(
            employment=employment, salary=Decimal("2900"), start_date=date(2026, 1, 1), end_date=date(2026, 6, 30),
        )
        StaffFundingAllocation.objects.create(
            employment=employment, budget_item=self.staff_item, percentage=Decimal("50"),
            start_date=date(2026, 1, 1), end_date=date(2026, 6, 30),
        )

        check = self.check("4000100")

        self.assertEqual(check.status, Status.MISMATCH)
        self.assertTrue(check.linked_by_name)
        self.assertIn("Geplant ohne SAP-Vertrag: 2026-04 – 2026-06", check.findings)
        self.assertEqual([month for month, _, _ in check.salary_mismatch_months], ["2026-01", "2026-02"])

    def test_other_position_matches_transaction_by_sap_id(self):
        OtherBudgetItemTransaction.objects.create(
            budget_item=self.travel_item, date=date(2026, 2, 10), amount=Decimal("400"), sap_id="8000001",
        )
        self.assertEqual(self.check("8000001").status, Status.MISMATCH)

        OtherBudgetItemTransaction.objects.filter(sap_id="8000001").update(amount=Decimal("480"))
        self.assertEqual(self.check("8000001").status, Status.OK)

    def test_unlinked_planner_transaction_is_reported(self):
        OtherBudgetItemTransaction.objects.create(
            budget_item=self.travel_item, date=date(2026, 1, 10), amount=Decimal("99"), sap_id="9999",
        )
        result = build_reconciliation(self.fund)
        self.assertEqual([t.sap_id for t in result.orphan_transactions], ["9999"])


class TransformTests(ReconciliationTestBase):
    def test_create_staff_planning_matches_sap(self):
        position = self.check("4000100").position
        member, allocations = create_staff_planning(
            position,
            budget_item=self.staff_item,
            category="researcher",
            percentage=Decimal("50"),
            first_name="Erika",
            last_name="Muster-Frau",
        )

        self.assertEqual(len(allocations), 1)
        self.assertEqual(allocations[0].sap_reference, "4000100")
        self.assertEqual((allocations[0].start_date, allocations[0].end_date), (date(2026, 1, 1), date(2026, 3, 31)))
        self.assertTrue(SAPPersonMapping.objects.filter(staff_member=member).exists())
        months = calculate_salary_for_allocation(allocations[0]).months
        self.assertEqual(months["2026-01"], Decimal("3000.00"))
        self.assertEqual(months["2026-03"], Decimal("3000.00"))  # forecast continues last SAP month
        self.assertEqual(self.check("4000100").status, Status.OK)

    def test_apply_salaries_corrects_partial_first_month_and_scales_share(self):
        member = StaffMember.objects.create(first_name="A", last_name="B")
        employment = Employment.objects.create(
            staff_member=member, start_date=date(2026, 1, 16), end_date=date(2026, 12, 31),
            percentage=Decimal("100"),
        )
        EmploymentSalaries.objects.create(
            employment=employment, salary=Decimal("5000"), start_date=date(2026, 1, 16), end_date=date(2026, 12, 31),
        )
        allocation = StaffFundingAllocation.objects.create(
            employment=employment, budget_item=self.staff_item, percentage=Decimal("50"),
            start_date=date(2026, 1, 16), end_date=date(2026, 6, 30),
        )

        skipped = apply_monthly_salaries(
            allocation, {"2026-01": Decimal("1280.00"), "2026-02": Decimal("2600.00"), "2026-08": Decimal("1.00")}
        )

        self.assertEqual(skipped, ["2026-08"])
        months = calculate_salary_for_allocation(allocation).months
        self.assertEqual(months["2026-01"], Decimal("1280.00"))
        self.assertEqual(months["2026-02"], Decimal("2600.00"))
        self.assertEqual(months["2026-03"], Decimal("2500.00"))
        salaries = list(employment.employmentsalaries_set.order_by("start_date"))
        self.assertEqual(salaries[0].start_date, date(2026, 1, 16))
        self.assertEqual(salaries[-1].end_date, date(2026, 12, 31))

    def test_create_transaction_uses_total_and_reference(self):
        planner_transaction = create_transaction(self.check("10000001").position, self.material_item)
        self.assertEqual(planner_transaction.amount, Decimal("200.00"))
        self.assertEqual(planner_transaction.sap_id, "10000001")
        self.assertEqual(self.check("10000001").status, Status.OK)

    def test_uncleared_reservation_is_not_planned_but_flagged(self):
        position = self.check("8000001").position
        # Reservation 500 € (value type 81) never reduced, 480 € paid.
        position.bookings = [b for b in position.bookings if not (b["value_type"] == "81" and Decimal(b["amount"]) < 0)]
        position.commitment = Decimal("500.00")

        self.assertEqual(position.planned_amount, Decimal("480.00"))
        self.assertEqual(position.uncleared_commitment, Decimal("500.00"))

    def test_reservation_without_payment_is_planned(self):
        position = self.check("8000001").position
        position.actual = Decimal("0.00")
        position.bookings = [b for b in position.bookings if b["value_type"] == "81" and Decimal(b["amount"]) > 0]
        position.commitment = Decimal("500.00")

        self.assertEqual(position.planned_amount, Decimal("500.00"))
        self.assertEqual(position.uncleared_commitment, Decimal("0.00"))

    def test_uncleared_reservation_finding(self):
        position = self.check("8000001").position
        position.bookings = [b for b in position.bookings if not (b["value_type"] == "81" and Decimal(b["amount"]) < 0)]
        position.commitment = Decimal("500.00")
        position.save()
        create_transaction(position, self.travel_item)

        check = self.check("8000001")

        self.assertEqual(check.planned_total, Decimal("480.00"))
        self.assertEqual(check.status, Status.MISMATCH)
        self.assertIn("nicht vollständig ausgebucht", check.findings[0])


@override_settings(SAP_ENABLED=False, SAP_GM_IMPORT_ENABLED=True, STORAGES=STATIC_STORAGE)
class ReconciliationViewTests(ReconciliationTestBase):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(username="sap", password="x", is_staff=True)
        self.client.force_login(self.user)

    def test_upload_imports_export_and_redirects(self):
        content = Path(self.export_path()).read_bytes()
        upload = SimpleUploadedFile("export.xlsx", content)
        response = self.client.post(reverse("sap_integration:upload_export"), {"export_file": upload})
        self.assertRedirects(response, reverse("sap_integration:reconciliation", args=[self.fund.id]))
        self.assertEqual(self.fund.sap_imports.get().file_name, "export.xlsx")

    def test_upload_reports_invalid_file(self):
        upload = SimpleUploadedFile("broken.xlsx", b"not an excel file")
        response = self.client.post(reverse("sap_integration:upload_export"), {"export_file": upload}, follow=True)
        self.assertContains(response, "konnte nicht als SAP-Export gelesen werden")

    def test_reconciliation_page_lists_open_positions(self):
        response = self.client.get(reverse("sap_integration:reconciliation", args=[self.fund.id]))
        self.assertContains(response, "Muster-Frau, Erika")
        self.assertContains(response, "Fehlt in Planung")
        self.assertNotContains(response, "TEST_MR_01")

    def test_ignore_and_mapping_actions(self):
        position = self.check("8000001").position
        url = reverse("sap_integration:position_detail", args=[self.fund.id, position.id])
        self.client.post(url, {"action": "ignore", "note": "privat"})
        self.assertEqual(SAPIgnoredPosition.objects.get(reference="8000001").note, "privat")
        self.client.post(url, {"action": "unignore"})
        self.assertFalse(SAPIgnoredPosition.objects.exists())

        self.client.post(
            reverse("sap_integration:cost_type_mapping", args=[self.fund.id]),
            {"cost_type": "7464", "target": f"other:{self.travel_item.id}"},
        )
        self.assertEqual(SAPCostTypeMapping.objects.get(cost_type="7464").other_budget_item, self.travel_item)
        self.client.post(url, {"action": "create_transaction", "budget_item": self.travel_item.id})
        self.assertEqual(self.check("8000001").status, Status.OK)

    def test_position_detail_and_staff_transform(self):
        position = self.check("4000100").position
        url = reverse("sap_integration:position_detail", args=[self.fund.id, position.id])
        self.assertContains(self.client.get(url), "In Planung übernehmen")

        response = self.client.post(url, {
            "action": "transform_staff",
            "first_name": "Erika",
            "last_name": "Muster-Frau",
            "budget_item": self.staff_item.id,
            "category": "researcher",
            "percentage": "50",
            "create_salaries": "on",
            "remember_name": "on",
        })
        self.assertRedirects(response, reverse("sap_integration:reconciliation", args=[self.fund.id]))
        self.assertEqual(self.check("4000100").status, Status.OK)

    def test_warnings_page_mentions_open_positions(self):
        response = self.client.get(reverse("warnings"))
        self.assertContains(response, "SAP-Abgleich: GMTEST")

    def test_overview_without_webgui_shows_only_project_exports(self):
        response = self.client.get(reverse("sap_integration:overview"))
        self.assertContains(response, "Projektexporte (Grants Management)")
        self.assertNotContains(response, "Geschäftsjahre")
        self.assertEqual(self.client.get(reverse("sap_integration:overview_year", args=[2026])).status_code, 404)


@override_settings(SAP_ENABLED=True, SAP_GM_IMPORT_ENABLED=False, STORAGES=STATIC_STORAGE)
class GMImportDisabledTests(ReconciliationTestBase):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(username="sap", password="x", is_staff=True)
        self.client.force_login(self.user)

    def test_webgui_overview_hides_project_exports(self):
        response = self.client.get(reverse("sap_integration:overview"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Projektexporte")

    def test_project_export_pages_and_warnings_are_disabled(self):
        upload = SimpleUploadedFile("export.xlsx", Path(self.export_path()).read_bytes())
        self.assertEqual(
            self.client.post(reverse("sap_integration:upload_export"), {"export_file": upload}).status_code, 404
        )
        self.assertEqual(
            self.client.get(reverse("sap_integration:reconciliation", args=[self.fund.id])).status_code, 404
        )
        self.assertNotContains(self.client.get(reverse("warnings")), "SAP-Abgleich")

    def test_import_command_is_disabled(self):
        with self.assertRaisesMessage(CommandError, "SAP_GM_IMPORT_ENABLED"):
            call_command("import_sap_export", str(self.export_path()))
