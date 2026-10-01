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
    ProjectMilestone,
    SAPFund,
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

class ProjectMilestoneInlineAdmin(admin.TabularInline):
    model = ProjectMilestone
    extra = 1
    fields = ('date', 'title')


class ProjectSAPFundInlineAdmin(admin.TabularInline):
    model = SAPFund
    fk_name = "project"
    extra = 1
    fields = (
        "fund_number",
        "label",
        "treat_negative_actuals_as_funding",
        "is_active",
    )


class ProjectAdmin(admin.ModelAdmin):
    inlines = [
        StaffBudgetItemInlineAdmin,
        OverheadBudgetItemInlineAdmin,
        OtherBudgetItemInlineAdmin,
        ProjectMilestoneInlineAdmin,
        ProjectSAPFundInlineAdmin,
    ]

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


class AnnualPoolSAPFundInlineAdmin(admin.TabularInline):
    model = SAPFund
    fk_name = "annual_pool"
    extra = 1
    fields = (
        "fund_number",
        "label",
        "treat_negative_actuals_as_funding",
        "is_active",
    )


@admin.register(AnnualPool)
class AnnualPoolAdmin(admin.ModelAdmin):
    inlines = [AnnualPoolBudgetInlineAdmin, AnnualPoolSAPFundInlineAdmin]
    list_display = ("title",)


@admin.register(AnnualPoolBudget)
class AnnualPoolBudgetAdmin(admin.ModelAdmin):
    list_display = ("annual_pool", "year", "amount_assigned")
    list_filter = ("year", "annual_pool")


@admin.register(SAPFund)
class SAPFundAdmin(admin.ModelAdmin):
    list_display = (
        "fund_number",
        "label",
        "owner",
        "treat_negative_actuals_as_funding",
        "is_universal",
        "is_active",
    )
    list_filter = (
        "treat_negative_actuals_as_funding",
        "is_universal",
        "is_active",
    )
    search_fields = ("fund_number", "label", "project__acronym", "annual_pool__title")

    @admin.display(description="Zuordnung")
    def owner(self, obj):
        return obj.project or obj.annual_pool or "Universalprojekt"
