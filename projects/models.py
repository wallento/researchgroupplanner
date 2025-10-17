from django.db import models

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

    def __str__(self):
        return self.acronym

    def get_years(self):
        return [str(i) for i in range(self.start_date.year, self.end_date.year + 1)]

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

    def __str__(self):
        return f"{self.project.acronym} - Overhead - {self.amount}"

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