import re

from django.db import migrations

TRAVEL_TEXT_RE = re.compile(r"^\W*RK[EA]\b|^\W*RK[EA]_")


def reclassify(apps, schema_editor):
    """Travel (RKE/RKA) booked on personnel cost types was imported as staff."""
    SAPPosition = apps.get_model("sap_integration", "SAPPosition")
    for position in SAPPosition.objects.filter(kind="staff"):
        if any(TRAVEL_TEXT_RE.match(text) for text in (position.title, position.description) if text):
            position.kind = "travel"
            position.person_name = ""
            position.contract_type = ""
            position.contract_periods = []
            position.monthly_actuals = {}
            position.save(update_fields=[
                "kind", "person_name", "contract_type", "contract_periods", "monthly_actuals",
            ])


class Migration(migrations.Migration):

    dependencies = [
        ("sap_integration", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(reclassify, migrations.RunPython.noop),
    ]
