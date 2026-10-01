from calendar import monthrange

from django.db import migrations, models


def mark_existing_single_month_partial_amounts(apps, schema_editor):
    EmploymentSalaries = apps.get_model("staffing", "EmploymentSalaries")
    exact_ids = []
    for salary in EmploymentSalaries.objects.all().iterator():
        same_month = (
            salary.start_date.year,
            salary.start_date.month,
        ) == (
            salary.end_date.year,
            salary.end_date.month,
        )
        covers_full_month = (
            salary.start_date.day == 1
            and salary.end_date.day
            == monthrange(salary.end_date.year, salary.end_date.month)[1]
        )
        if same_month and not covers_full_month:
            exact_ids.append(salary.pk)

    EmploymentSalaries.objects.filter(pk__in=exact_ids).update(
        is_exact_amount=True
    )


class Migration(migrations.Migration):
    dependencies = [
        ("staffing", "0009_stafffundingallocation_is_universal"),
    ]

    operations = [
        migrations.AddField(
            model_name="employmentsalaries",
            name="is_exact_amount",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Der Betrag gilt vollständig für den angegebenen "
                    "Teilzeitraum und wird nicht tagesanteilig berechnet."
                ),
                verbose_name="Exakter Betrag?",
            ),
        ),
        migrations.RunPython(
            mark_existing_single_month_partial_amounts,
            migrations.RunPython.noop,
        ),
    ]
