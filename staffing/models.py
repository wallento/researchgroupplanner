from django.db import models

from projects.models import Project, EmploymentCategories, StaffBudgetItem

# Create your models here.
class StaffMember(models.Model):
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
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

class StaffAssignment(models.Model):
    employment = models.ForeignKey(Employment, on_delete=models.CASCADE)
    budget_item = models.ForeignKey(StaffBudgetItem, on_delete=models.CASCADE, null=True, blank=True)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)

    def __str__(self):
        return f"{self.employment.staff_member} - ({self.start_date} - {self.end_date}) in {self.budget_item}"