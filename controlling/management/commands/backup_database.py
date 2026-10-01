from django.core.management.base import BaseCommand, CommandError

from controlling.backups import BackupError, perform_database_backup


class Command(BaseCommand):
    help = "Create, verify, rotate, and optionally upload an SQLite backup."

    def handle(self, *args, **options):
        try:
            result = perform_database_backup(kind="scheduled")
        except (BackupError, OSError, ValueError) as error:
            raise CommandError(str(error)) from error

        destination = (
            "lokal und in Nextcloud"
            if result.uploaded_to_nextcloud
            else "lokal"
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Backup {result.path.name} wurde {destination} gespeichert."
            )
        )
