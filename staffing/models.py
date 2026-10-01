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

class Employment(models.Model):
    staff_member = models.ForeignKey(StaffMember, on_delete=models.CASCADE)
    start_date = models.DateField()
    end_date = models.DateField()
    percentage = models.DecimalField(max_digits=5, decimal_places=2)
    category = models.CharField(max_length=50, choices=EmploymentCategories, default='researcher')

    def __str__(self):
        return f"{self.staff_member} - {EmploymentCategories[self.category]} ({self.percentage}%)"
    
    def get_category(self):
        return EmploymentCategories[self.category]

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
