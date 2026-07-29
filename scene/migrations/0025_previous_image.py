import django.db.models.deletion
import filer.fields.image
from django.conf import settings
from django.db import migrations


class Migration(migrations.Migration):
    """Add `previous_image` to the four models that can generate a paid plate.

    Hand-written, and it has to cover the historical models too: `simple_history` mirrors
    every field of Action/Background/Character/Prop onto Historical*, so a column added to
    only the live table makes the next save fail on the missing history column. The historical
    copies are deliberately looser -- `db_constraint=False`, `DO_NOTHING`, `related_name="+"`
    -- because a history row must survive the image it points at being deleted.
    """

    dependencies = [
        ("filer", "0017_image__transparent"),
        migrations.swappable_dependency(settings.FILER_IMAGE_MODEL),
        ("scene", "0024_action_lettering"),
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
    ] + [
        migrations.AddField(
            model_name=model,
            name="previous_image",
            field=filer.fields.image.FilerImageField(
                blank=True,
                db_constraint=False,
                editable=False,
                null=True,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name="+",
                to=settings.FILER_IMAGE_MODEL,
                verbose_name="previous image",
            ),
        )
        for model in (
            "historicalaction",
            "historicalbackground",
            "historicalcharacter",
            "historicalprop",
        )
    ]
