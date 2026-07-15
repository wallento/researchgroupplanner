"""
Custom email backend that allows disabling SSL verification for self-signed certificates.
"""
import ssl
from django.core.mail.backends.smtp import EmailBackend as DjangoEmailBackend


class InsecureEmailBackend(DjangoEmailBackend):
    """
    Email backend that disables SSL certificate verification.
    Use this only for development or internal SMTP servers with self-signed certificates.
    """
    def open(self):
        if self.ssl_context is None and (self.use_ssl or self.use_tls):
            # Create SSL context with certificate verification disabled
            self.ssl_context = ssl.create_default_context()
            self.ssl_context.check_hostname = False
            self.ssl_context.verify_mode = ssl.CERT_NONE
        return super().open()
