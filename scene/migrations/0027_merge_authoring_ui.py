from django.db import migrations


class Migration(migrations.Migration):
    """Joins the four migration leaves the authoring branches each grew.

    Every feature branch was cut from the same parent, so each added its own migration on top
    of it and `migrate` refuses to run with more than one leaf per app. Nothing is altered
    here -- the operations list is empty on purpose; this only tells Django the order the four
    already-independent changes settle in.

    Upstream will not need this file: merged one PR at a time, each branch is rebased and
    renumbered onto the previous one, so the chain stays linear. It exists because this branch
    takes all of them at once.
    """

    dependencies = [
        ("scene", "0024_backfill_story_profiles"),
        ("scene", "0025_previous_image"),
        ("scene", "0026_render_document_config"),
        ("scene", "0026_scene_order_nullable"),
    ]

    operations = []
