from decimal import Decimal

from dateutil.relativedelta import relativedelta
from django.db import models
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator

from projects.models import AnnualPoolBudget, EmploymentCategories, Landesstelle, StaffBudgetItem

# Create your models here.
class StaffMember(models.Model):
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    email = models.EmailField(blank=True, default='')
    sap_business_partner = models.CharField(
        "SAP-Geschäftspartner",
        max_length=255,
        blank=True,
        default="",
        help_text=(
            "Optionaler Name aus dem SAP-Kontoauszug. Ohne Eintrag wird die "
            "Zuordnung automatisch über Vor- und Nachname versucht."
        ),
    )
    is_leadership = models.BooleanField(default=False, help_text="Person hat Leitungsfunktion (z.B. Professor)")
    status = models.CharField(max_length=20, choices={
        'in_hire': 'Einstellung',
        'active': 'Aktiv',
        'alumni': 'Alumni',
    }, default='active')

    def __str__(self):
        return f"{self.first_name} {self.last_name}"

# TV-L: years spent in a level before reaching the next one (Stufenlaufzeit).
LEVEL_WAITING_YEARS = {1: 1, 2: 2, 3: 3, 4: 4, 5: 5}
MAX_LEVEL = 6


def level_progression(start_level, level_start_date):
    """[(level, from_date)] from the start level up to the final level.

    A new level applies from the first of the month in which its waiting
    time is completed (TV-L §17 Abs. 1).
    """
    steps = [(start_level, level_start_date)]
    reached = level_start_date
    for level in range(start_level, MAX_LEVEL):
        reached += relativedelta(years=LEVEL_WAITING_YEARS[level])
        steps.append((level + 1, reached.replace(day=1)))
    return steps


class SalaryCategory(models.Model):
    """Fixed pay grade (e.g. TV-L E13); levels 1-5 within it determine the salary."""

    name = models.CharField("Name", max_length=50, unique=True)
    special_payment_rate = models.DecimalField(
        "Jahressonderzahlung (%)",
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Prozentsatz nach § 20 Abs. 2 TV-L, z. B. 46,47 für E 12–13.",
    )

    class Meta:
        ordering = ["name"]
        verbose_name = "Entgeltgruppe"
        verbose_name_plural = "Entgeltgruppen"

    def __str__(self):
        return self.name


class SalaryTable(models.Model):
    """TV-L pay table (Entgelttabelle) valid for a period."""

    name = models.CharField("Name", max_length=100)
    valid_from = models.DateField("Gültig ab")
    valid_until = models.DateField("Gültig bis", null=True, blank=True, help_text="Leer lassen, solange die Tabelle gilt.")

    class Meta:
        ordering = ["-valid_from"]
        verbose_name = "Entgelttabelle"
        verbose_name_plural = "Entgelttabellen"

    def __str__(self):
        until = f"{self.valid_until:%d.%m.%Y}" if self.valid_until else "offen"
        return f"{self.name} ({self.valid_from:%d.%m.%Y} – {until})"

    def clean(self):
        super().clean()
        if not self.valid_from:
            return
        if self.valid_until and self.valid_until < self.valid_from:
            raise ValidationError("„Gültig bis“ darf nicht vor „Gültig ab“ liegen.")
        overlapping = SalaryTable.objects.exclude(pk=self.pk).filter(
            models.Q(valid_until__isnull=True) | models.Q(valid_until__gte=self.valid_from)
        )
        if self.valid_until:
            overlapping = overlapping.filter(valid_from__lte=self.valid_until)
        if overlapping.exists():
            raise ValidationError(f"Der Gültigkeitszeitraum überschneidet sich mit {overlapping.first()}.")

    @classmethod
    def valid_on(cls, day):
        return cls.objects.filter(valid_from__lte=day).filter(
            models.Q(valid_until__isnull=True) | models.Q(valid_until__gte=day)
        ).first()


class SalaryTableEntry(models.Model):
    table = models.ForeignKey(SalaryTable, on_delete=models.CASCADE, related_name="entries")
    salary_category = models.ForeignKey(SalaryCategory, on_delete=models.PROTECT, verbose_name="Entgeltgruppe")
    level = models.PositiveSmallIntegerField(
        "Stufe", choices=[(level, f"Stufe {level}") for level in range(1, MAX_LEVEL + 1)],
    )
    gross = models.DecimalField(
        "Tabellenentgelt", max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Brutto ohne Arbeitgeberanteile, bei Vollzeit (100 %); Grundlage der Jahressonderzahlung.",
    )
    amount = models.DecimalField(
        "Monatliche Personalkosten", max_digits=10, decimal_places=2,
        help_text="Brutto inkl. Arbeitgeberanteile, bei Vollzeit (100 %).",
    )

    class Meta:
        ordering = ["salary_category__name", "level"]
        unique_together = [("table", "salary_category", "level")]
        verbose_name = "Tabellenwert"
        verbose_name_plural = "Tabellenwerte"

    def __str__(self):
        return f"{self.salary_category} Stufe {self.level}: {self.amount}"


def tariff_entry(salary_category, level, day):
    """Salary table entry valid on the day, or None."""
    return SalaryTableEntry.objects.filter(
        table=SalaryTable.valid_on(day), salary_category=salary_category, level=level,
    ).first()


def tariff_amount(salary_category, level, day):
    """Monthly full-time cost incl. employer contributions from the table valid on the day, or None."""
    entry = tariff_entry(salary_category, level, day)
    return entry.amount if entry else None


EMPLOYMENT_STATUSES = {
    "planned": "Geplant",
    "contract": "Vertrag",
    "rebook": "Umbuchung",
}


class Employment(models.Model):
    staff_member = models.ForeignKey(StaffMember, on_delete=models.CASCADE)
    start_date = models.DateField()
    end_date = models.DateField()
    percentage = models.DecimalField(max_digits=5, decimal_places=2)
    category = models.CharField(max_length=50, choices=EmploymentCategories, default='researcher')
    status = models.CharField("Status", max_length=20, choices=EMPLOYMENT_STATUSES, default="contract")
    salary_category = models.ForeignKey(
        SalaryCategory,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        verbose_name="Entgeltgruppe",
    )
    start_level = models.PositiveSmallIntegerField(
        "Stufe bei Beginn",
        choices=[(level, f"Stufe {level}") for level in range(1, MAX_LEVEL + 1)],
        null=True,
        blank=True,
    )
    level_start_date = models.DateField(
        "Stufenbeginn (fiktiv)",
        null=True,
        blank=True,
        help_text="Ab wann die Stufe bei Beginn gilt; daraus folgen die weiteren Stufenaufstiege.",
    )

    def __str__(self):
        return f"{self.staff_member} - {EmploymentCategories[self.category]} ({self.percentage}%)"
    
    def get_category(self):
        return EmploymentCategories[self.category]

    def clean(self):
        super().clean()
        if (self.start_level is None) != (self.level_start_date is None):
            raise ValidationError("Stufe bei Beginn und Stufenbeginn bitte gemeinsam angeben.")
        if self.level_start_date and self.start_date and self.level_start_date > self.start_date:
            raise ValidationError("Der Stufenbeginn darf nicht nach dem Beginn der Anstellung liegen.")

    def level_at(self, day):
        """TV-L level on a given day, or None without level data."""
        if self.start_level is None or self.level_start_date is None:
            return None
        current = None
        for level, since in level_progression(self.start_level, self.level_start_date):
            if since <= day:
                current = level
        return current

    def tariff_amount_at(self, day):
        """Monthly cost incl. employer contributions of this employment on a day, or None.

        The salary table holds full-time amounts; part-time scales by the
        employment percentage.
        """
        level = self.level_at(day)
        if level is None or self.salary_category_id is None:
            return None
        amount = tariff_amount(self.salary_category, level, day)
        if amount is None:
            return None
        return (amount * Decimal(self.percentage) / Decimal("100")).quantize(Decimal("0.01"))

    def tariff_gross_at(self, day):
        """Monthly gross table pay (without employer contributions) of this employment, or None."""
        level = self.level_at(day)
        if level is None or self.salary_category_id is None:
            return None
        entry = tariff_entry(self.salary_category, level, day)
        if entry is None or entry.gross is None:
            return None
        return (entry.gross * Decimal(self.percentage) / Decimal("100")).quantize(Decimal("0.01"))

    def level_steps(self):
        """[(level, from_date)] in effect during the employment."""
        if self.start_level is None or self.level_start_date is None:
            return []
        steps = []
        for level, since in level_progression(self.start_level, self.level_start_date):
            if since > self.end_date:
                break
            if since <= self.start_date:
                steps = [(level, self.start_date)]
            else:
                steps.append((level, since))
        return steps

class EmploymentSalaries(models.Model):
    employment = models.ForeignKey(Employment, on_delete=models.CASCADE, null=True, blank=True)
    salary = models.DecimalField(max_digits=10, decimal_places=2)
    is_exact_amount = models.BooleanField(
        "Exakter Betrag?",
        default=False,
        help_text=(
            "Der Betrag gilt vollständig für den angegebenen Teilzeitraum und "
            "wird nicht tagesanteilig berechnet."
        ),
    )
    start_date = models.DateField()
    end_date = models.DateField()

    def clean(self):
        super().clean()
        if not self.start_date or not self.end_date:
            return
        if self.end_date < self.start_date:
            raise ValidationError("Das Enddatum darf nicht vor dem Startdatum liegen.")
        if self.is_exact_amount and (
            self.start_date.year,
            self.start_date.month,
        ) != (
            self.end_date.year,
            self.end_date.month,
        ):
            raise ValidationError(
                "Ein exakter Betrag muss vollständig innerhalb eines "
                "Kalendermonats liegen."
            )

    def __str__(self):
        return f"{self.employment.staff_member} ({self.start_date} - {self.end_date}, € {self.salary})"
    
    def staff_member(self):
        return self.employment.staff_member


class StaffFundingAllocation(models.Model):
    employment = models.ForeignKey(Employment, on_delete=models.CASCADE)
    budget_item = models.ForeignKey(StaffBudgetItem, on_delete=models.CASCADE, null=True, blank=True)
    landesstelle = models.ForeignKey(Landesstelle, on_delete=models.CASCADE, null=True, blank=True)
    annual_pool_budget = models.ForeignKey(AnnualPoolBudget, on_delete=models.CASCADE, null=True, blank=True)
    is_universal = models.BooleanField("Universalprojekt", default=False)
    percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    sap_reference = models.CharField(
        "SAP-Referenz",
        max_length=50,
        blank=True,
        default="",
        help_text="Referenzbelegnummer der SAP-Mittelreservierung, z.B. 4000123",
    )
    is_rebooking = models.BooleanField(
        "Umgebucht", default=False, help_text="Durch eine abgeschlossene Umbuchung entstanden.",
    )

    def clean(self):
        super().clean()
        source_count = sum(
            (
                self.budget_item is not None,
                self.landesstelle is not None,
                self.annual_pool_budget is not None,
                self.is_universal,
            )
        )
        if source_count != 1:
            raise ValidationError(
                "Bitte genau eine Finanzierungsquelle angeben: Projektbudget, "
                "Landesstelle, Annual Pool Budget oder Universalprojekt."
            )
        if self.end_date and self.end_date < self.start_date:
            raise ValidationError("Das Enddatum darf nicht vor dem Startdatum liegen.")

        if self.annual_pool_budget_id:
            allocation_end = self.end_date or self.employment.end_date
            pool_year = self.annual_pool_budget.year
            if self.start_date.year != pool_year or allocation_end.year != pool_year:
                raise ValidationError(
                    "Zuordnungen auf ein Annual Pool Budget muessen vollstaendig innerhalb des zugehoerigen Jahres liegen."
                )

    def source(self):
        if self.budget_item:
            return self.budget_item
        if self.landesstelle:
            return self.landesstelle
        if self.annual_pool_budget:
            return self.annual_pool_budget
        return "Universalprojekt"

    def __str__(self):
        end_date = self.end_date if self.end_date else "offen"
        return f"{self.employment.staff_member} - {self.percentage}% ({self.start_date} - {end_date}) in {self.source()}"


class Rebooking(models.Model):
    """Open Umbuchung of (part of) an allocation to another budget.

    It leaves the allocation untouched until it is completed, so deleting it
    has no side effects; completing it splits the allocation accordingly.
    """

    allocation = models.ForeignKey(
        StaffFundingAllocation, on_delete=models.CASCADE, related_name="rebookings", verbose_name="Zuordnung",
    )
    budget_item = models.ForeignKey(StaffBudgetItem, on_delete=models.CASCADE, verbose_name="Nach (Personalbudget)")
    percentage = models.DecimalField(
        "Umfang (%)", max_digits=5, decimal_places=2, validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    start_date = models.DateField("Ab")
    end_date = models.DateField("Bis", null=True, blank=True, help_text="Leer lassen für das Ende der Zuordnung.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["start_date", "pk"]
        verbose_name = "Umbuchung"
        verbose_name_plural = "Umbuchungen"

    def __str__(self):
        return f"{self.allocation.employment.staff_member}: {self.budget_item} ab {self.start_date:%d.%m.%Y}"

    @property
    def allocation_end(self):
        return self.allocation.end_date or self.allocation.employment.end_date

    @property
    def end(self):
        return self.end_date or self.allocation_end

    def clean(self):
        super().clean()
        if not (self.allocation_id and self.start_date):
            return
        if not (self.allocation.start_date <= self.start_date <= self.allocation_end):
            raise ValidationError(
                f"„Ab“ muss in der Zuordnung liegen ({self.allocation.start_date:%d.%m.%Y} – {self.allocation_end:%d.%m.%Y})."
            )
        if self.end_date and not (self.start_date <= self.end_date <= self.allocation_end):
            raise ValidationError("„Bis“ muss zwischen „Ab“ und dem Ende der Zuordnung liegen.")
        if self.percentage is not None:
            overlapping = [
                other for other in self.allocation.rebookings.exclude(pk=self.pk)
                if other.start_date <= self.end and self.start_date <= other.end
            ]
            booked = sum((other.percentage for other in overlapping), self.percentage)
            if booked > self.allocation.percentage:
                raise ValidationError(
                    f"Zusammen mit den überlappenden Umbuchungen würden {booked.normalize():f} % umgebucht, "
                    f"die Zuordnung hat nur {self.allocation.percentage.normalize():f} %."
                )

    def as_allocations(self):
        """Unsaved (source, target) allocations covering the rebooked period, for cost calculations.

        The source loses exactly the rebooked share; the rest stays where it is.
        """
        source = StaffFundingAllocation(
            employment=self.allocation.employment, budget_item=self.allocation.budget_item,
            percentage=min(self.percentage, self.allocation.percentage), start_date=self.start_date, end_date=self.end,
        )
        target = StaffFundingAllocation(
            employment=self.allocation.employment, budget_item=self.budget_item,
            percentage=self.percentage, start_date=self.start_date, end_date=self.end,
        )
        return source, target


def complete_rebooking(rebooking):
    """Apply an Umbuchung: move its share of the allocation to the target budget for its period.

    The allocation is split into the part before, the remainder during
    (allocation minus rebooked share, if any) and the part after the period.
    Other open Umbuchungen of the allocation move to the part they start in.
    """
    from datetime import timedelta

    from django.db import transaction

    allocation = rebooking.allocation
    employment_end = allocation.employment.end_date
    open_end = allocation.end_date is None
    start, end = rebooking.start_date, rebooking.end

    def end_value(day):
        return None if open_end and day == employment_end else day

    pieces = []
    if start > allocation.start_date:
        pieces.append((allocation.start_date, start - timedelta(days=1), allocation.percentage))
    remaining = allocation.percentage - rebooking.percentage
    if remaining > 0:
        pieces.append((start, end, remaining))
    if end < rebooking.allocation_end:
        pieces.append((end + timedelta(days=1), rebooking.allocation_end, allocation.percentage))

    others = list(allocation.rebookings.exclude(pk=rebooking.pk))
    with transaction.atomic():
        template = StaffFundingAllocation.objects.get(pk=allocation.pk)
        saved = []
        for index, (piece_start, piece_end, percentage) in enumerate(pieces):
            piece = allocation if index == 0 else StaffFundingAllocation.objects.get(pk=template.pk)
            if index:
                piece.pk = None
            piece.start_date, piece.end_date, piece.percentage = piece_start, end_value(piece_end), percentage
            piece.save()
            saved.append(piece)

        target = StaffFundingAllocation(employment=allocation.employment) if saved else allocation
        target.budget_item = rebooking.budget_item
        target.landesstelle = target.annual_pool_budget = None
        target.is_universal = False
        target.percentage = rebooking.percentage
        target.start_date, target.end_date = start, end_value(end)
        target.is_rebooking = True
        if saved:
            target.sap_reference = ""
        target.full_clean()
        target.save()

        for other in others:
            piece = next(
                (p for p in saved if p.start_date <= other.start_date <= (p.end_date or employment_end)), None,
            )
            if piece is not None:
                other.allocation = piece
                other.save(update_fields=["allocation"])
        rebooking.delete()
    return target
