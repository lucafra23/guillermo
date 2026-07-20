import base64

from google import genai
import random
import os
import time
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.utils.module_loading import import_string
from task.mixins import  AfterSaveActionMixin
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils.translation import gettext_lazy as _
from agent.utils import wave_file
from PIL import Image
from io import BytesIO
from filer.fields.image import FilerImageField
from filer.fields.file import FilerFileField
from filer.models.imagemodels import Image as FilerImage
from django.utils.text import slugify
from easy_thumbnails.files import get_thumbnailer
from google.auth.credentials import AnonymousCredentials
from project.settings import TASK_TYPE_GENERATE_SCENE, TASK_TYPE_GENERATE_SCENE_ACTIONS, TASK_TYPE_GENERATE_TEXT, TASK_TYPE_GENERATE_VOICE
from task.models import Task, TaskHolder
from PIL import Image

from google.auth.credentials import Credentials

# Create a mock credential that satisfies all internal SDK token fetches
class MockVertexCredentials(Credentials):
    def __init__(self, project_id):
        super().__init__()
        self.project_id = project_id
        self.token = "dummy-token-to-satisfy-sdk-parser"

    def refresh(self, request):
        # Prevent the SDK from crashing when it tries to refresh
        self.token = "dummy-token-to-satisfy-sdk-parser"

class GetContentsMixin:
    PRESET_IMAGE = "image"
    PRESET_REFINE = "refine"
    PRESET_VIDEO = "video_image"
    PRESET_OMNI_VIDEO = "omni_video"
    PRESET_VIDEO_FIRST_LAST = "video_first_last"
    PRESET_COMIC = "comic"
    PRESET_VOICE = "voice"
    PRESET_CHARACTER = "character"
    PRESET_WRITER = "writer"
    PRESET_REFINE_PROMPT = "refine_prompt"
    PRESET_SCENE = "generate_scene"
    

    @property
    def messages(self):
        return Message.objects.filter(
            content_type=ContentType.objects.get_for_model(self.__class__),
            object_id=self.pk
        ).order_by('created_at')

    def get_contents(self, generate_self=True, preset=None,):
         # remove generate self and add preset regenerate_image
        parts = [self.context_text(generate_self=generate_self, preset=preset)]
        if not generate_self or preset in [self.PRESET_REFINE, self.PRESET_COMIC]:
            if hasattr(self, 'image') and self.image:
                parts.append(self.image)
        return [p for p in parts if p is not None and (not isinstance(p, str) or p.strip() != "")]


    def get_agent(self, output_type):
        agent = Agent.objects.filter(output_type=output_type).first()
        if agent is None:
            raise ValueError(f"No agent configured for output type: {output_type}")
        return agent
    
    def get_agent_by_name(self, name):
        agent = Agent.objects.filter(name=name).first()
        if agent is None:
            raise ValueError(f"No agent configured for name: {name}")   
        return agent

    def generate_image(self, user=None):
        agent = self.get_agent(Agent.OUTPUT_TYPE_IMAGE)
        self.image = agent.generate(self, preset=self.PRESET_IMAGE, user=user, target_field="image")
        self.save()
        return self.image
    
    def refine_image(self, save=True, user=None):
        image_agent = self.get_agent(Agent.OUTPUT_TYPE_IMAGE)
        out = image_agent.generate(self, preset=self.PRESET_REFINE, user=user, target_field="image")
        if save and out:
            self.image = out
            self.save()
        return out
    
    def generate_video(self, preset, user=None):
        agent = self.get_agent(Agent.OUTPUT_TYPE_VIDEO)
        self.video = agent.generate(self, preset=preset, user=user, target_field="video")
        self.save()
        return self.video

    def generate_image_omni_video(self, preset, user=None, target_field="video"):
        agent = self.get_agent(Agent.OUTPUT_TYPE_IMAGE_OMNI_VIDEO)
        self.video = agent.generate(self, preset=preset, user=user, target_field="video")
        pass

    def generate_voice(self, preset, user=None, target_field="audio_voice"):
        agent = self.get_agent(Agent.OUTPUT_TYPE_VOICE)
        out = agent.generate(self, preset=preset, user=user, target_field=target_field)
        setattr(self, target_field, out)
        self.save()
        return getattr(self, target_field)
    

    def generate_scene(self, preset=PRESET_SCENE, user=None):
        agent = Agent.objects.filter(schema=settings.SCHEMA_SCENE).first()
        out = agent.generate(self, preset=preset, user=user, target_field="scene")
        return out

    def refine_image(self, save=True, user=None):
        image_agent = self.get_agent(Agent.OUTPUT_TYPE_IMAGE)
        out = image_agent.generate(self, preset=self.PRESET_REFINE, user=user, target_field="image")
        if save and out:
            self.image = out
            self.save()
        return out
    
    def generate_text(self, preset=PRESET_REFINE_PROMPT, message=None,  target_field="prompt",  agent=None, user=None):
        if agent is None:
            agent = self.get_agent(Agent.OUTPUT_TYPE_TEXT)
        out = agent.generate(self, preset=preset, message_part=message, user=user, target_field=target_field)
        if out is not None:
            setattr(self, target_field, out)
            self.save()
        return out
    
    def refine_prompt(self, save=True, user=None, agent=None):
        text_agent = agent or self.get_agent(Agent.OUTPUT_TYPE_TEXT)
        out = text_agent.generate(self, preset=self.PRESET_REFINE_PROMPT, user=user, target_field="prompt")
        if save and out:
            self.image = out
            self.save()
        return out

class AgentModel(models.Model):
    name = models.CharField(_("name"), max_length=100, default="name")
    def __str__(self):
        return "{}".format(self.name)

class GoogleVoice(models.Model):
    name = models.CharField(_("name"), max_length=100, default="name")
    description = models.TextField(_("description"), blank=True, null=True)

    def __str__(self):
        return "{}".format(self.name)


class Prompt(models.Model):
    CHOICES = (
        ("general", _("General")),
        (GetContentsMixin.PRESET_REFINE, _("Refine")),
        (GetContentsMixin.PRESET_VIDEO, _("Video")),
        (GetContentsMixin.PRESET_VIDEO_FIRST_LAST, _("Video First Last")),
        (GetContentsMixin.PRESET_COMIC, _("Comic")),
        (GetContentsMixin.PRESET_WRITER, _("Writer")),
        (GetContentsMixin.PRESET_CHARACTER, _("Character")),
        (GetContentsMixin.PRESET_REFINE_PROMPT, _("Refine Prompt")),
        (GetContentsMixin.PRESET_SCENE, _("Sync Scene")),
    )
    
    name= models.CharField(_("name"), max_length=100, default="name")
    prompt = models.TextField(null=True, blank=True)
    category = models.CharField(max_length=100, default="general", choices=CHOICES)
    content_types = models.ManyToManyField(ContentType, blank=True)
    is_global = models.BooleanField(_("is global"), default=False)
    order = models.IntegerField(_("order"), default=0)


    @classmethod
    def instructions(cls, preset, obj):
        from_preset =  [item.prompt for item in cls.objects.filter(category=preset, is_global=True) ]
        content_type = ContentType.objects.get_for_model(obj.__class__)
        from_content = [item.prompt for item in cls.objects.filter(content_types=content_type, is_global=True)]
        out = from_preset + from_content
        if hasattr(obj, 'get_instructions') and obj.get_instructions:
            obj_instrs = [item.prompt for item in obj.get_instructions(preset)]
            out.extend(obj_instrs)
        return out

    @classmethod
    def prompt_for_model(cls, obj):
        content_type = ContentType.objects.get_for_model(obj.__class__)
        from_content = [item.prompt for item in cls.objects.filter(content_types=content_type)]
        return from_content

    def __str__(self):
        return "{}".format(self.name)

    
class Agent(models.Model):
    OUTPUT_TYPE_IMAGE = "image"
    OUTPUT_TYPE_TEXT = "text"
    OUTPUT_TYPE_VOICE = "voice"
    
    OUTPUT_TYPE_STRUCTURED = "structured"
    OUTPUT_TYPE_VIDEO = "video"
    OUTPUT_TYPE_IMAGE_OMNI_VIDEO = "image_omni_video"

    
    name = models.CharField(_("name"), max_length=100, default="name")
    instructions = models.ManyToManyField("Prompt", verbose_name=_("instructions"), related_name='agents', blank=True)
    agent_model = models.ForeignKey(AgentModel, verbose_name=_("agent model"), related_name='agents', on_delete=models.CASCADE)
    agent_model_enterprise = models.ForeignKey(AgentModel, verbose_name=_("agent model"),blank=True, null=True,  related_name='enterprise_agents', on_delete=models.CASCADE)

    output_type = models.CharField(
        _("output type"),
        max_length=100,
        default=OUTPUT_TYPE_TEXT,
        choices=getattr(settings, "AGENT_OUTPUT_TYPE_CHOICES", [
            (OUTPUT_TYPE_IMAGE, _("Image")),
            (OUTPUT_TYPE_TEXT, _("Text")),
            (OUTPUT_TYPE_STRUCTURED, _("Structured")),
            (OUTPUT_TYPE_VIDEO, _("Video")),
            (OUTPUT_TYPE_IMAGE_OMNI_VIDEO, _("Image Omni Video")),
            (OUTPUT_TYPE_VOICE, _("Voice")),
        ])
    )
    schema = models.CharField(
        _("schema"),
        max_length=100,
        blank=True,
        null=True,
        choices=settings.AGENT_SCHEMA_CHOICES
    )

    def get_schema_class(self):
        schema_path = settings.AGENT_SCHEMAS.get(self.schema)
        return import_string(schema_path) if schema_path else None

    def get_genai_client(self, user):
        if user and hasattr(user, 'agent_profile') and user.agent_profile.google_api_key:
            from google.genai import types
            if AgentApiKey.objects.filter(agent=self, profile=user.agent_profile).exists():
                google_api_key = AgentApiKey.objects.get(agent=self, profile=user.agent_profile).api_key
            else:
                google_api_key = user.agent_profile.google_api_key
            if google_api_key.enterprise:
                return genai.Client(api_key=google_api_key.api_key, vertexai=True, http_options=types.HttpOptions(timeout=settings.GENAI_REQUEST_TIMEOUT_MS))                
            else:
                return genai.Client(api_key=google_api_key.api_key, http_options=types.HttpOptions(timeout=settings.GENAI_REQUEST_TIMEOUT_MS))
        else:
            raise ValueError("User does not have an API key configured. Please set up your API key in your profile settings.")

    def save_usage(self, user, response, obj=None, preset=None):
        usage = getattr(response, 'usage_metadata', None)
        if not usage:
            return

        usage_dict = {
            "prompt_token_count": usage.prompt_token_count,
            "candidates_token_count": usage.candidates_token_count,
            "total_token_count": usage.total_token_count,
            "api_key_id": user.agent_profile.google_api_key.id if user.agent_profile.google_api_key else None,
            
            # Fields for advanced features (if available)
        }
        
        TokenUsage.objects.create(
            user=user,
            agent=self,
            json_report=usage_dict,
            tokens=usage.total_token_count,
            preset=preset,
            content_type=ContentType.objects.get_for_model(obj.__class__) if obj else None,
            object_id=obj.pk if obj else None
        )

    def __str__(self):
        return "{}".format(self.name)
    
    def extract_text(self, response, prompt_obj):
        try:
            return response.text
        except ValueError:
            if response.candidates:
                return f"Response blocked. Finish reason: {response.candidates[0].finish_reason}"
            return "No text was generated."

    def generate_voice(self, preset, prompt_obj, message=None, contents=None, user=None):
        # Check for errors if voice is not generated
        from google.genai import types
        if not contents:
            contents = prompt_obj.get_contents(generate_self=True, preset=preset)
        out = None
        client = self.get_genai_client(user)
        response = client.models.generate_content(
            model=self.agent_model.name,
            contents=contents['prompt'],
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=contents['voice'],
                        )
                    )
                ),
            )
        )
        if not response.candidates:
            finish_reason = getattr(response, 'prompt_feedback', 'No candidates returned, reason unknown.')
            raise ValueError(f"Voice generation failed. The API returned no candidates. Reason: {finish_reason}")

        data = response.candidates[0].content.parts[0].inline_data.data
        self.save_usage(user, response, obj=prompt_obj, preset=preset)
        name = f"voice_{slugify(prompt_obj.__class__.__name__)}_{slugify(prompt_obj.name)}_{slugify(self.name)}_{random.randint(1000,9999)}.wav"
        filepath_relative = f"agent_voices/{name}"
        filepath_abs = os.path.join( settings.MEDIA_ROOT, filepath_relative)
        wave_file(filepath_abs, data)
        out = FilerImage.objects.create(
            original_filename=name,
            file=filepath_relative,
            name=name
        )
        return out
    
    def generate_text(self, preset, prompt_obj, message=None, user=None, contents=None):
        from google.genai import types
        instructions = self.get_instructions(user=user, preset=preset, obj=prompt_obj)
        message.set_instructions(instructions)
        config = types.GenerateContentConfig(
            system_instruction=self.get_instructions(user=user, preset=preset, obj=prompt_obj),
        )
        with self.get_genai_client(user) as client:
            response = client.models.generate_content(
                model=self.agent_model.name,
                contents=contents,
                config=config
            )
            self.save_usage(user, response, obj=prompt_obj, preset=preset)
            out = self.extract_text(response, prompt_obj)
        return out

    def generate_image(self, preset, prompt_obj, message=None, user=None, contents=None):
        from google.genai import types
        instructions = self.get_instructions(user=user, preset=preset, obj=prompt_obj)
        message.set_instructions(instructions)
        contents.extend(instructions)
        config = types.GenerateContentConfig(
            image_config=types.ImageConfig(
                aspect_ratio="9:16",
            )
        )
        with self.get_genai_client(user) as client:
            response = client.models.generate_content(
                model=self.agent_model.name,
                contents=contents,
                config=config
            )
            self.save_usage(user, response, obj=prompt_obj, preset=preset)
            out = self.save_image(response, prompt_obj)
        return out
    

    def generate_image_omni_video(self, preset, prompt_obj,message=None, user=None, contents=None):
        with self.get_genai_client(user) as client:
            interaction = client.interactions.create(
                model="gemini-omni-flash-preview",
                input=[
                     {
                        "type": "image", 
                        "data": contents["image"], 
                        "mime_type": "image/png" # Use image/jpeg if using a .jpg file
                    },
                    {
                        "type": "text", 
                        "text": contents["prompt"]
                    }
                    ]
                    
            )
            if interaction.output_video and interaction.output_video.data:
                name = f"video_{slugify(prompt_obj.__class__.__name__)}_{slugify(prompt_obj.name)}_{slugify(self.name)}_{random.randint(1000,9999)}.mp4"
                filepath_relative = f"agent_videos/{name}"
                filepath_abs = os.path.join( settings.MEDIA_ROOT, filepath_relative)
                 # The API payload returns base64 data; decode it back to native raw binary bytes
                video_bytes = base64.b64decode(interaction.output_video.data)

                # Save the binary data as an mp4 file
                with open(filepath_abs, "wb") as video_file:
                    video_file.write(video_bytes)
                out = FilerImage.objects.create(
                    original_filename=name,
                    file=filepath_relative,
                    name=name
                )
                return out
            else:
                return None
        

    def generate_video(self, preset, prompt_obj, user=None, contents=None):
        # Check for errors if a video is not generated
        from google.genai import types
        out = None
        client = self.get_genai_client(user)
        if contents is None:
            contents = prompt_obj.get_contents(generate_self=True, preset=preset)
        if preset == GetContentsMixin.PRESET_VIDEO:
            operation = client.models.generate_videos(
                model=self.agent_model.name,
                prompt=contents['prompt'],
                image=contents['image'] if 'image' in contents else None
            )
        elif preset == GetContentsMixin.PRESET_VIDEO_FIRST_LAST:
            operation = client.models.generate_videos(
                model=self.agent_model.name,
                prompt=contents['prompt'],
                image=contents['image_first'] if 'image_first' in contents else None,
                config=types.GenerateVideosConfig(
                    last_frame=contents['image_last'] if 'image_last' in contents else None
                ),
            )
        # Poll the operation status until the video is ready.
        while not operation.done:
            print("Waiting for video generation to complete...")
            time.sleep(5)
            operation = client.operations.get(operation)
        # Download the generated video.
        if operation.response.generated_videos is None:
            raise Exception(operation.response.rai_media_filtered_reasons)
        generated_video = operation.response.generated_videos[0]
        self.save_usage(user, operation.response, obj=prompt_obj, preset=preset)

        name = f"video_{slugify(prompt_obj.__class__.__name__)}_{slugify(prompt_obj.name)}_{slugify(self.name)}_{random.randint(1000,9999)}.mp4"
        filepath_relative = f"agent_videos/{name}"
        filepath_abs = os.path.join( settings.MEDIA_ROOT, filepath_relative)
        generated_video.video.save(filepath_abs)
        out = FilerImage.objects.create(
            original_filename=name,
            file=filepath_relative,
            name=name
        )
        return out

    def save_image(self, response, prompt_obj):
        # Check for errors if an image is not generated
        out = None
        from google.genai.types import FinishReason
        if response.candidates[0].finish_reason != FinishReason.STOP:
            reason = response.candidates[0].finish_reason
            raise ValueError(f"Prompt Content Error: {reason}")
        
        for part in response.candidates[0].content.parts:
            if part.inline_data:
                image_data = BytesIO(part.inline_data.data)
                out =  Image.open(image_data)
                name = f"{slugify(prompt_obj.__class__.__name__)}_{slugify(prompt_obj.name)}_{slugify(self.name)}_{random.randint(1000,9999)}"
                filepath_relative = f"agent_images/{name}.png"
                filepath_abs = os.path.join( settings.MEDIA_ROOT, filepath_relative)
                out.save(filepath_abs, format="PNG")
                out = FilerImage.objects.create(
                    original_filename=name,
                    file=filepath_relative,
                    name=name
                )
        return out
    
    def get_instructions(self, user=None, preset=None, obj=None):
        instructions = [ item.prompt for item in self.instructions.all()]
        if preset:
            instructions += Prompt.instructions(preset, obj)
        return [i for i in instructions if i and str(i).strip() != ""]
    
    def generate_structured(self, preset, prompt_obj, message=None, user=None, contents=None):
        from google.genai import types
        schema_class = self.get_schema_class()
        if not schema_class:
            return None
        instructions = self.get_instructions(user=user, preset=preset, obj=prompt_obj)
        message.set_instructions(instructions)
        config = types.GenerateContentConfig(
            system_instruction=instructions,
            response_mime_type="application/json",
            response_schema=schema_class,
            temperature=0.1,
        )

        with self.get_genai_client(user) as client:
            response = client.models.generate_content(
                model=self.agent_model.name,
                contents=contents,
                config=config
            )
            self.save_usage(user, response, obj=prompt_obj, preset=preset)
            data = schema_class.model_validate_json(response.text)
            out = data.sync_model(prompt_obj) if hasattr(data, "sync_model") else data
        return out

    def generate(self, obj, preset=None, user=None, target_field=None, message_part=None):
        parts = obj.get_contents(generate_self=True, preset=preset)
        if message_part:
            parts.append(message_part)
        message = Message.create_message(
            obj,
            agent=self,
            user=user,
            target_field=target_field,
        )
        message.set_input(parts)
        contents = Message.to_google_types(parts)
        out = None
        if self.output_type == self.OUTPUT_TYPE_IMAGE_OMNI_VIDEO:
            out = self.generate_image_omni_video(preset, obj,message=message, user=user, contents=contents)
        elif self.output_type == self.OUTPUT_TYPE_VIDEO:
            out = self.generate_video(preset, obj, message=message, user=user, contents=contents)
        elif self.output_type == self.OUTPUT_TYPE_VOICE:
            out = self.generate_voice(preset, obj, message=message, user=user, contents=contents)
        elif self.output_type == self.OUTPUT_TYPE_IMAGE:
            out = self.generate_image(preset, obj, message=message, user=user, contents=contents)
        elif self.output_type == self.OUTPUT_TYPE_TEXT:
            out = self.generate_text(preset, obj, message=message, user=user, contents=contents)
        elif self.output_type == self.OUTPUT_TYPE_STRUCTURED:
            out = self.generate_structured(preset, obj, message=message, user=user, contents=contents)
        message.set_output(out)
        return out



class TokenUsage(models.Model):
    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)
    agent = models.ForeignKey(Agent, verbose_name=_("agent"), related_name='token_usages', on_delete=models.SET_NULL, null=True, blank=True)
    task = models.ForeignKey(Task, verbose_name=_("task"), related_name='token_usages', on_delete=models.SET_NULL, null=True, blank=True)
    tokens = models.PositiveIntegerField(_("tokens"), default=0)
    preset = models.CharField(_("preset"), max_length=100, null=True, blank=True)
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, null=True, blank=True)
    object_id = models.PositiveIntegerField(null=True, blank=True)
    content_object = GenericForeignKey('content_type', 'object_id')
    json_report = models.JSONField(null=True, blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name=_("user"), related_name='token_usages', on_delete=models.SET_NULL, null=True, blank=True)

class GoogleApiKey(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name=_("user"), related_name='api_keys', on_delete=models.CASCADE)
    name = models.CharField(_("name"), unique=True, max_length=255, null=True, blank=True)
    api_key = models.TextField(_("api key"), null=True, blank=True)
    enterprise = models.BooleanField(_("enterprise"), default=False)
    project = models.CharField(_("project"), max_length=255, null=True, blank=True)

    def __str__(self):
        return "{}-{}".format(self.name, self.user.username)

class AgentApiKey(models.Model):
    agent = models.ForeignKey(Agent, verbose_name=_("agent"), related_name='api_keys', on_delete=models.CASCADE)
    api_key = models.ForeignKey(GoogleApiKey, verbose_name=_("agent"), related_name='api_keys', on_delete=models.CASCADE)
    profile = models.ForeignKey('AgentProfile', verbose_name=_("profile"), related_name='api_keys', on_delete=models.CASCADE, null=True, blank=True)

class AgentProfile(models.Model):

    user = models.OneToOneField(settings.AUTH_USER_MODEL, verbose_name=_("user"), related_name='agent_profile', on_delete=models.CASCADE)
    credits = models.PositiveIntegerField(_("credits"), default=0)
    google_api_key = models.ForeignKey(GoogleApiKey, verbose_name=_("google api key"), related_name='agent_profiles', on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        verbose_name = _('AI User Profile')
        verbose_name_plural = _('AI User Profiles')

    def __str__(self):
        return "{}".format(self.user.username)

class Message(models.Model):
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    content_object = GenericForeignKey('content_type', 'object_id')
    agent = models.ForeignKey(Agent, verbose_name=_("agent"), on_delete=models.SET_NULL, null=True, blank=True, related_name='chat_history')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name=_("user"), on_delete=models.SET_NULL, null=True, blank=True, related_name='agent_messages')
    target_field = models.CharField(_("target field"), max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']

    def parts_input(self):
        return self.parts.filter(part_type=MessagePart.PART_TYPE_INPUT).order_by('order')

    def parts_output(self):
        return self.parts.filter(part_type=MessagePart.PART_TYPE_OUTPUT).order_by('order')

    def parts_instructions(self):
        return self.parts.filter(part_type=MessagePart.PART_TYPE_INSTRUCTIONS).order_by('order')

    @classmethod
    def create_message(cls, content_object, agent=None, user=None, target_field=""):
        message = cls.objects.create(
            content_object=content_object,
            agent=agent,
            user=user,
            target_field=target_field
        )
        return message#
    
    @classmethod
    def to_google_types(cls, parts):
        """
        Convert a list of parts (str, FilerImage, PIL Image) into a list
        of google.genai.types.Part compatible objects.
        """
        if isinstance(parts, dict):
            out = {}
            for key, part in parts.items():
                if isinstance(part, str):
                    out[key] = part
                elif isinstance(part, FilerImage):
                    out[key] = Image.open(part.file.path)
                elif isinstance(part, Image.Image):
                    out[key] = part
        elif isinstance(parts, list):
            out = []
            for part in parts:
                if isinstance(part, str):
                    out.append(part)
                elif isinstance(part, FilerImage):
                    out.append(Image.open(part.file.path))
                elif isinstance(part, Image.Image):
                    out.append(part)
        return out

    def _create_part(self, part_data, order, part_type, key=None):
        """Helper method to create a single MessagePart."""
        part_kwargs = {
            'message': self,
            'order': order,
            'part_type': part_type,
            'key': key
        }
        if isinstance(part_data, str):
            part_kwargs['text'] = part_data
        elif isinstance(part_data, FilerImage):
            part_kwargs['image'] = part_data
        elif isinstance(part_data, Image.Image):
            raise ValueError("PIL Image objects are not supported directly. Please save the image to a FilerImage first.")
        elif isinstance(part_data, dict):
            part_kwargs['json'] = part_data
        else:
            # Skip creating a part if the data type is not supported
            return

        MessagePart.objects.create(**part_kwargs)

    def _create_parts_from_list(self, parts_list, part_type):
        """Creates MessagePart objects from a list of data."""
        for i, part in enumerate(parts_list):
            self._create_part(part, order=i, part_type=part_type)

    def _create_parts_from_dict(self, parts_dict, part_type):
        """Creates MessagePart objects from a dictionary of data."""
        for i, (key, value) in enumerate(parts_dict.items()):
            self._create_part(value, order=i, part_type=part_type, key=key)

    def set_input(self, parts_data):
        """Creates input MessagePart objects from a list of data."""
        if isinstance(parts_data, dict):
            self._create_parts_from_dict(parts_data, MessagePart.PART_TYPE_INPUT)
        elif isinstance(parts_data, list):
            self._create_parts_from_list(parts_data, MessagePart.PART_TYPE_INPUT)

    def set_instructions(self, instructions_data):
        """Creates instruction MessagePart objects from a list of strings."""
        if isinstance(instructions_data, list):
            for i, instruction_text in enumerate(instructions_data):
                if isinstance(instruction_text, str):
                    MessagePart.objects.create(
                        message=self, part_type=MessagePart.PART_TYPE_INSTRUCTIONS, text=instruction_text, order=i
                    )

    def set_output(self, output_data):
        """Creates a single output MessagePart from the generation result."""
        if output_data is None:
            return

        self._create_part(output_data, order=999, part_type=MessagePart.PART_TYPE_OUTPUT)

class MessagePart(models.Model):
    PART_TYPE_INPUT = 'input'
    PART_TYPE_OUTPUT = 'output'
    PART_TYPE_INSTRUCTIONS = 'instructions'
    
    PART_TYPE_CHOICES = [
        (PART_TYPE_INPUT, _('Input')),
        (PART_TYPE_OUTPUT, _('Output')),
        (PART_TYPE_INSTRUCTIONS, _('Instructions')),
    ]

    message = models.ForeignKey(Message, related_name='parts', on_delete=models.CASCADE)
    part_type = models.CharField(_("part type"), max_length=31, choices=PART_TYPE_CHOICES, default=PART_TYPE_INPUT)
    text = models.TextField(_("text"), blank=True, null=True)
    json = models.JSONField(_("json data"), blank=True, null=True)
    file = FilerFileField(verbose_name=_("file"), null=True, blank=True, on_delete=models.SET_NULL, related_name='message_parts_file')
    image = FilerImageField(verbose_name=_("image"), null=True, blank=True, on_delete=models.SET_NULL, related_name='message_parts_image')
    order = models.PositiveIntegerField(_("order"), default=0)
    key = models.CharField(_("key"), max_length=255, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True, blank=True, null=True)

    class Meta:
        ordering = ['order']