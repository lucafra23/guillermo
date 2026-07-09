from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("scene", "0011_alter_action_shot_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="action",
            name="lettering",
            field=models.JSONField(blank=True, null=True, verbose_name="lettering"),
        ),
    ]
