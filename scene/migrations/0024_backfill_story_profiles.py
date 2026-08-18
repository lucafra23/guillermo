from django.conf import settings
from django.db import migrations


def create_missing_story_profiles(apps, schema_editor):
    # Users created while scene.signals was not imported have no StoryProfile,
    # and every admin changelist dereferences it. Give them one.
    User = apps.get_model(settings.AUTH_USER_MODEL)
    StoryProfile = apps.get_model('scene', 'StoryProfile')
    db = schema_editor.connection.alias

    with_profile = StoryProfile.objects.using(db).values_list('user_id', flat=True)
    missing = User.objects.using(db).exclude(pk__in=with_profile).values_list('pk', flat=True)
    StoryProfile.objects.using(db).bulk_create(
        [StoryProfile(user_id=pk) for pk in missing]
    )


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('scene', '0023_remove_historicalscene_prompt_draft_and_more'),
    ]

    operations = [
        # Reverse is a no-op: the profiles are indistinguishable from ones the
        # user made on purpose, so unapplying must not delete them.
        migrations.RunPython(create_missing_story_profiles, migrations.RunPython.noop),
    ]
