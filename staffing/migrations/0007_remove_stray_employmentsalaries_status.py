from django.db import migrations


def drop_stray_status_column(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        columns = {
            row.name
            for row in schema_editor.connection.introspection.get_table_description(
                cursor, "staffing_employmentsalaries"
            )
        }
    if "status" in columns:
        schema_editor.execute("ALTER TABLE staffing_employmentsalaries DROP COLUMN status;")


def add_back_status_column(apps, schema_editor):
    schema_editor.execute(
        "ALTER TABLE staffing_employmentsalaries ADD COLUMN status varchar(20) NOT NULL DEFAULT 'active';"
    )


class Migration(migrations.Migration):

    dependencies = [
        ('staffing', '0006_staffmember_is_leadership'),
    ]

    operations = [
        migrations.RunPython(drop_stray_status_column, add_back_status_column),
    ]
