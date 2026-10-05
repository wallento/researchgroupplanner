"""Cross-check of an imported SAP project export against the planning."""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from dateutil.relativedelta import relativedelta

from projects.models import OtherBudgetItemTransaction, StaffBudgetItem
from projects.utils import calculate_salary_for_allocation
from sap_integration.models import (
    SAPCostTypeMapping,
    SAPIgnoredPosition,
    SAPPositionKind,
)
from sap_integration.names import match_staff_member
from staffing.models import StaffFundingAllocation, StaffMember


SALARY_TOLERANCE = Decimal("1.00")
AMOUNT_TOLERANCE = Decimal("0.01")
OBLIGO_TOLERANCE = Decimal("1.00")
# Obligo and planned remaining cost differ notably above this amount or share.
OBLIGO_DIFFERENCE = Decimal("1000.00")
OBLIGO_DIFFERENCE_SHARE = Decimal("0.10")
FULL_TIME_WEEKLY_HOURS = Decimal("40")
STUDENT_CONTRACT_TYPES = {"SHK", "WHK", "TUT"}


class Status:
    OK = "ok"
    MISMATCH = "mismatch"
    MISSING = "missing"
    NEUTRAL = "neutral"
    INFO = "info"
    IGNORED = "ignored"


STATUS_LABELS = {
    Status.OK: "Übereinstimmend",
    Status.MISMATCH: "Abweichung",
    Status.MISSING: "Fehlt in Planung",
    Status.NEUTRAL: "Ohne Betrag",
    Status.INFO: "Information",
    Status.IGNORED: "Ignoriert",
}
OPEN_STATUSES = {Status.MISMATCH, Status.MISSING}


@dataclass
class PositionCheck:
    position: object
    status: str = Status.OK
    findings: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    allocations: list = field(default_factory=list)
    transactions: list = field(default_factory=list)
    linked_by_name: bool = False
    staff_member: object = None
    budget_item: object = None
    ignored: object = None
    planned_months: dict = field(default_factory=dict)
    salary_mismatch_months: list = field(default_factory=list)
    # Open staff Obligo not reflected in the planning: [{"text", "amount"}].
    obligo_issues: list = field(default_factory=list)

    @property
    def status_label(self):
        return STATUS_LABELS[self.status]

    @property
    def is_open(self):
        return self.status in OPEN_STATUSES

    @property
    def planned_total(self):
        if self.position.kind == SAPPositionKind.STAFF:
            return sum(self.planned_months.values(), Decimal("0.00"))
        return sum((t.amount for t in self.transactions), Decimal("0.00"))


@dataclass
class Reconciliation:
    fund: object
    sap_import: object
    checks: list
    cost_types: list
    orphan_transactions: list
    orphan_allocations: list

    @property
    def open_checks(self):
        return [check for check in self.checks if check.is_open]


def references(value):
    return {part.strip() for part in (value or "").split(",") if part.strip()}


def add_reference(value, reference):
    existing = references(value)
    if reference in existing:
        return value
    return ", ".join(sorted(existing | {reference}))


def build_reconciliation(fund):
    sap_import = fund.sap_imports.order_by("-imported_at").first()
    if sap_import is None or fund.project_id is None:
        return None
    project = fund.project
    positions = list(sap_import.positions.all())
    position_refs = {position.reference for position in positions}
    ignored = {entry.reference: entry for entry in SAPIgnoredPosition.objects.filter(fund=fund)}
    mappings = {
        mapping.cost_type: mapping
        for mapping in SAPCostTypeMapping.objects.filter(fund=fund).select_related(
            "staff_budget_item", "other_budget_item"
        )
    }

    allocations = list(
        StaffFundingAllocation.objects.filter(budget_item__project=project).select_related(
            "employment__staff_member", "budget_item"
        )
    )
    transactions = list(
        OtherBudgetItemTransaction.objects.filter(budget_item__project=project).select_related("budget_item")
    )
    staff_members = list(StaffMember.objects.all())
    staff_budget_items = list(StaffBudgetItem.objects.filter(project=project).prefetch_related("staffbudgetitemeligibility_set"))

    checks = []
    for position in positions:
        check = PositionCheck(position=position, ignored=ignored.get(position.reference))
        if position.is_transfer:
            check.status = Status.INFO
            check.notes.append("Umbuchung zwischen Fonds – keine eigene Planung, nicht als Gehalt übernehmen.")
        elif position.kind == SAPPositionKind.STAFF:
            _check_staff(check, allocations, staff_members, staff_budget_items, mappings)
        elif position.kind == SAPPositionKind.INCOME:
            check.status = Status.INFO
        else:
            _check_other(check, transactions, mappings)
        if check.ignored and check.status != Status.INFO:
            check.status = Status.IGNORED
        checks.append(check)

    linked_transaction_ids = {t.id for check in checks for t in check.transactions}
    orphan_transactions = [
        t for t in transactions
        if t.id not in linked_transaction_ids and (not t.sap_id or not references(t.sap_id) & position_refs)
    ]
    linked_allocation_ids = {a.id for check in checks for a in check.allocations}
    cutoff = sap_import.last_booking or date.today()
    orphan_allocations = [
        a for a in allocations
        if a.id not in linked_allocation_ids and a.start_date <= cutoff
    ]

    return Reconciliation(
        fund=fund,
        sap_import=sap_import,
        checks=checks,
        cost_types=_cost_type_rows(sap_import, mappings),
        orphan_transactions=orphan_transactions,
        orphan_allocations=orphan_allocations,
    )


def planner_category(position):
    return "student" if position.contract_type in STUDENT_CONTRACT_TYPES else "researcher"


def expected_percentage(position):
    if position.contract_type in STUDENT_CONTRACT_TYPES and position.weekly_hours:
        return (position.weekly_hours / FULL_TIME_WEEKLY_HOURS * 100).quantize(Decimal("0.01"))
    return position.percentage


def contract_periods(position):
    return [(date.fromisoformat(start), date.fromisoformat(end)) for start, end in position.contract_periods]


def default_staff_budget_item(position, staff_budget_items, mappings):
    category = planner_category(position)
    for item in staff_budget_items:
        if any(e.eligible_employment == category for e in item.staffbudgetitemeligibility_set.all()):
            return item
    mapping = mappings.get(position.cost_type)
    if mapping and mapping.staff_budget_item:
        return mapping.staff_budget_item
    return staff_budget_items[0] if staff_budget_items else None


def _check_staff(check, allocations, staff_members, staff_budget_items, mappings):
    position = check.position
    check.allocations = [a for a in allocations if position.reference in references(a.sap_reference)]
    if position.person_name:
        check.staff_member = match_staff_member(position.person_name, staff_members)
    check.budget_item = default_staff_budget_item(position, staff_budget_items, mappings)

    if not check.allocations and check.staff_member:
        periods = contract_periods(position)
        candidates = [
            a for a in allocations
            if a.employment.staff_member_id == check.staff_member.id and not references(a.sap_reference)
        ]
        if periods:
            candidates = [a for a in candidates if any(_overlaps(a, start, end) for start, end in periods)]
        check.allocations = candidates
        check.linked_by_name = bool(candidates)

    if position.total == 0 and not position.monthly_actuals and not check.allocations:
        check.status = Status.NEUTRAL
        return
    if not check.allocations:
        check.status = Status.MISSING
        if position.commitment > OBLIGO_TOLERANCE:
            check.obligo_issues.append({
                "text": f"Position fehlt in der Planung, offenes Obligo {position.commitment:,.2f} €.",
                "amount": position.commitment,
            })
        if not position.person_name:
            check.findings.append("Keine Person erkennbar – bitte manuell verknüpfen oder ignorieren.")
        elif check.staff_member is None:
            check.findings.append(f"Kein Mitarbeiter für „{position.person_name}“ gefunden.")
        return

    if check.staff_member is None:
        check.staff_member = check.allocations[0].employment.staff_member
    if check.linked_by_name:
        check.notes.append("Automatisch über den Namen zugeordnet.")

    for allocation in check.allocations:
        for month, amount in calculate_salary_for_allocation(allocation).months.items():
            check.planned_months[month] = check.planned_months.get(month, Decimal("0.00")) + Decimal(amount)

    unplanned_months = _compare_periods(check)
    _compare_percentage(check)
    _compare_salaries(check)
    _compare_obligo(check, unplanned_months)
    check.status = Status.MISMATCH if check.findings else Status.OK


def _compare_periods(check):
    periods = contract_periods(check.position)
    if not periods:
        return
    sap_months = set()
    for start, end in periods:
        sap_months |= set(_months(start, end))
    planned_months = set()
    for allocation in check.allocations:
        planned_months |= set(_months(allocation.start_date, allocation.end_date or allocation.employment.end_date))
    missing = sorted(sap_months - planned_months)
    extra = sorted(planned_months - sap_months)
    if missing:
        check.findings.append(f"SAP-Vertrag nicht geplant: {_format_month_ranges(missing)}")
    if extra:
        check.findings.append(f"Geplant ohne SAP-Vertrag: {_format_month_ranges(extra)}")
    return missing


def _compare_percentage(check):
    expected = expected_percentage(check.position)
    if expected is None:
        return
    planned = {Decimal(a.percentage) for a in check.allocations}
    if planned != {expected}:
        planned_text = ", ".join(f"{p:g} %" for p in sorted(planned))
        check.findings.append(f"Umfang laut SAP {expected:g} %, geplant {planned_text}.")


def _compare_salaries(check):
    shifted = []
    for month, actual in sorted(check.position.monthly_actuals.items()):
        actual = Decimal(actual)
        planned = check.planned_months.get(month, Decimal("0.00"))
        if abs(actual - planned) > SALARY_TOLERANCE:
            check.salary_mismatch_months.append((month, actual, planned))
    if check.salary_mismatch_months:
        # Payroll sometimes moves amounts between funds; if all funds together
        # match the planning, the month is fine for this person.
        sap_by_month, planned_by_month = _all_funds_months(check.position.reference)
        for month, actual, planned in list(check.salary_mismatch_months):
            if abs(sap_by_month.get(month, Decimal("0")) - planned_by_month.get(month, Decimal("0"))) <= SALARY_TOLERANCE:
                check.salary_mismatch_months.remove((month, actual, planned))
                shifted.append(f"{month} ({actual - planned:+,.2f} €)")
    if shifted:
        check.notes.append(
            f"Verschiebung zwischen Fonds, Summe über alle Fonds stimmt: {', '.join(shifted)}."
        )
    if check.salary_mismatch_months:
        sample = ", ".join(
            f"{month}: SAP {actual:,.2f} € / Plan {planned:,.2f} €"
            for month, actual, planned in check.salary_mismatch_months[:3]
        )
        more = len(check.salary_mismatch_months) - 3
        if more > 0:
            sample += f" (+{more} weitere)"
        check.findings.append(f"Gehaltsabweichung in {len(check.salary_mismatch_months)} Monat(en): {sample}")


def _all_funds_months(reference):
    """({month: SAP actual}, {month: planned}) for an SAP reference across all funds and projects."""
    from sap_integration.models import SAPPosition

    sap = {}
    latest_imports = {}
    for position in SAPPosition.objects.filter(reference=reference, kind=SAPPositionKind.STAFF).select_related(
        "sap_import"
    ).order_by("-sap_import__imported_at"):
        fund_id = position.sap_import.fund_id
        if latest_imports.setdefault(fund_id, position.sap_import_id) != position.sap_import_id:
            continue
        for month, amount in position.monthly_actuals.items():
            sap[month] = sap.get(month, Decimal("0")) + Decimal(amount)
    planned = {}
    for allocation in StaffFundingAllocation.objects.filter(sap_reference__contains=reference).select_related("employment"):
        if reference not in references(allocation.sap_reference):
            continue
        for month, amount in calculate_salary_for_allocation(allocation).months.items():
            planned[month] = planned.get(month, Decimal("0")) + Decimal(amount)
    return sap, planned


def _compare_obligo(check, unplanned_months):
    """Report open staff Obligo that the planning does not reflect.

    - Obligo left after the contract is fully paid: the reservation was not
      cleared in SAP (like travel reservations); it is not planned.
    - Contract months reserved in SAP but not planned: estimated amount.
    - Otherwise a clear difference between Obligo and planned remaining cost.
    """
    position = check.position
    commitment = position.commitment
    if commitment <= OBLIGO_TOLERANCE:
        return
    periods = contract_periods(position)
    contract_end = max((end for _, end in periods), default=None)
    paid_months = sorted(position.monthly_actuals)
    last_paid = paid_months[-1] if paid_months else ""

    if contract_end and last_paid and last_paid >= contract_end.strftime("%Y-%m"):
        text = (
            f"Obligo nach Vertragsende nicht ausgebucht: {commitment:,.2f} € – bitte in SAP ausbuchen lassen; "
            "es wird nicht geplant."
        )
        check.findings.append(text)
        check.obligo_issues.append({"text": text, "amount": commitment})
        return

    future_unplanned = [month for month in unplanned_months if month > last_paid]
    if future_unplanned:
        rate = next((Decimal(position.monthly_actuals[m]) for m in reversed(paid_months)
                     if Decimal(position.monthly_actuals[m]) > 0), None)
        if rate is None:
            unpaid = [m for start, end in periods for m in _months(start, end) if m > last_paid]
            rate = commitment / len(unpaid) if unpaid else commitment
        estimate = min(commitment, (rate * len(future_unplanned)).quantize(Decimal("0.01")))
        if future_unplanned == unplanned_months:
            # Replaces the generic period finding, which covers the same months.
            check.findings.remove(f"SAP-Vertrag nicht geplant: {_format_month_ranges(unplanned_months)}")
        text = (
            f"Vertrag in SAP reserviert, nicht geplant: {_format_month_ranges(future_unplanned)} "
            f"(≈ {estimate:,.2f} € von {commitment:,.2f} € Obligo)."
        )
        check.findings.append(text)
        check.obligo_issues.append({"text": text, "amount": estimate})
        return

    planned_future = sum(
        (amount for month, amount in check.planned_months.items() if month > last_paid), Decimal("0.00"),
    )
    if abs(planned_future - commitment) > max(OBLIGO_DIFFERENCE, commitment * OBLIGO_DIFFERENCE_SHARE):
        text = (
            f"Offenes Obligo {commitment:,.2f} € weicht von den geplanten Restkosten "
            f"nach {last_paid or 'Beginn'} ab ({planned_future:,.2f} €)."
        )
        check.findings.append(text)
        check.obligo_issues.append({"text": text, "amount": commitment - planned_future})


def _check_other(check, transactions, mappings):
    position = check.position
    check.transactions = [t for t in transactions if position.reference in references(t.sap_id)]
    mapping = mappings.get(position.cost_type)
    check.budget_item = mapping.other_budget_item if mapping else None

    if not check.transactions:
        check.status = Status.NEUTRAL if position.total == 0 else Status.MISSING
        if check.status == Status.MISSING and check.budget_item is None:
            check.findings.append(f"E/A-Art {position.cost_type} ist keinem Sachmittelbudget zugeordnet.")
        return

    planned = check.planned_total
    source = "Ist + offene Bestellung" if position.actual and position.planned_amount != position.actual else (
        "Ist" if position.actual else "Obligo, noch kein Ist"
    )
    if abs(planned - position.planned_amount) > AMOUNT_TOLERANCE:
        check.findings.append(f"SAP {position.planned_amount:,.2f} € ({source}), geplant {planned:,.2f} €.")
    if position.uncleared_commitment:
        check.findings.append(
            f"Mittelreservierung nicht vollständig ausgebucht: Obligo {position.uncleared_commitment:,.2f} € "
            f"trotz Ist {position.actual:,.2f} € – bitte in SAP ausbuchen lassen; die Reservierung wird nicht geplant."
        )
    if check.budget_item and any(t.budget_item_id != check.budget_item.id for t in check.transactions):
        check.findings.append(f"Geplant auf anderem Budget als E/A-Art {position.cost_type} ({check.budget_item.title}).")
    check.status = Status.MISMATCH if check.findings else Status.OK


def _cost_type_rows(sap_import, mappings):
    rows = []
    for cost_type, values in sap_import.cost_type_values().items():
        mapping = mappings.get(cost_type)
        budget_item = mapping.budget_item() if mapping else None
        rows.append({
            "cost_type": cost_type,
            "budget": values["budget"],
            "actual": values["actual"],
            "commitment": values["commitment"],
            "remaining": values["budget"] - values["actual"] - values["commitment"],
            "mapping": mapping,
            "budget_item": budget_item,
        })
    return rows


def _overlaps(allocation, start, end):
    allocation_end = allocation.end_date or allocation.employment.end_date
    return allocation.start_date <= end and start <= allocation_end


def _months(start, end):
    current = start.replace(day=1)
    while current <= end:
        yield current.strftime("%Y-%m")
        current += relativedelta(months=1)


def _format_month_ranges(months):
    ranges = []
    for month in months:
        year, number = map(int, month.split("-"))
        if ranges:
            last_year, last_number = map(int, ranges[-1][1].split("-"))
            if (year, number) == (last_year + (last_number == 12), last_number % 12 + 1):
                ranges[-1][1] = month
                continue
        ranges.append([month, month])
    return ", ".join(start if start == end else f"{start} – {end}" for start, end in ranges)


def sap_actuals_by_year():
    """Actual costs per calendar year from the latest import of each project fund (without income).

    Staff payroll counts by salary month, other bookings by booking date.
    """
    from projects.models import SAPFund
    from sap_integration.models import ACTUAL_VALUE_TYPES

    totals = {}
    for fund in SAPFund.objects.filter(project__isnull=False, sap_imports__isnull=False).distinct():
        sap_import = fund.sap_imports.order_by("-imported_at").first()
        for position in sap_import.positions.exclude(kind=SAPPositionKind.INCOME):
            if position.kind == SAPPositionKind.STAFF and position.monthly_actuals:
                entries = ((month[:4], amount) for month, amount in position.monthly_actuals.items())
            else:
                entries = (
                    (b["date"][:4], b["amount"]) for b in position.bookings
                    if b.get("value_type") in ACTUAL_VALUE_TYPES and b.get("date")
                )
            for year, amount in entries:
                totals[year] = totals.get(year, Decimal("0.00")) + Decimal(amount)
    return {year: amount.quantize(Decimal("0.01")) for year, amount in totals.items() if amount}
