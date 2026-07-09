from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("scene", "0023_remove_historicalscene_prompt_draft_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="action",
            name="lettering",
            field=models.JSONField(blank=True, null=True, verbose_name="lettering"),
        ),
        # Action carries a bare HistoricalRecords() with no excluded_fields, so every concrete
        # field has a shadow on HistoricalAction. Adding only the concrete one leaves the two
        # models out of step and makemigrations reports drift on a tree nobody has touched.
        migrations.AddField(
            model_name="historicalaction",
            name="lettering",
            field=models.JSONField(blank=True, null=True, verbose_name="lettering"),
        ),
    ]
