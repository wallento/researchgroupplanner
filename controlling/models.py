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

