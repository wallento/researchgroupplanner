from datetime import date
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from projects.models import OtherBudgetItem, SAPFund, StaffBudgetItem
from staffing.models import StaffMember


# FMM-Werttyp of manual transfer postings between funds.
TRANSFER_VALUE_TYPE = "66"
# FMM-Werttyp of Mittelreservierungen (not reduced by the actual payment).
RESERVATION_VALUE_TYPE = "81"
# FMM-Werttypen of actual bookings (payments, transfers).
ACTUAL_VALUE_TYPES = {"66", "99", "Z1"}


class SAPPositionKind(models.TextChoices):
    STAFF = "staff", "Personal"
    TRAVEL = "travel", "Reise"
    OTHER = "other", "Sachmittel"
    INCOME = "income", "Einnahme"


class SAPImport(models.Model):
    """Latest imported project export (GM_E_4GBA) for one SAP fund."""

    fund = models.ForeignKey(SAPFund, on_delete=models.CASCADE, related_name="sap_imports")
    file_name = models.CharField("Datei", max_length=255)
    imported_at = models.DateTimeField("Importiert am", auto_now_add=True)
    imported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    row_count = models.PositiveIntegerField("Buchungszeilen", default=0)
    first_booking = models.DateField(null=True, blank=True)
    last_booking = models.DateField(null=True, blank=True)
    # {cost_type: {"budget": "…", "actual": "…", "commitment": "…"}} as decimal strings.
    cost_types = models.JSONField(default=dict)

    class Meta:
        ordering = ["-imported_at"]
        verbose_name = "SAP-Import"
        verbose_name_plural = "SAP-Importe"

    def __str__(self):
        return f"{self.fund.fund_number} ({self.imported_at:%d.%m.%Y %H:%M})"

    def cost_type_values(self):
        return {
            cost_type: {key: Decimal(value) for key, value in values.items()}
            for cost_type, values in self.cost_types.items()
        }


class SAPPosition(models.Model):
    """One SAP document chain (reservation, order, travel, …) with its bookings."""

    sap_import = models.ForeignKey(SAPImport, on_delete=models.CASCADE, related_name="positions")
    reference = models.CharField("Referenzbelegnummer", max_length=50)
    kind = models.CharField(max_length=20, choices=SAPPositionKind.choices)
    cost_type = models.CharField("E/A-Art", max_length=50)
    title = models.CharField("Bezeichnung", max_length=255, blank=True)
    description = models.TextField("Text", blank=True)
    person_name = models.CharField("Person", max_length=200, blank=True)
    contract_type = models.CharField("Vertragsart", max_length=20, blank=True)
    # [["2023-10-01", "2024-12-31"], …] parsed from the reservation texts.
    contract_periods = models.JSONField(default=list)
    percentage = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    weekly_hours = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    first_date = models.DateField(null=True, blank=True)
    last_date = models.DateField(null=True, blank=True)
    actual = models.DecimalField("Ist", max_digits=12, decimal_places=2, default=Decimal("0.00"))
    commitment = models.DecimalField("Obligo", max_digits=12, decimal_places=2, default=Decimal("0.00"))
    # {"2024-01": "5445.01"} payroll per salary month for staff positions.
    monthly_actuals = models.JSONField(default=dict)
    # [{"date", "value_type", "amount", "text"}] for display.
    bookings = models.JSONField(default=list)

    class Meta:
        ordering = ["kind", "person_name", "first_date", "reference"]
        unique_together = [("sap_import", "reference")]
        verbose_name = "SAP-Position"
        verbose_name_plural = "SAP-Positionen"

    def __str__(self):
        return f"{self.reference} {self.title}".strip()

    @property
    def total(self):
        return self.actual + self.commitment

    @property
    def actual_date(self):
        """Date of the last actual booking (e.g. the invoice payment), or None if nothing was paid."""
        dates = [
            b["date"] for b in self.bookings
            if b.get("value_type") in ACTUAL_VALUE_TYPES and b.get("date") and Decimal(b["amount"])
        ]
        return date.fromisoformat(max(dates)) if dates else None

    @property
    def reservation_commitment(self):
        """Commitment from Mittelreservierungen (FMM-Werttyp 81), e.g. travel reservations."""
        return sum(
            (Decimal(b["amount"]) for b in self.bookings if b.get("value_type") == RESERVATION_VALUE_TYPE),
            Decimal("0.00"),
        )

    @property
    def planned_amount(self):
        """Amount the planning should carry.

        Orders (requisitions/purchase orders) are reduced by SAP with each
        invoice, so their open commitment is still to come. A Mittelreservierung
        is not reduced by the payment: once anything was paid only the actual
        counts and a remaining reservation is reported (uncleared_commitment).
        """
        reservation = self.reservation_commitment
        order_commitment = self.commitment - reservation
        return self.actual + order_commitment + (Decimal("0.00") if self.actual else reservation)

    @property
    def uncleared_commitment(self):
        """Reservation still open although actual bookings exist (not cleared in SAP)."""
        reservation = self.reservation_commitment
        return reservation if self.actual and reservation else Decimal("0.00")

    @property
    def is_transfer(self):
        """Staff cost transfer between funds (Umbuchung), not a contract of its own."""
        return (
            self.kind == SAPPositionKind.STAFF
            and not self.person_name
            and not self.contract_periods
            and bool(self.bookings)
            and all(booking["value_type"] == TRANSFER_VALUE_TYPE for booking in self.bookings)
        )


class SAPCostTypeMapping(models.Model):
    """Maps an SAP E/A-Art of a fund onto a planner budget item."""

    fund = models.ForeignKey(SAPFund, on_delete=models.CASCADE, related_name="cost_type_mappings")
    cost_type = models.CharField("E/A-Art", max_length=50)
    staff_budget_item = models.ForeignKey(
        StaffBudgetItem,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        verbose_name="Personalbudget",
    )
    other_budget_item = models.ForeignKey(
        OtherBudgetItem,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        verbose_name="Sachmittelbudget",
    )

    class Meta:
        ordering = ["fund", "cost_type"]
        unique_together = [("fund", "cost_type")]
        verbose_name = "SAP-E/A-Art-Zuordnung"
        verbose_name_plural = "SAP-E/A-Art-Zuordnungen"

    def clean(self):
        super().clean()
        if self.staff_budget_item_id and self.other_budget_item_id:
            raise ValidationError("Bitte höchstens ein Budget angeben.")
        project_id = self.fund.project_id if self.fund_id else None
        for item in (self.staff_budget_item, self.other_budget_item):
            if item is not None and item.project_id != project_id:
                raise ValidationError("Das Budget muss zum Projekt des SAP-Fonds gehören.")

    def budget_item(self):
        return self.staff_budget_item or self.other_budget_item

    def __str__(self):
        return f"{self.fund.fund_number} {self.cost_type} → {self.budget_item() or '–'}"


class SAPPersonMapping(models.Model):
    """Remembers which staff member an SAP person name refers to."""

    name_key = models.CharField(max_length=200, unique=True)
    sap_name = models.CharField("Name in SAP", max_length=200)
    staff_member = models.ForeignKey(
        StaffMember,
        on_delete=models.CASCADE,
        related_name="sap_name_mappings",
        verbose_name="Mitarbeiter",
    )

    class Meta:
        ordering = ["sap_name"]
        verbose_name = "SAP-Namenszuordnung"
        verbose_name_plural = "SAP-Namenszuordnungen"

    def save(self, *args, **kwargs):
        from sap_integration.names import name_key

        self.name_key = name_key(self.sap_name)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.sap_name} → {self.staff_member}"


class SAPIgnoredPosition(models.Model):
    """SAP positions stashed as irrelevant; kept across re-imports."""

    fund = models.ForeignKey(SAPFund, on_delete=models.CASCADE, related_name="ignored_positions")
    reference = models.CharField("Referenzbelegnummer", max_length=50)
    note = models.CharField("Notiz", max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    class Meta:
        unique_together = [("fund", "reference")]
        verbose_name = "Ignorierte SAP-Position"
        verbose_name_plural = "Ignorierte SAP-Positionen"

    def __str__(self):
        return f"{self.fund.fund_number} {self.reference}"
