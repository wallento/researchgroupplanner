from django.contrib import admin

# Register your models here.
from .models import (
    Employment,
    EmploymentSalaries,
    Rebooking,
    SalaryCategory,
    SalaryTable,
    SalaryTableEntry,
    StaffFundingAllocation,
    StaffMember,
)

class EmploymentInline(admin.StackedInline):
    model = Employment
    extra = 0

class StaffMemberAdmin(admin.ModelAdmin):
    list_display = (
        'get_full_name',
        'email',
        'sap_business_partner',
        'is_leadership',
        'status',
    )
    fields = (
        'first_name',
        'last_name',
        'email',
        'sap_business_partner',
        'is_leadership',
        'status',
    )
    inlines = [EmploymentInline]
    
    def get_full_name(self, obj):
        return str(obj)
    get_full_name.short_description = 'Name'

admin.site.register(StaffMember, StaffMemberAdmin)

class EmploymentSalariesInline(admin.TabularInline):
    model = EmploymentSalaries
    extra = 0
    ordering = ("start_date", "end_date", "pk")
    fields = ("salary", "is_exact_amount", "start_date", "end_date")


class StaffFundingAllocationInline(admin.TabularInline):
    model = StaffFundingAllocation
    extra = 0

class EmploymentAdmin(admin.ModelAdmin):
    list_display = ("__str__", "salary_category", "start_date", "end_date")
    list_filter = ("salary_category", "category")
    inlines = [EmploymentSalariesInline, StaffFundingAllocationInline]

admin.site.register(Employment, EmploymentAdmin)


@admin.register(StaffFundingAllocation)
class StaffFundingAllocationAdmin(admin.ModelAdmin):
    list_display = ("employment", "budget_item", "percentage", "start_date", "end_date", "is_rebooking")
    list_filter = ("is_rebooking", "budget_item__project")
    search_fields = ("employment__staff_member__first_name", "employment__staff_member__last_name", "sap_reference")
    list_select_related = ("employment__staff_member", "budget_item__project")


@admin.register(SalaryCategory)
class SalaryCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "special_payment_rate")
    search_fields = ("name",)


class SalaryTableEntryInline(admin.TabularInline):
    model = SalaryTableEntry
    extra = 0
    fields = ("salary_category", "level", "gross", "amount")


@admin.register(SalaryTable)
class SalaryTableAdmin(admin.ModelAdmin):
    list_display = ("name", "valid_from", "valid_until")
    inlines = [SalaryTableEntryInline]


@admin.register(Rebooking)
class RebookingAdmin(admin.ModelAdmin):
    list_display = ("allocation", "budget_item", "percentage", "start_date", "end_date")
    list_filter = ("budget_item__project",)
    search_fields = ("allocation__employment__staff_member__first_name", "allocation__employment__staff_member__last_name")
    list_select_related = ("allocation__employment__staff_member", "budget_item__project")
