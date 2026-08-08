from django.apps import AppConfig
from django.db.models.signals import post_migrate


class SceneConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'scene'

    def ready(self):
        # Import signals module to ensure receivers are connected
        from agent.signals import sync_categories
        post_migrate.connect(sync_categories, sender=self)