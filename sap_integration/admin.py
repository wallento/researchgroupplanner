from django.contrib import admin

from sap_integration.models import (
    SAPCostTypeMapping,
    SAPIgnoredPosition,
    SAPImport,
    SAPPersonMapping,
)


@admin.register(SAPImport)
class SAPImportAdmin(admin.ModelAdmin):
    list_display = ("fund", "file_name", "last_booking", "imported_at", "imported_by")
    readonly_fields = ("fund", "file_name", "imported_at", "imported_by", "row_count", "first_booking", "last_booking", "cost_types")


@admin.register(SAPCostTypeMapping)
class SAPCostTypeMappingAdmin(admin.ModelAdmin):
    list_display = ("fund", "cost_type", "staff_budget_item", "other_budget_item")
    list_filter = ("fund",)


@admin.register(SAPPersonMapping)
class SAPPersonMappingAdmin(admin.ModelAdmin):
    list_display = ("sap_name", "staff_member")
    fields = ("sap_name", "staff_member")
    search_fields = ("sap_name", "staff_member__last_name", "staff_member__first_name")


@admin.register(SAPIgnoredPosition)
class SAPIgnoredPositionAdmin(admin.ModelAdmin):
    list_display = ("fund", "reference", "note", "created_by", "created_at")
    list_filter = ("fund",)
    search_fields = ("reference", "note")
