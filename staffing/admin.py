from django.contrib import admin

# Register your models here.
from .models import Employment, EmploymentSalaries, StaffAssignment, StaffFundingAllocation, StaffMember

class EmploymentInline(admin.StackedInline):
    model = Employment
    extra = 0

class StaffMemberAdmin(admin.ModelAdmin):
    inlines = [EmploymentInline]

admin.site.register(StaffMember, StaffMemberAdmin)
admin.site.register(StaffAssignment)

class EmploymentSalariesInline(admin.TabularInline):
    model = EmploymentSalaries
    extra = 0


class StaffFundingAllocationInline(admin.TabularInline):
    model = StaffFundingAllocation
    extra = 0

class EmploymentAdmin(admin.ModelAdmin):
    inlines = [EmploymentSalariesInline, StaffFundingAllocationInline]

admin.site.register(Employment, EmploymentAdmin)
admin.site.register(StaffFundingAllocation)