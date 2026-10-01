from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from projects.models import SAPFund
from sap_integration.config import SAPConfig, SAPConfigurationError
from sap_integration.parser import parse_downloaded_reports
from sap_integration.sync import run_sync


class Command(BaseCommand):
    help = "Lädt Budget, Ist und Obligo manuell aus der SAP WebGUI herunter."

    def add_arguments(self, parser):
        parser.add_argument(
            "--year",
            type=int,
            default=timezone.localdate().year,
            help="Geschäftsjahr (standardmäßig das aktuelle Jahr)",
        )

    def handle(self, *args, **options):
        year = options["year"]
        if not 2000 <= year <= 2100:
            raise CommandError("Das SAP-Geschäftsjahr muss zwischen 2000 und 2100 liegen.")

        try:
            config = SAPConfig.from_settings()
        except SAPConfigurationError as error:
            raise CommandError(str(error)) from error

        active_funds = SAPFund.objects.filter(is_active=True).count()
        if active_funds == 0:
            self.stdout.write(
                self.style.WARNING("Es sind keine aktiven SAP-Fonds konfiguriert.")
            )

        self.stdout.write(
            f"SAP-Download für {year} und {active_funds} aktive Fonds wird gestartet …"
        )
        try:
            result = run_sync(config, year)
        except Exception as error:
            raise CommandError(
                f"SAP-Download fehlgeschlagen ({type(error).__name__}): {error}"
            ) from error

        try:
            parsed_path = parse_downloaded_reports(
                config.data_dir,
                year,
                SAPFund.objects.filter(is_active=True).values_list("fund_number", flat=True),
            )
        except Exception as error:
            raise CommandError(
                "SAP-Download wurde gespeichert, aber das Parsing ist "
                f"fehlgeschlagen ({type(error).__name__}): {error}"
            ) from error

        self.stdout.write(
            self.style.SUCCESS(
                f"SAP-Download für {result.year} abgeschlossen: "
                f"{len(result.report_paths)} Exporte gespeichert und nach {parsed_path} aufbereitet."
            )
        )
