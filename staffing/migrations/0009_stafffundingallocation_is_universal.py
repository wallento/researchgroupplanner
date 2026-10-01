from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("staffing", "0008_staffmember_sap_business_partner"),
    ]

    operations = [
        migrations.AddField(
            model_name="stafffundingallocation",
            name="is_universal",
            field=models.BooleanField(default=False, verbose_name="Universalprojekt"),
        ),
    ]
