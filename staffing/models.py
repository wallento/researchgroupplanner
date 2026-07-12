from django.db import models
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator

from projects.models import AnnualPoolBudget, EmploymentCategories, Landesstelle, StaffBudgetItem

# Create your models here.
class StaffMember(models.Model):
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    email = models.EmailField(blank=True, default='')
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
    start_date = models.DateField()
    end_date = models.DateField()

    def __str__(self):
        return f"{self.employment.staff_member} ({self.start_date} - {self.end_date}, € {self.salary})"
    
    def staff_member(self):
        return self.employment.staff_member


class StaffFundingAllocation(models.Model):
    employment = models.ForeignKey(Employment, on_delete=models.CASCADE)
    budget_item = models.ForeignKey(StaffBudgetItem, on_delete=models.CASCADE, null=True, blank=True)
    landesstelle = models.ForeignKey(Landesstelle, on_delete=models.CASCADE, null=True, blank=True)
    annual_pool_budget = models.ForeignKey(AnnualPoolBudget, on_delete=models.CASCADE, null=True, blank=True)
    percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)

    def clean(self):
        super().clean()
        source_count = sum(
            source is not None
            for source in (self.budget_item, self.landesstelle, self.annual_pool_budget)
        )
        if source_count != 1:
            raise ValidationError(
                "Bitte genau eine Finanzierungsquelle angeben: Projektbudget, Landesstelle oder Annual Pool Budget."
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
        return self.annual_pool_budget

    def __str__(self):
        end_date = self.end_date if self.end_date else "offen"
        return f"{self.employment.staff_member} - {self.percentage}% ({self.start_date} - {end_date}) in {self.source()}"