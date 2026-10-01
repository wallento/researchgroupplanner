from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from sap_integration.gm_export import import_gm_export


class Command(BaseCommand):
    help = "Importiert einen SAP-Projektexport (GM_E_4GBA) für den Abgleich mit der Planung."

    def add_arguments(self, parser):
        parser.add_argument("export_file", help="Pfad zur exportierten .xlsx-Datei")

    def handle(self, *args, **options):
        if not settings.SAP_GM_IMPORT_ENABLED:
            raise CommandError("Der Import von SAP-Projektexporten ist deaktiviert (SAP_GM_IMPORT_ENABLED).")
        path = Path(options["export_file"])
        if not path.is_file():
            raise CommandError(f"Datei nicht gefunden: {path}")
        try:
            sap_import = import_gm_export(path)
        except Exception as error:
            raise CommandError(f"SAP-Import fehlgeschlagen: {error}") from error
        self.stdout.write(
            self.style.SUCCESS(
                f"{sap_import.positions.count()} SAP-Positionen für {sap_import.fund} importiert "
                f"(Buchungen bis {sap_import.last_booking:%d.%m.%Y})."
            )
        )
