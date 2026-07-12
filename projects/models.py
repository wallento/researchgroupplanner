from django.db import models
from django.core.validators import MaxValueValidator, MinValueValidator
from django.core.exceptions import ValidationError
from decimal import Decimal

EmploymentCategories = {
    'student': 'Studentische Hilfskraft',
    'undergrad': 'Nicht-Wissenschaftliche Mitarbeiter',
    'researcher': 'Wissenschaftliche Mitarbeiter',
}

# Create your models here.
class Project(models.Model):
    acronym = models.CharField(max_length=100)
    start_date = models.DateField()
    end_date = models.DateField()
    extension_planning_date = models.DateField(blank=True, null=True)
    budget_total = models.DecimalField(max_digits=10, decimal_places=2)

    def clean(self):
        super().clean()
        if self.extension_planning_date and self.extension_planning_date < self.end_date:
            raise ValidationError({
                "extension_planning_date": "Die kostenneutrale Verlängerung darf nicht vor dem regulären Enddatum liegen."
            })

    def __str__(self):
        return self.acronym

    def get_years(self):
        return [str(i) for i in range(self.start_date.year, self.end_date.year + 1)]

    def get_effective_end_date(self):
        if self.extension_planning_date and self.extension_planning_date > self.end_date:
            return self.extension_planning_date
        return self.end_date


class ProjectMilestone(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='milestones')
    date = models.DateField()
    title = models.CharField(max_length=200)

    class Meta:
        ordering = ['date']

    def __str__(self):
        return f"{self.project.acronym} - {self.title} ({self.date})"


class Institute(models.Model):
    name = models.CharField(max_length=200, unique=True)
    short_name = models.CharField(max_length=50, unique=True)
    is_own_chair = models.BooleanField(
        default=False,
        help_text="Markiert den eigenen Lehrstuhl. Overhead-Anteile dieses Instituts gelten als verfügbar.",
    )

    def __str__(self):
        return self.short_name


class Landesstelle(models.Model):
    title = models.CharField(max_length=200)
    institute = models.ForeignKey(Institute, on_delete=models.PROTECT, null=True, blank=True)
    percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    start_date = models.DateField()
    end_date = models.DateField(blank=True, null=True)
    notes = models.TextField(blank=True, null=True)

    def __str__(self):
        institute_str = f" ({self.institute.short_name})" if self.institute_id else ""
        if self.end_date:
            return f"{self.title}{institute_str} {self.percentage}% ({self.start_date} - {self.end_date})"
        return f"{self.title}{institute_str} {self.percentage}% (ab {self.start_date})"

class StaffBudgetItem(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    title = models.CharField(max_length=100)
    short_title = models.CharField(max_length=50, blank=True, null=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)


    def __str__(self):
        return f"{self.project.acronym} - {self.title}"

    def get_eligibilities(self):
        return StaffBudgetItemEligibility.objects.filter(budget_item=self)

class StaffBudgetItemEligibility(models.Model):
    budget_item = models.ForeignKey(StaffBudgetItem, on_delete=models.CASCADE)
    eligible_employment = models.CharField(max_length=50, choices=EmploymentCategories)

class OverheadBudgetItem(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    amount = models.DecimalField(max_digits=10, decimal_places=2)

    def available_amount(self):
        """Sum of shares belonging to institutes marked as own chair."""
        total = Decimal("0.00")
        for share in self.overheadbudgetitemshare_set.filter(institute__is_own_chair=True):
            total += (self.amount * share.percentage / Decimal("100")).quantize(Decimal("0.01"))
        return total

    def own_chair_percentage(self):
        return sum(
            share.percentage
            for share in self.overheadbudgetitemshare_set.filter(institute__is_own_chair=True)
        )

    def __str__(self):
        return f"{self.project.acronym} - Overhead - {self.amount}"


class OverheadBudgetItemShare(models.Model):
    overhead_item = models.ForeignKey(OverheadBudgetItem, on_delete=models.CASCADE)
    institute = models.ForeignKey(Institute, on_delete=models.PROTECT)
    percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )

    class Meta:
        unique_together = [("overhead_item", "institute")]

    def __str__(self):
        return f"{self.overhead_item} → {self.institute}: {self.percentage}%"

class OtherBudgetItem(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    title = models.CharField(max_length=100)
    short_title = models.CharField(max_length=50, blank=True, null=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        return f"{self.project.acronym} - {self.title} - {self.amount}"
    
    def get_transactions(self, year: int = None) -> list['OtherBudgetItemTransaction']:
        if year:
            return list(OtherBudgetItemTransaction.objects.filter(budget_item=self, date__year=year))
        return list(OtherBudgetItemTransaction.objects.filter(budget_item=self))

class OtherBudgetItemTransaction(models.Model):
    budget_item = models.ForeignKey(OtherBudgetItem, on_delete=models.CASCADE)
    date = models.DateField()
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    description = models.TextField(blank=True, null=True)
    sap_id = models.CharField(max_length=100, blank=True, null=True)

    def __str__(self):
        return f"{self.budget_item} - {self.date} - {self.amount}"


class AnnualPool(models.Model):
    title = models.CharField(max_length=200, unique=True)
    notes = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.title


class AnnualPoolBudget(models.Model):
    annual_pool = models.ForeignKey(AnnualPool, on_delete=models.CASCADE)
    year = models.PositiveIntegerField()
    amount_assigned = models.DecimalField(max_digits=10, decimal_places=2)
    notes = models.TextField(blank=True, null=True)

    class Meta:
        unique_together = [("annual_pool", "year")]
        ordering = ["year"]

    def __str__(self):
        return f"{self.annual_pool.title} - {self.year}: {self.amount_assigned}"