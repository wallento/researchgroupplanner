from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from projects.models import SAPFund
from sap_integration.parser import parse_downloaded_reports


class Command(BaseCommand):
    help = "Bereitet bereits heruntergeladene SAP-Exporte als JSON für die Webansicht auf."

    def add_arguments(self, parser):
        parser.add_argument(
            "--year",
            type=int,
            default=timezone.localdate().year,
            help="Geschäftsjahr (standardmäßig das aktuelle Jahr)",
        )

    def handle(self, *args, **options):
        if not settings.SAP_ENABLED:
            raise CommandError("Die SAP-Integration ist deaktiviert.")
        year = options["year"]
        if not 2000 <= year <= 2100:
            raise CommandError("Das SAP-Geschäftsjahr muss zwischen 2000 und 2100 liegen.")
        fund_numbers = list(
            SAPFund.objects.filter(is_active=True).values_list("fund_number", flat=True)
        )
        try:
            target_path = parse_downloaded_reports(
                settings.SAP_DATA_DIR,
                year,
                fund_numbers,
            )
        except Exception as error:
            raise CommandError(
                f"SAP-Parsing fehlgeschlagen ({type(error).__name__}): {error}"
            ) from error
        self.stdout.write(
            self.style.SUCCESS(
                f"SAP-Daten für {year} und {len(fund_numbers)} Fonds aufbereitet: {target_path}"
            )
        )
