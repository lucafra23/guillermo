import django.db.models.deletion
import filer.fields.file
from django.db import migrations, models


class Migration(migrations.Migration):
    """Add Render.document and Render.config, the output and options of a graphic-novel render.

    Deliberately contains ONLY this field. `makemigrations` also wanted to emit AlterField
    operations for the `action` SlugField on Action/Background/Character/Prop/Scene/Story/SyncItem:
    that is pre-existing `choices` drift between the models and the migration history, it predates
    this change, and absorbing it here would make a comic-render PR look as though it altered seven
    unrelated models. It is left where it was found.
    """

    dependencies = [
        ("filer", "0017_image__transparent"),
        ("scene", "0012_action_lettering"),
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
