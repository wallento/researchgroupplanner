import hashlib

from django.conf import settings
from django.db import models
from django.utils import timezone


class NotificationLog(models.Model):
    """Track sent notifications to avoid duplicates"""
    NOTIFICATION_TYPES = [
        ('contract_expiry', 'Vertrag läuft aus'),
        ('milestone_due', 'Meilenstein fällig'),
    ]
    
    notification_type = models.CharField(max_length=20, choices=NOTIFICATION_TYPES)
    recipient_email = models.EmailField()
    related_object_type = models.CharField(max_length=50)  # 'Employment' oder 'ProjectMilestone'
    related_object_id = models.IntegerField()
    sent_date = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ('notification_type', 'recipient_email', 'related_object_type', 'related_object_id')
        ordering = ['-sent_date']
    
    def __str__(self):
        return f"{self.get_notification_type_display()} → {self.recipient_email} ({self.sent_date.date()})"



class IgnoredWarning(models.Model):
    """A warning hidden from the warnings page until its content changes."""

    key = models.CharField(max_length=64, unique=True)
    comment = models.TextField("Begründung")
    created_at = models.DateTimeField("Ignoriert am", auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Ignorierte Warnung"
        verbose_name_plural = "Ignorierte Warnungen"

    def __str__(self):
        return self.key[:12]

    @staticmethod
    def key_for(warning):
        """Stable key from the warning text; any change in the text shows it again."""
        text = "\n".join([warning["title"], warning.get("detail") or "", *warning.get("details", [])])
        return hashlib.sha256(text.encode()).hexdigest()
