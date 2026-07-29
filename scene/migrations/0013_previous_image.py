import django.db.models.deletion
import filer.fields.image
from django.db import migrations


class Migration(migrations.Migration):
    """Add `previous_image` to the four models that can generate a paid plate.

    Hand-written. `makemigrations` also wanted to emit AlterField operations for the `action`
    SlugField on Action/Background/Character/Prop/Scene/Story/SyncItem: that is pre-existing
    `choices` drift between the models and the migration history, it predates this change, and
    absorbing it here would make a plate-history PR look as though it altered seven unrelated
    models. It is left where it was found.
    """

    dependencies = [
        ("filer", "0017_image__transparent"),
        ("scene", "0012_action_lettering"),
    ]

    operations = [
        migrations.AddField(
            model_name=model,
            name="previous_image",
            field=filer.fields.image.FilerImageField(
                blank=True,
                editable=False,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name=related,
                to="filer.image",
                verbose_name="previous image",
            ),
        )
        for model, related in (
            ("action", "panel_previous"),
            ("background", "background_previous"),
            ("character", "character_previous"),
            ("prop", "prop_previous"),
        )
    ]
