from django.db import migrations, models


class Migration(migrations.Migration):
    """Make `Scene.order` nullable so "unset" is distinguishable from position 0.

    Hand-written, and containing only this field. `makemigrations` also wants to emit AlterField
    operations for the `action` SlugField on seven unrelated models: that is pre-existing `choices`
    drift between the models and the migration history and is left where it was found.

    No data change. Existing scenes keep their integer positions; only newly created rows can be
    NULL, and `Scene.save()` fills those in before they reach the database.
    """

    dependencies = [("scene", "0012_action_lettering")]

    operations = [
        migrations.AlterField(
            model_name="scene",
            name="order",
            field=models.PositiveIntegerField(
                blank=True, db_index=True, null=True, verbose_name="order"),
        ),
    ]
