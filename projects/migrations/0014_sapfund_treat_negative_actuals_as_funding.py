from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0013_project_no_overhead"),
    ]

    operations = [
        migrations.AddField(
            model_name="sapfund",
            name="treat_negative_actuals_as_funding",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Nach dem Entfernen von Gegenbuchungen werden verbleibende "
                    "negative Ist-Buchungen angezeigt, aber nicht als Ausgaben "
                    "verrechnet."
                ),
                verbose_name=(
                    "Negative Ist-Buchungen als Mittelzuflüsse behandeln"
                ),
            ),
        ),
    ]
