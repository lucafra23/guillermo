from django.db.models.signals import post_save
from django.contrib.auth.models import User
from django.contrib.auth import get_user_model
from django.dispatch import receiver
from .models import AgentProfile
from .models import AgentProfile, PromptCategory

@receiver(post_save, sender=User)
def create_profile(sender, instance, created, **kwargs):
    if created:
        AgentProfile.objects.create(user=instance)


def sync_categories(sender, **kwargs):
    """
    Iterates over all models in this app after migrations run.
    If a model defines a static category method, run it to sync rows.
    """
    app_config = kwargs.get("app_config")
    if not app_config or app_config.name != sender.name:
        return

    # 1. Loop through all registered models in this app
    for model in app_config.get_models():
        
        # 2. Check if the model has your custom hook (e.g., get_static_categories)
        cat_func = getattr(model, "sync_prompt_categories", None)
        if cat_func:
            for cat_data in cat_func():
                # If categories are strings:
                # Use square brackets for tuple access and target the correct model
                PromptCategory.objects.get_or_create(slug=cat_data[0], defaults={'name': cat_data[1]})
