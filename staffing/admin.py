from django.contrib import admin

# Register your models here.
from .models import StaffMember, StaffAssignment, Employment, EmploymentSalaries

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

class EmploymentAdmin(admin.ModelAdmin):
    inlines = [EmploymentSalariesInline]

admin.site.register(Employment, EmploymentAdmin)