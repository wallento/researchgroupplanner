from django.core.management.base import BaseCommand
from django.core.mail import send_mail
from django.template.loader import render_to_string
from staffing.models import StaffMember


class Command(BaseCommand):
    help = 'Send a test email to the first leadership staff member with an email address'

    def handle(self, *args, **options):
        # Get first leadership staff member with email
        staff = StaffMember.objects.filter(is_leadership=True, email__isnull=False).exclude(email='').first()
        
        if not staff:
            self.stdout.write(self.style.ERROR('No leadership staff member with email found'))
            return
        
        recipient_email = staff.email
        subject = f'Test Email - {staff.first_name} {staff.last_name}'
        message = f'This is a test email sent at {__import__("django.utils.timezone", fromlist=["now"]).now()}'
        
        try:
            send_mail(subject, message, None, [recipient_email])
            self.stdout.write(self.style.SUCCESS(f'✓ Test email sent to {recipient_email}'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'✗ Error sending test email: {e}'))
