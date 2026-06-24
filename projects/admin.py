from django.contrib import admin

# Register your models here.
from .models import (
    AnnualPool,
    AnnualPoolBudget,
    Institute,
    Landesstelle,
    OtherBudgetItem,
    OtherBudgetItemTransaction,
    OverheadBudgetItem,
    OverheadBudgetItemShare,
    Project,
    StaffBudgetItem,
    StaffBudgetItemEligibility,
)

class StaffBudgetItemInlineAdmin(admin.StackedInline):
    model = StaffBudgetItem
    extra = 0

class OverheadBudgetItemShareInlineAdmin(admin.TabularInline):
    model = OverheadBudgetItemShare
    extra = 1

class OverheadBudgetItemInlineAdmin(admin.StackedInline):
    model = OverheadBudgetItem
    extra = 0
    show_change_link = True

class OtherBudgetItemInlineAdmin(admin.StackedInline):
    model = OtherBudgetItem
    extra = 0


class ProjectAdmin(admin.ModelAdmin):
    inlines = [StaffBudgetItemInlineAdmin, OverheadBudgetItemInlineAdmin, OtherBudgetItemInlineAdmin]

admin.site.register(Project, ProjectAdmin)
admin.site.register(Institute)
admin.site.register(Landesstelle)

class OverheadBudgetItemAdmin(admin.ModelAdmin):
    inlines = [OverheadBudgetItemShareInlineAdmin]

admin.site.register(OverheadBudgetItem, OverheadBudgetItemAdmin)

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


class AnnualPoolBudgetInlineAdmin(admin.TabularInline):
    model = AnnualPoolBudget
    extra = 1


@admin.register(AnnualPool)
class AnnualPoolAdmin(admin.ModelAdmin):
    inlines = [AnnualPoolBudgetInlineAdmin]
    list_display = ("title",)


@admin.register(AnnualPoolBudget)
class AnnualPoolBudgetAdmin(admin.ModelAdmin):
    list_display = ("annual_pool", "year", "amount_assigned")
    list_filter = ("year", "annual_pool")