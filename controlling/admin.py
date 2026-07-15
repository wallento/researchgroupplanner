from django.contrib import admin
from .models import NotificationLog


@admin.register(NotificationLog)
class NotificationLogAdmin(admin.ModelAdmin):
    list_display = ('notification_type', 'recipient_email', 'related_object_type', 'sent_date')
    list_filter = ('notification_type', 'sent_date')
    search_fields = ('recipient_email', 'related_object_type')
    readonly_fields = ('sent_date',)

