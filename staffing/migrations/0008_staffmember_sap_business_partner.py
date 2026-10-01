from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("staffing", "0007_remove_stray_employmentsalaries_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="staffmember",
            name="sap_business_partner",
            field=models.CharField(
                blank=True,
                default="",
                help_text=(
                    "Optionaler Name aus dem SAP-Kontoauszug. Ohne Eintrag wird "
                    "die Zuordnung automatisch über Vor- und Nachname versucht."
                ),
                max_length=255,
                verbose_name="SAP-Geschäftspartner",
            ),
        ),
    ]
