from django.core.management.base import BaseCommand
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils import timezone
from dateutil.relativedelta import relativedelta
from datetime import date

from staffing.models import Employment, StaffMember
from projects.models import Project, ProjectMilestone
from controlling.models import NotificationLog


class Command(BaseCommand):
    help = 'Send notifications for contract expiries and milestones due in 1 month'

    def handle(self, *args, **options):
        today = timezone.now().date()
        notification_date = today + relativedelta(months=1)
        
        self.stdout.write(self.style.SUCCESS(f'Checking for notifications on {notification_date}...'))
        
        # 1. Send contract expiry notifications
        self._send_contract_expiry_notifications(today, notification_date)
        
        # 2. Send milestone due notifications
        self._send_milestone_notifications(today, notification_date)
        
        self.stdout.write(self.style.SUCCESS('Notifications sent successfully!'))

    def _send_contract_expiry_notifications(self, today, notification_date):
        """Send notifications for employments ending in 1 month"""
        # Find employments ending in 1 month
        employments = Employment.objects.filter(
            end_date=notification_date,
            staff_member__is_leadership=True
        ).select_related('staff_member')
        
        for employment in employments:
            # Check if already notified
            if NotificationLog.objects.filter(
                notification_type='contract_expiry',
                recipient_email=employment.staff_member.email,
                related_object_type='Employment',
                related_object_id=employment.id
            ).exists():
                self.stdout.write(f'⏭️ Already notified: {employment.staff_member.email} about {employment.staff_member}')
                continue
            
            if not employment.staff_member.email:
                self.stdout.write(f'⚠️ No email for {employment.staff_member}')
                continue
            
            # Send email
            context = {
                'staff_member_name': employment.staff_member.first_name or employment.staff_member.last_name,
                'employee_name': str(employment.staff_member),
                'contract_end_date': employment.end_date,
            }
            
            try:
                subject = f'Erinnerung: Vertrag läuft aus - {employment.staff_member}'
                message = render_to_string('controlling/contract_expiry.txt', context)
                
                send_mail(
                    subject,
                    message,
                    'noreply@example.com',
                    [employment.staff_member.email],
                    fail_silently=False,
                )
                
                # Log notification
                NotificationLog.objects.create(
                    notification_type='contract_expiry',
                    recipient_email=employment.staff_member.email,
                    related_object_type='Employment',
                    related_object_id=employment.id
                )
                
                self.stdout.write(
                    self.style.SUCCESS(f'✓ Sent contract expiry notification to {employment.staff_member.email}')
                )
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f'✗ Error sending email to {employment.staff_member.email}: {str(e)}')
                )

    def _send_milestone_notifications(self, today, notification_date):
        """Send notifications for milestones due in 1 month"""
        # Find milestones due in 1 month
        milestones = ProjectMilestone.objects.filter(
            date=notification_date
        ).select_related('project')
        
        # Get all leaders
        leaders = StaffMember.objects.filter(is_leadership=True, email__isnull=False).exclude(email='')
        
        for milestone in milestones:
            for leader in leaders:
                # Check if already notified
                if NotificationLog.objects.filter(
                    notification_type='milestone_due',
                    recipient_email=leader.email,
                    related_object_type='ProjectMilestone',
                    related_object_id=milestone.id
                ).exists():
                    continue
                
                # Send email
                context = {
                    'leader_name': leader.first_name or leader.last_name,
                    'milestone_title': milestone.title,
                    'project_acronym': milestone.project.acronym,
                    'milestone_date': milestone.date,
                }
                
                try:
                    subject = f'Erinnerung: Meilenstein fällig - {milestone.project.acronym}'
                    message = render_to_string('controlling/milestone_due.txt', context)
                    
                    send_mail(
                        subject,
                        message,
                        'noreply@example.com',
                        [leader.email],
                        fail_silently=False,
                    )
                    
                    # Log notification
                    NotificationLog.objects.create(
                        notification_type='milestone_due',
                        recipient_email=leader.email,
                        related_object_type='ProjectMilestone',
                        related_object_id=milestone.id
                    )
                    
                    self.stdout.write(
                        self.style.SUCCESS(f'✓ Sent milestone notification to {leader.email}')
                    )
                except Exception as e:
                    self.stdout.write(
                        self.style.ERROR(f'✗ Error sending email to {leader.email}: {str(e)}')
                    )
