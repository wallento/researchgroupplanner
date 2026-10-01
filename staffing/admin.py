from django.contrib import admin

# Register your models here.
from .models import Employment, EmploymentSalaries, StaffFundingAllocation, StaffMember

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
    inlines = [EmploymentSalariesInline, StaffFundingAllocationInline]

admin.site.register(Employment, EmploymentAdmin)
admin.site.register(StaffFundingAllocation)
