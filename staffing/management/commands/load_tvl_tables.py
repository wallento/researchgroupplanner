from django.core.management.base import BaseCommand
from django.db import transaction

from staffing.models import SalaryCategory, SalaryTable, SalaryTableEntry
from staffing.tvl import PERIODS, employer_cost, gross_amounts


class Command(BaseCommand):
    help = "Fill the Entgelttabellen with TV-L Personalkosten (incl. Arbeitgeberanteile) for existing Entgeltgruppen."

    @transaction.atomic
    def handle(self, *args, **options):
        categories = {category.name: category for category in SalaryCategory.objects.all()}
        for name, valid_from, valid_until, gross_table, rates in PERIODS:
            table, created = SalaryTable.objects.get_or_create(
                name=name, defaults={"valid_from": valid_from, "valid_until": valid_until},
            )
            table.valid_from, table.valid_until = valid_from, valid_until
            table.full_clean()
            table.save()

            count = 0
            for (group, level), gross in gross_amounts(gross_table).items():
                category = categories.get(group)
                if category is None:
                    continue
                SalaryTableEntry.objects.update_or_create(
                    table=table, salary_category=category, level=level,
                    defaults={"gross": gross, "amount": employer_cost(gross, rates)},
                )
                count += 1
            self.stdout.write(f"{'Angelegt' if created else 'Aktualisiert'}: {table} – {count} Werte")

        missing = sorted({group for _, _, _, table, _ in PERIODS for group, _ in gross_amounts(table)} - set(categories))
        if missing:
            self.stdout.write(f"Ohne Entgeltgruppe in der Datenbank, nicht übernommen: {', '.join(missing)}")
