

from .models import StaffBudgetItem
from django.http import HttpRequest
from django.contrib import messages

def check_staff_budget_item(request: HttpRequest, budget_item: StaffBudgetItem) -> None:
    if budget_item.get_eligibilities().count() == 0:
        messages.error(request, f"Das Personalbudget '{budget_item}' hat keine zugewiesenen Kategorien.")
    for assignment in budget_item.staffassignment_set.all():
        if assignment.start_date < budget_item.project.start_date or assignment.end_date > budget_item.project.end_date:
            messages.warning(request, f"Die Zuweisung '{assignment}' liegt außerhalb des Zeitraums des Budgets '{budget_item}'.")


def full_checks(request: HttpRequest) -> None:
    budget_items = StaffBudgetItem.objects.all()
    for item in budget_items:
        check_staff_budget_item(request, item)