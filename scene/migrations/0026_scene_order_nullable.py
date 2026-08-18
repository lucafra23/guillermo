from django.db import migrations, models


class Migration(migrations.Migration):
    """Make `Scene.order` nullable so "unset" is distinguishable from position 0.

    No data change. Existing scenes keep their integer positions; only newly created rows can be
    NULL, and `Scene.save()` fills those in before they reach the database.

    Numbered 0026 rather than 0025 on rebase. It only needs 0024, but 0025 is claimed by the
    lettering branch and two open PRs must never propose the same number: git merges duplicates
    silently and Django then refuses the graph.
    """

    dependencies = [("scene", "0024_action_lettering")]

    operations = [
        migrations.AlterField(
            model_name="scene",
            name="order",
            field=models.PositiveIntegerField(
                blank=True, db_index=True, null=True, verbose_name="order"),
        ),
        # Scene carries a bare HistoricalRecords(), so the shadow field has to move with it or
        # makemigrations reports drift on a tree nobody edited.
        migrations.AlterField(
            model_name="historicalscene",
            name="order",
            field=models.PositiveIntegerField(
                blank=True, db_index=True, null=True, verbose_name="order"),
        ),
    ]
