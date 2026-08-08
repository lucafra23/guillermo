import base64
from django.conf import settings
import json
from django.db import models

def handle_ajax_field_save(obj, field_name, value):
    """Centralized logic for saving a field via AJAX, handling Filer fields and Booleans."""
    from filer.fields.image import FilerImageField
    from filer.fields.file import FilerFileField
    from filer.models.imagemodels import Image as FilerImage
    from filer.models.filemodels import File as FilerFile
    from urllib.parse import urlparse

    field = obj._meta.get_field(field_name)
    
    if isinstance(field, (models.BooleanField, models.NullBooleanField)):
        if str(value).lower() in ['true', 'on']: value = True
        elif str(value).lower() in ['false', 'off']: value = False
        else: value = None
    elif isinstance(field, (FilerImageField, FilerFileField)) and isinstance(value, str):
        clean_path = value.split("?")[0]
        if settings.MEDIA_URL and clean_path.startswith(settings.MEDIA_URL):
            clean_path = clean_path[len(settings.MEDIA_URL):]
        elif "://" in clean_path:
            clean_path = urlparse(clean_path).path
            if settings.MEDIA_URL and clean_path.startswith(settings.MEDIA_URL):
                clean_path = clean_path[len(settings.MEDIA_URL):]

        filer_model = FilerImage if isinstance(field, FilerImageField) else FilerFile
        filer_instance = filer_model.objects.filter(file=clean_path).first()
        if filer_instance:
            value = filer_instance
        else:
            raise ValueError(f"Filer object not found for path: {clean_path}")

    setattr(obj, field.get_attname(), value if value != "" else None)
    obj.save()


def get_genai_client(user=None):
    from google import genai
    if user and hasattr(user, 'agent_profile') and user.agent_profile.google_api_key:
        api_key = user.agent_profile.google_api_key.api_key
        genai_client = genai.Client(
            vertexai=True,
            api_key=api_key
        )
    else:
        raise Exception("User does not have an API key configured. Please set up your API key in your profile settings.")
    return genai_client


# Set up the wave file to save the output:
def wave_file(filename, pcm, channels=1, rate=24000, sample_width=2):
    import wave
    with wave.open(filename, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(rate)
        wf.writeframes(pcm)