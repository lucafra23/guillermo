import django.db.models.deletion
import filer.fields.file
from django.db import migrations, models


class Migration(migrations.Migration):
    """Add Render.document and Render.config, the output and options of a graphic-novel render."""

    dependencies = [
        ("filer", "0017_image__transparent"),
        ("scene", "0025_letter_action_choice"),
    ]

    operations = [
        migrations.AddField(
            model_name="render",
            name="document",
            field=filer.fields.file.FilerFileField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="render_documents",
                to="filer.file",
                verbose_name="document",
            ),
        ),
        migrations.AddField(
            model_name="render",
            name="config",
            field=models.JSONField(blank=True, null=True, verbose_name="config"),
        ),
    ]
