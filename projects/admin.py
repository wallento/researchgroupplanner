from django.contrib import admin

# Register your models here.
from .models import OtherBudgetItem, OtherBudgetItemTransaction, OverheadBudgetItem, Project, StaffBudgetItem, StaffBudgetItemEligibility

class StaffBudgetItemInlineAdmin(admin.StackedInline):
    model = StaffBudgetItem
    extra = 0

class OverheadBudgetItemInlineAdmin(admin.StackedInline):
    model = OverheadBudgetItem
    extra = 0

class OtherBudgetItemInlineAdmin(admin.StackedInline):
    model = OtherBudgetItem
    extra = 0

class ProjectAdmin(admin.ModelAdmin):
    inlines = [StaffBudgetItemInlineAdmin, OverheadBudgetItemInlineAdmin, OtherBudgetItemInlineAdmin]

admin.site.register(Project, ProjectAdmin)

class StaffBudgetItemEligibilityAdmin(admin.StackedInline):
    model = StaffBudgetItemEligibility
    extra = 0

class StaffBudgetItemAdmin(admin.ModelAdmin):
    inlines = [StaffBudgetItemEligibilityAdmin]

admin.site.register(StaffBudgetItem, StaffBudgetItemAdmin)

class OtherBudgetItemTransactionInlineAdmin(admin.TabularInline):
    model = OtherBudgetItemTransaction
    extra = 0

class OtherBudgetItemAdmin(admin.ModelAdmin):
    inlines = [OtherBudgetItemTransactionInlineAdmin]

admin.site.register(OtherBudgetItem, OtherBudgetItemAdmin)