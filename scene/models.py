from django.db import models
from django.conf import settings
from agent.models import Agent, Prompt
from filer.fields.image import FilerImageField, FilerFileField
from agent.models import GetContentsMixin, GoogleVoice
from scene.mixins import (
    EmailSenderMixin, UserCreatorMixin, ModelDisplayMixin, RenderTypeMixin
)
from .schemas import StoryElementsSchema, BackgroundSchema, CharacterSchema, PropSchema, VoiceSchema, GoogleVoiceSchema

from task.mixins import  AfterSaveActionMixin
from task.models import TaskHolder, Task
import yaml
from .schemas import ActionSchema
from django.utils.translation import gettext_lazy as _
from django.template.loader import render_to_string
from django.utils.safestring import mark_safe
from PIL import Image

def dashboard_callback(request, context):
    stories = []
    if request.user.is_authenticated:
        profile = getattr(request.user, 'story_profile', None)
        if profile:
            current_story = profile.get_current_story()
            if current_story:
                stories = Story.objects.filter(pk=current_story.pk)
            else:
                stories = Story.objects.filter(authors__user=request.user).distinct()
        else:
            stories = Story.objects.filter(authors__user=request.user).distinct()
    context.update({'stories': stories})
    return context

class Theme(models.Model):
    name = models.CharField(_("name"), max_length=100, default='New Game')
    prompt = models.TextField(_("prompt"), null=True, blank=True)
    
    def __str__(self):
        return "{}".format(self.name)


class Style(models.Model, GetContentsMixin, ModelDisplayMixin):
    prompt = models.TextField(_("prompt"), null=True, blank=True)
    name = models.CharField(_("name"), max_length=100, default="")
    global_default = models.BooleanField(_("global default"), default=False)
    
    def __str__(self):
        return "{}".format(self.name)
    
    def context_text(self, generate_self=True, preset=None):
        if preset == self.PRESET_REFINE:
            return self.prompt_refine
        return self.prompt

class Preset(models.Model):
    name = models.CharField(_("name"), null=True, blank=True)
    preset = models.JsonField(_("preset"), null=True, blank=True)
    

class Author(models.Model, UserCreatorMixin):
    story = models.ForeignKey('scene.Story', verbose_name=_("story"), related_name='authors', on_delete=models.CASCADE, null=True, blank=True)
    order = models.IntegerField(_("order"), default=0)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name=_("user"), related_name='authors', on_delete=models.CASCADE, null=True, blank=True)
    email = models.EmailField(_("email"), null=True, blank=True)

    def save(self, *args, **kwargs):
        if not self.email and not self.user:
            raise ValueError("Either email or user must be provided.")
        if self.email and self.user is None:
            user = self.create_user(self.story, self.email)
            self.user = user
        super().save(*args, **kwargs)
           

    def __str__(self):
        return "{}".format(self.user.username if self.user else self.email)

    def username(self):
        return self.user.username if self.user else self.email
    
    def scene_count(self):
        return self.scenes.count()

class Story(AfterSaveActionMixin, RenderTypeMixin, models.Model, GetContentsMixin, TaskHolder, ModelDisplayMixin):
    name = models.CharField(_("name"), max_length=200)
    order = models.PositiveIntegerField(_("order"), default=0, db_index=True)
    style = models.ForeignKey(Style, verbose_name=_("style"), related_name='stories', null=True, blank=True, on_delete=models.CASCADE)
    theme = models.ForeignKey('Theme', verbose_name=_("theme"), on_delete=models.CASCADE, null=True, blank=True)
    group = models.ForeignKey('scene.StoryGroup', verbose_name=_("group"), help_text=_("Auto create authors from this group, it happens only when the script is saved for the first time"),  on_delete=models.CASCADE, null=True, blank=True, related_name='stories')

    prompt = models.TextField(_("prompt"), null=True, blank=True, default="#Plot\n")
    prompt_refine = models.TextField(_("prompt refine"), null=True, blank=True)
    action = models.SlugField(_("action"), choices=settings.TASK_TYPE_CHOICES, null=True, blank=True)
    mentor = models.ForeignKey("agent.Agent", verbose_name=_("mentor"), related_name='mentors_stories', on_delete=models.CASCADE, null=True, blank=True)

    RENDER_TYPE_FILM = 'film'
    RENDER_TYPE_GRAPHIC_NOVEL = 'comic'
    RENDER_TYPE_ANIMATIC = 'animatic'

    RENDER_TYPE_CHOICES = [
        (RENDER_TYPE_FILM, _('Film')),
        (RENDER_TYPE_GRAPHIC_NOVEL, _('Graphic Novel')),
        (RENDER_TYPE_ANIMATIC, _('Animatic')),
    ]

    render_type = models.CharField(
        _("render type"),
        max_length=50,
        choices=RENDER_TYPE_CHOICES,
        default=getattr(settings, 'DEFAULT_RENDER_TYPE', RENDER_TYPE_ANIMATIC),
        help_text=_("The default render format for this story.")
    )
    config = models.ForeignKey(Preset, verbose_name=_("config"), related_name='stories', on_delete=models.SET_NULL, null=True, blank=True
    
    def __str__(self):
        return "{}".format(self.name)

    class Meta:
        verbose_name = _('Story')
        verbose_name_plural = _('Stories')

    def get_mentor(self):
        out = self.mentor
        if not out:
            out = Agent.objects.filter(output_type=Agent.OUTPUT_TYPE_TEXT).first()
        return out
    
    def get_locations(self):
        return self.backgrounds.all().order_by('name')

    def get_cast(self):
        return self.characters.all().order_by('name')

    def get_slides(self):
        slides = []
        for scene in self.scenes.all().order_by('order'):
            for action in scene.actions.all().order_by('order'):
                slides.append(action.slide())
        return slides

    def get_props(self):
        return Prop.objects.filter(story=self).order_by('name')

    def get_voices(self):
        return Voice.objects.filter(story=self).order_by('name')

    def import_group_members(self):
        if self.group is not None:
            for member in self.group.users.all():
                if not Author.objects.filter(story=self, user=member).exists():
                    Author.objects.create(story=self, user=member)

    def add_author(self, user):
        out = False
        if not Author.objects.filter(story=self, user=user).exists():
            Author.objects.create(story=self, user=user)
            out = True
        return out

    def save(self, *args, **kwargs):
        if not self.style:
            default_style = Style.objects.filter(global_default=True).first()
            if default_style:
                self.style = default_style
                
        do_import = not self.id and self.group is not None
        super().save(*args, **kwargs)
        if do_import:
            self.import_group_members()

    def intro(self):
        intro_action = Action.objects.filter(is_intro='story', scene__story=self).first()
        if intro_action:
            return intro_action.intro()
        return None
    
    def items(self):
        """
        Renders a summary dropdown of story elements using a template.
        """
        locations = self.get_locations()
        characters = self.get_cast()
        props = self.get_props()
        voices = self.get_voices()
        scenes = self.scenes.all()
        actions = Action.objects.filter(scene__story=self)
        video_actions = VideoAction.objects.filter(scene__story=self)
        comic_actions = ComicAction.objects.filter(scene__story=self)
        voice_actions = VoiceAction.objects.filter(scene__story=self)

        context = {
            'locations': locations,
            'characters': characters,
            'props': props,
            'voices': voices,
            'scenes': scenes,
            'actions': actions,
            'video_actions': video_actions,
            'comic_actions': comic_actions,
            'voice_actions': voice_actions,
            'count': (locations.count() + characters.count() + props.count() + 
                      voices.count() + scenes.count() + actions.count()),
            "prop_ids": ",".join([str(p.id) for p in props]),
            "char_ids": ",".join([str(c.id) for c in characters]),
            "loc_ids": ",".join([str(l.id) for l in locations]),
            "voice_ids": ",".join([str(v.id) for v in voices]),
            "action_ids": ",".join([str(a.id) for a in actions]),
            "scene_ids": ",".join([str(s.id) for s in scenes]),
            "instance": self,
        }
        return render_to_string("story/items_dropdown.html", context)

    def generate_render(self):
        """
        Creates a new Render object for this scene based on the story's render type
        and populates it with RenderItems for each action in the scene.
        """
        
        render = Render.objects.create(
            story=self,
            name=f"Render for {self.name or self.id}",
            render_type=self.render_type if self.render_type else 'animatic'
        )
        render.refresh_render()    
        return render

    def get_elements_as_yaml(self):
        """
        Serializes the story's main elements (locations, characters, props, voices)
        and reference Google Voices into a YAML formatted string.
        """
        
        locations = [BackgroundSchema(name=b.name, prompt=b.prompt) for b in self.backgrounds.all()]
        characters = [CharacterSchema(name=c.name, prompt=c.prompt) for c in self.characters.all()]
        props = [PropSchema(name=p.name, prompt=p.prompt) for p in self.props.all()]
        voices = [
            VoiceSchema(
                name=v.name,
                prompt=v.prompt,
                google_voice=v.google_voice.name if v.google_voice else None
            ) for v in self.voices.all()
        ]
        google_voices = [
            GoogleVoiceSchema(
                name=gv.name,
                description=gv.description or ""
            ) for gv in GoogleVoice.objects.all()
        ]

        elements_data = StoryElementsSchema(
            locations=locations,
            characters=characters,
            props=props,
            voices=voices,
            google_voices=google_voices
        ).model_dump()

        # Filter out empty lists before dumping to YAML
        filtered_data = {k: v for k, v in elements_data.items() if v}

        if not filtered_data:
            return None
        return yaml.dump({"story_context": filtered_data}, indent=2, default_flow_style=False)

class Scene(AfterSaveActionMixin, models.Model, TaskHolder, GetContentsMixin, ModelDisplayMixin):
    
    TASK_TEXT_GENERATE = settings.TASK_TYPE_GENERATE_TEXT
    TASK_EXTRACT_SCENE = settings.TASK_TYPE_EXTRACT_SCENE

    PRESET_FROM_SHOTS = "from_shots"
    PRESET_FROM_PROMPT = "from_prompt"
    PRESET_TRANSLATE =  "translate"
    
    ACTION_REFINE_FROM_SHOTS = f"{TASK_TEXT_GENERATE}_preset_{PRESET_FROM_SHOTS}"
    ACTION_REFINE_FROM_PROMPT = f"{TASK_TEXT_GENERATE}_preset_{PRESET_FROM_PROMPT}"
    ACTION_TRANSLATE_FROM_PROMPT = f"{TASK_TEXT_GENERATE}_preset_{PRESET_TRANSLATE}"
    ACTION_GENERATE_STRUCTURE = settings.TASK_TYPE_EXTRACT_SCENE

    ACTION_CHOICES = (
        (ACTION_REFINE_FROM_SHOTS, _("Refine from shots")),
        (ACTION_REFINE_FROM_PROMPT, _("Refine from prompt")),
        (ACTION_TRANSLATE_FROM_PROMPT, _("Translate from prompt")),
        (ACTION_GENERATE_STRUCTURE, _("Sync structure"))
    )

    name = models.CharField(_("name"), max_length=200, null=True, blank=True)
    prompt = models.TextField(_("prompt"), null=True, blank=True, default="#Shots\n")
    prompt_refine = models.TextField(_("prompt refine"), null=True, blank=True)
    action = models.SlugField(_("action"), choices=ACTION_CHOICES, null=True, blank=True)

    order = models.PositiveIntegerField(_("order"), default=0, db_index=True)
    story = models.ForeignKey('Story', verbose_name=_("story"), related_name='scenes', null=True, blank=True, on_delete=models.CASCADE)
    author = models.ForeignKey('Author', verbose_name=_("author"), related_name='scenes', on_delete=models.CASCADE, null=True, blank=True)
    instructions = models.ManyToManyField('agent.Prompt', verbose_name=_("instructions"), null=True, blank=True)

    def __str__(self):
        return "{}".format(self.name if self.name else f"Scene{self.id} of {self.story}")
    
    def get_instructions(self, preset):
        return self.instructions.filter(category=preset)

    def task_from_action(self, action_type, user, message=None):
        """
        Parses an action_type string to extract the task_type and an optional preset.
        The expected format is 'task_type_preset_preset_name' or just 'task_type'.
        """
        parts = action_type.split('_preset_', 1)
        task_type = parts[0]
        preset = parts[1] if len(parts) > 1 else None
        payload = {}
        if preset:
            payload['preset'] = preset
        if message:
            payload['message'] = message

        task = Task.createTaskIfQueueEnabled(
                subject=self,
                task_type=task_type,
                payload=payload if payload else None,
                owner=user
            )
        return task

    def shots(self):
        return self.actions.all()
    
    def get_shots_as_yaml(self):
        """
        Serializes the scene's actions (shots) into a YAML formatted string.
        """
        shots = self.shots()
        if not shots.exists():
            return None

        action_list = [
            ActionSchema(
                name=a.name,
                order=a.order,
                prompt=a.prompt or "",
                prompt_comic=a.prompt_comic or "",
                prompt_video=a.prompt_video or "",
                prompt_voice=a.prompt_voice or "",
                text=a.text or "",
                voice=a.voice.name if a.voice else "None",
                background=a.background.name if a.background else "None",
                cast=[c.name for c in a.cast.all()],
                props=[p.name for p in a.props.all()]
            ).model_dump()
            for a in shots
        ]
        
        return yaml.dump(action_list, indent=2, default_flow_style=False)

    def get_contents(self, generate_self=True, preset=None):
        parts = []
        if not generate_self:
            parts.extend(self.story.style.get_contents(generate_self=False))
        else:
            if preset == self.PRESET_REFINE_PROMPT:
                parts.append(self.prompt_refine)
                if self.shots().exists():
                    parts.append(self.get_shots_as_yaml())
                else:
                    parts.append(self.prompt)
            elif preset == self.PRESET_FROM_SHOTS:
                if self.shots().exists():
                    parts.append(self.get_shots_as_yaml())
            elif preset == self.PRESET_FROM_PROMPT:
                parts.append(self.prompt)
            if self.story:
                elements_yaml = self.story.get_elements_as_yaml()
                if elements_yaml: parts.append(elements_yaml)
        return parts

    def get_cast(self):
        return Character.objects.filter(actions_cast__in=self.actions.all()).distinct()

    def get_locations(self):
        return Background.objects.filter(actions__in=self.actions.all()).distinct()

    def get_props(self):
        return Prop.objects.filter(actions__in=self.actions.all()).distinct()   

    def get_voices(self):
        return Voice.objects.filter(actions_voice__in=self.actions.all()).distinct()

    def get_elements(self):
        """
        Returns a dictionary containing distinct Backgrounds, Props, Characters, and Voices
        referenced by all actions within this scene, ordered by name.
        """
        actions = self.actions.all()

        locations = self.get_locations()
        props = self.get_props()
        characters = self.get_cast()
        voices = self.get_voices()
       
        return {
            'locations': locations,
            'characters': characters,
            'props': props,
            'voices': voices,
        }

    def items(self):
        """
        Renders a summary dropdown of scene elements using a template.
        """
        context = self.get_elements()
        context['actions'] = self.actions.all()

        # Pre-calculate counts and IDs for the template
        context.update({
            "count": sum(qs.count() for qs in context.values()) + context['actions'].count(),
            "prop_ids": ",".join([str(p.id) for p in context['props']]),
            "char_ids": ",".join([str(c.id) for c in context['characters']]),
            "loc_ids": ",".join([str(l.id) for l in context['locations']]),
            "voice_ids": ",".join([str(v.id) for v in context['voices']]),
            "action_ids": ",".join([str(a.id) for a in context['actions']]),
            "instance": self,
        })
        return render_to_string("scene/items_dropdown.html", context)

    def generate_render(self):
        """
        Creates a new Render object for this scene based on the story's render type
        and populates it with RenderItems for each action in the scene.
        """
        
        render = Render.objects.create(
            scene=self,
            name=f"Render for {self.name or self.id}",
            render_type=self.story.render_type if self.story else 'animatic'
        )
        render.refresh_render()
        
        return render

    class Meta:
        ordering = ['order']


class Nudge(models.Model, EmailSenderMixin):
    email_template = 'email/nudge.html'

    sender = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name=_("sender"), related_name='sent_nudges', on_delete=models.CASCADE, null=True, blank=True)
    receiver = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name=_("receiver"), related_name='received_nudges', on_delete=models.CASCADE, null=True, blank=True)
    story = models.ForeignKey('scene.Story', verbose_name=_("story"), related_name='nudges', on_delete=models.CASCADE, null=True, blank=True)
    message = models.TextField(_("message"), null=True, blank=True, help_text=_("Optional message to include in the nudge email."))
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    read = models.BooleanField(default=False)

    def __str__(self):
        return "{}".format(self.sender.username)

    def mark_as_read(self):
        self.read = True
        self.save() 
   
    def save(self, *args, **kwargs):
        if self.sender == self.receiver:
            raise ValueError("Sender and receiver cannot be the same user.")
        send = False
        cta_url = ""
        if not self.id:
            author = Author.objects.filter(story=self.story, user=self.receiver).first()
            if author:
                cta_url = settings.SITE_URL + f'/admin/scene/scene/add?story={self.story.id}&author={author.id}'
                send = True
        super().save(*args, **kwargs)
        if send:
            self.send_email(
                subject=_("Nudge on the story: %(story_name)s. %(sender_name)s nudged you!") % {
                    'story_name': self.story.name,
                    'sender_name': self.sender.username
                },
                context={
                    'item': self,
                    'cta': cta_url
                },
                recipient_list=[self.receiver.email]
            )


class Prop(AfterSaveActionMixin, models.Model, GetContentsMixin, TaskHolder, ModelDisplayMixin):
    name = models.CharField(_("name"), max_length=100, default="")
    image = FilerImageField(verbose_name=_("image"), null=True, blank=True, on_delete=models.SET_NULL, related_name='props')
    prompt= models.TextField(_("prompt"), null=True, blank=True)
    prompt_refine = models.TextField(_("prompt refine"), null=True, blank=True)
    story = models.ForeignKey('Story', verbose_name=_("story"), related_name='props', null=True, blank=True, on_delete=models.CASCADE)

    # trick to save and execute tasks
    TASK_TYPE_CHOICES = settings.TASK_TYPE_CHOICES
    action = models.SlugField(_("action"), choices=settings.TASK_TYPE_CHOICES, null=True, blank=True)

    def __str__(self):
        return "{}".format(self.name)

    def context_text(self, generate_self=True, preset=None):
        if preset == self.PRESET_REFINE:
            return self.prompt_refine
        if generate_self:
            return self.prompt
        return f"Object or Prop referenced as: {self.name}:"
    
    def get_contents(self, generate_self=True, preset=None):
        parts = super().get_contents(generate_self=generate_self, preset=preset)
        if self.story and self.story.style and generate_self:
            parts.extend(self.story.style.get_contents(generate_self=False))
        return parts

class Voice(AfterSaveActionMixin, models.Model, TaskHolder, GetContentsMixin, ModelDisplayMixin):
    SAMPLE_TEXT_DEFAULT = "Hello, this is a sample voice. 1, 2, 3. Change me and add a sample prompt for better results. Add audio effects for more interesting voices!"
    PROMPT_SAMPLE_DEFAULT = "Speak:"
    PROMPT_DEFAULT = "Style: [describe the style of speaking you want, e.g. excited, sad, professional, etc.]\nPace: [describe the pace of speaking you want, e.g. fast, slow, etc.]\nAccent: [describe the accent you want, e.g. British, American, etc.]\n\n"
    
    name = models.CharField(_("name"), max_length=100)
    google_voice = models.ForeignKey('agent.GoogleVoice', verbose_name=_("google voice"), related_name='voices', on_delete=models.SET_NULL, null=True, blank=True)
    prompt = models.TextField(_("prompt"), null=True, blank=True , default=PROMPT_DEFAULT)
    audio_voice = FilerFileField(verbose_name=_("audio voice"), null=True, blank=True, on_delete=models.SET_NULL, related_name='samples')
    sample_text = models.TextField(_("sample text"), null=True, blank=True , default=SAMPLE_TEXT_DEFAULT)
    story = models.ForeignKey('scene.Story', verbose_name=_("story"), related_name='voices', on_delete=models.CASCADE, null=True, blank=True)
    global_default = models.BooleanField(_("global default"), default=False)

    TASK_TYPE_CHOICES = [
        (settings.TASK_TYPE_GENERATE_VOICE, _("Generate Voice"))
    ]
    action = models.SlugField(_("action"), choices=TASK_TYPE_CHOICES, null=True, blank=True)

    
    def __str__(self):
        return "{}".format(self.name)
    
    def get_prompt_header(self):
        return f"# AUDIO PROFILE: {self.name}/n/n"

    def get_sample_text(self):
        return self.sample_text if self.sample_text else self.SAMPLE_TEXT_DEFAULT
    
    def get_contents(self, generate_self=True, preset=None):
        prompt = ""
        if preset == self.PRESET_VOICE:
            prompt = f"out {self.get_prompt_header()} ### DIRECTOR'S NOTES\n\n {self.prompt} \n\n #### TRANSCRIPT \n\n"
            if generate_self:
                prompt += self.get_sample_text()
        return {
            'prompt': prompt,
            'voice': self.google_voice.name
        }
    
class Character(models.Model, GetContentsMixin, TaskHolder, ModelDisplayMixin):
    name = models.CharField(_("name"), max_length=100, default="")
    image = FilerImageField(verbose_name=_("image"), null=True, blank=True, on_delete=models.SET_NULL, related_name='characters')
    prompt= models.TextField(_("prompt"), null=True, blank=True)
    prompt_refine = models.TextField(_("prompt refine"), null=True, blank=True)
    story = models.ForeignKey('Story', verbose_name=_("story"), related_name='characters', null=True, blank=True, on_delete=models.CASCADE)
    voice = models.ForeignKey(Voice, verbose_name=_("voice"), related_name='characters', on_delete=models.SET_NULL, null=True, blank=True) 
    TASK_TYPE_CHOICES = settings.TASK_TYPE_CHOICES
    action = models.SlugField(_("action"), choices=settings.TASK_TYPE_CHOICES, null=True, blank=True)
   
    def __str__(self):
        return "{}".format(self.name)

    def context_text(self, generate_self=True, preset=None):
        if preset == self.PRESET_REFINE:
            return self.prompt_refine
        if generate_self:
            return self.prompt
        return f"Character: {self.name}:"

    def get_contents(self, generate_self=True, preset=None):
        parts = super().get_contents(generate_self=generate_self, preset=preset)
        if self.story and self.story.style and generate_self:
            parts.extend(self.story.style.get_contents(generate_self=False))
        return  parts # reverse to have the character prompt last so it is more important

    class Meta:
        verbose_name = _('Character')
        verbose_name_plural = _('Characters')


class Background(AfterSaveActionMixin, models.Model, GetContentsMixin, TaskHolder, ModelDisplayMixin):
    name = models.CharField(_("name"), max_length=100, default="")
    prompt= models.TextField(_("prompt"), null=True, blank=True)
    image = FilerImageField(verbose_name=_("image"), null=True, blank=True, on_delete=models.SET_NULL, related_name='backgrounds')
    prompt_refine = models.TextField(_("prompt refine"), null=True, blank=True)
    image_refine = FilerImageField(verbose_name=_("image refine"), null=True, blank=True, on_delete=models.SET_NULL, related_name='background_refine')
    story = models.ForeignKey('Story', verbose_name=_("story"), related_name='backgrounds', null=True, blank=True, on_delete=models.CASCADE)

    TASK_TYPE_CHOICES = settings.TASK_TYPE_CHOICES
    action = models.SlugField(_("action"), choices=settings.TASK_TYPE_CHOICES, null=True, blank=True)


    def __str__(self):
        return "{}".format(self.name)

    def context_text(self, generate_self=True, preset=None):
        if preset == self.PRESET_REFINE:
            return self.prompt_refine
        if generate_self:
            return self.prompt
        return self.name

    def get_contents(self, generate_self=True, preset=None):
        parts = super().get_contents(generate_self=generate_self, preset=preset)
        if self.story and self.story.style and generate_self and not preset == self.PRESET_REFINE:
            parts.extend(self.story.style.get_contents())
        return parts

    class Meta:
        verbose_name = _('Location')
        verbose_name_plural = _('Locations')

class Sync(AfterSaveActionMixin, models.Model, TaskHolder, ModelDisplayMixin):
    story = models.ForeignKey('Story', verbose_name=_("story"), related_name='syncs', on_delete=models.CASCADE)
    last_file_in = FilerFileField(verbose_name=_("last file in"), null=True, blank=True, on_delete=models.SET_NULL, related_name='syncs_in')
    last_file_out = FilerFileField(verbose_name=_("last file out"), null=True, blank=True, on_delete=models.SET_NULL, related_name='syncs_out')

    def __str__(self):
        return f"Sync: {self.story.name}"

class SyncItem(AfterSaveActionMixin, models.Model, TaskHolder, ModelDisplayMixin):
    TYPE_IMPORT = 'import'
    TYPE_EXPORT = 'export'
    TYPE_CHOICES = [
        (TYPE_IMPORT, _('Import')),
        (TYPE_EXPORT, _('Export')),
    ]
    sync = models.ForeignKey(Sync, verbose_name=_("sync"), related_name='items', on_delete=models.CASCADE)
    type = models.CharField(_("type"), max_length=10, choices=TYPE_CHOICES)
    zip_file = FilerFileField(verbose_name=_("zip file"), null=True, blank=True, on_delete=models.SET_NULL, related_name='sync_items')
    
    TASK_TYPE_CHOICES = settings.TASK_TYPE_CHOICES
    action = models.SlugField(_("action"), choices=TASK_TYPE_CHOICES, null=True, blank=True)

    def __str__(self):
        return f"{self.get_type_display()} for {self.sync.story.name} ({self.id})"

    class Meta:
        ordering = ['-id']


class StoryGroup(models.Model):
    story = models.ForeignKey(Story, verbose_name=_("story"), related_name='story_groups', on_delete=models.SET_NULL, null=True, blank=True)
    name = models.CharField(_("name"), max_length=200)
    users = models.ManyToManyField(settings.AUTH_USER_MODEL, verbose_name=_("users"), related_name='story_groups')
    
    def __str__(self):
        return "{}".format(self.name)


class StoryProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, verbose_name=_("user"), related_name='story_profile', on_delete=models.CASCADE)
    story = models.ForeignKey(Story, verbose_name=_("story"), related_name='story_profiles', on_delete=models.SET_NULL, null=True, blank=True)
    scene = models.ForeignKey(Scene, verbose_name=_("scene"), related_name='story_profiles', on_delete=models.SET_NULL, null=True, blank=True)
    group = models.ForeignKey(StoryGroup, verbose_name=_("group"), related_name='story_profiles', on_delete=models.SET_NULL, null=True, blank=True)
    enable_filters = models.BooleanField(_("enable filters"), default=False)
    
    def __str__(self):
        return "{}".format(self.user.username)

    def get_current_story(self):
        story = None
        if self.story:
            story = self.story
        if self.group and self.group.story:
            story = self.group.story
        return story

class Action(AfterSaveActionMixin, models.Model, GetContentsMixin, TaskHolder, ModelDisplayMixin):
    
    IS_INTRO_CHOICES = [
        ('scene', _('Scene Intro')),
        ('story', _('Story Intro'))
    ]
    SHOT_TYPE_SILENT = 'silent'
    SHOT_TYPE_VOICE = 'voice'
    SHOT_TYPE_VIDEO = 'video'
    SHOT_TYPE_COMIC = 'comic'
    SHOT_TYPE_VIDEO_LOOP = 'video_loop'

    RENDER_TYPE_GRAPHIC_NOVEL = 'comic'
    RENDER_TYPE_ANIMATIC = 'animatic'

    SHOT_TYPE_CHOICES = [
        (SHOT_TYPE_SILENT, _('Silent')),
        (SHOT_TYPE_VOICE, _('Voice')),
        (SHOT_TYPE_VIDEO, _('Video')),
        (SHOT_TYPE_COMIC, _('Comic')),
        (SHOT_TYPE_VIDEO_LOOP, _('Video Loop')),
    ]

    name = models.CharField(_("name"), max_length=200, default="Action")
    scene = models.ForeignKey(Scene, verbose_name=_("scene"), related_name='actions', on_delete=models.CASCADE)
    is_intro = models.SlugField(_("is intro"), choices=IS_INTRO_CHOICES, null=True, blank=True)
    prompt = models.TextField(_("prompt"), null=True, blank=True)
    order = models.PositiveIntegerField(_("order"), default=0, db_index=True)
    image = FilerImageField(verbose_name=_("image"), null=True, blank=True, on_delete=models.SET_NULL, related_name='panel')
    background = models.ForeignKey(Background, verbose_name=_("background"), related_name='actions', on_delete=models.SET_NULL, null=True, blank=True)
    actor = models.ForeignKey(Character, verbose_name=_("actor"), related_name='actions', on_delete=models.SET_NULL, null=True, blank=True)
    props = models.ManyToManyField(Prop, verbose_name=_("props"), related_name='actions', blank=True)
    cast = models.ManyToManyField(Character, verbose_name=_("cast"), related_name='actions_cast', blank=True)
    consistent_with = models.ForeignKey('self', verbose_name=_("consistent with"), related_name='consistent_actions', on_delete=models.SET_NULL, null=True, blank=True)
    prompt_refine = models.TextField(_("prompt refine"), null=True, blank=True)
    image_refine = FilerImageField(verbose_name=_("image refine"), null=True, blank=True, on_delete=models.SET_NULL, related_name='action_refine')
    # video
    image_first = FilerImageField(verbose_name=_("image first"), null=True, blank=True, on_delete=models.SET_NULL, related_name='actions_first')
    image_last = FilerImageField(verbose_name=_("image last"), null=True, blank=True, on_delete=models.SET_NULL, related_name='actions_last')
    video = FilerFileField(verbose_name=_("video"), null=True, blank=True, on_delete=models.SET_NULL, related_name='actions_video')
    prompt_video = models.TextField(_("prompt video"), null=True, blank=True)

    TASK_TYPE_CHOICES = settings.TASK_TYPE_CHOICES
    action = models.SlugField(_("action"), choices=settings.TASK_TYPE_CHOICES, null=True, blank=True)

    prompt_comic = models.TextField(_("prompt comic"), null=True, blank=True)
    image_comic = FilerImageField(verbose_name=_("image comic"), null=True, blank=True, on_delete=models.SET_NULL, related_name='actions_comic')
    voice = models.ForeignKey(Voice, verbose_name=_("voice"), related_name='actions_voice', on_delete=models.SET_NULL, null=True, blank=True)
    audio_voice = FilerFileField(verbose_name=_("audio voice"), null=True, blank=True, on_delete=models.SET_NULL, related_name='actions_audio')
    prompt_voice = models.TextField(_("prompt voice"), null=True, blank=True)
    text = models.TextField(_("text"), null=True, blank=True)
    parameters = models.JSONField(_("configuration"), null=True, blank=True)
    shot_type = models.CharField(_("shot type"), max_length=20, choices=SHOT_TYPE_CHOICES, null=True, blank=True)

    def __str__(self):
        return self.get_name()
    
    def get_name(self):
        return self.name if self.name else f"#{self.id} of{self.scene.name}"
        
    def items(self):
        """Renders the explore dropdown for the action."""
        return render_to_string("scene/action_items_dropdown.html", {"instance": self, "scene": self.scene})
    items.short_description = _("Items")

    def slide(self):
        return {
            "media_url": self.video.url if self.video else (self.image_comic.url if self.image_comic else (self.image.url if self.image else None)),
            "media_type": "video" if self.video else "image",
            "audio_url": self.audio_voice.url if self.audio_voice else None,
            "text": self.text,
            "name": self.get_name()
        }

    class Meta:
        ordering = ['order', 'name']
        verbose_name = _('Shot')
        verbose_name_plural = _('Shots')

    def elements(self):
        props = []
        cast_members = []
        main_actor = self.actor.name if self.actor else "None"
        background = self.background.name if self.background else "None"
        if self.props:
            props.extend([prop.name for prop in self.props.all()])
        if self.cast:
            cast_members.extend([character.name for character in self.cast.all()])
        output =  f"Main Actor: <b>{main_actor}</b><br/> Background: <b>{background}</b><br/> Props: <b>{', '.join(props)}</b><br/> Cast: <b>{', '.join(cast_members)}</b><br/>"
        return mark_safe(output)
    
    def get_thumbnail(self, preset=None):
        return Image.open(self.image.path)

    def generate_comic(self, user=None, target_field="image_comic"):
        image_agent = Agent.objects.filter(output_type=Agent.OUTPUT_TYPE_IMAGE).first()
        out = image_agent.generate(self, preset=self.PRESET_COMIC, user=user, target_field=target_field)
        return out
    
    def intro(self):
        out = None
        if self.image_comic:
            out = self.image_comic
        elif self.image:
            out = self.image   
        return out

    def get_contents(self, generate_self=True, preset=None):
        from google.genai import types
        if preset == self.PRESET_VIDEO or preset == self.PRESET_OMNI_VIDEO:
            contents = {}
            contents['prompt'] = self.prompt_video
            contents['image'] = self.image
        elif preset == self.PRESET_VIDEO_FIRST_LAST:
            contents = {}
            contents['prompt'] = self.prompt_video
            contents['image_first'] = self.image_first or self.image
            contents['image_last'] = self.image_last or contents['image_first']
        elif preset == self.PRESET_VOICE:
            contents = {}
            contents = self.voice.get_contents(generate_self=False, preset=Voice.PRESET_VOICE)
            contents["prompt"] += self.prompt_voice
        else:
            # preset refine is handled in the mixin
            contents = super().get_contents(generate_self=generate_self, preset=preset)
            if preset != self.PRESET_COMIC and preset != self.PRESET_REFINE:
                if self.consistent_with:
                    contents.extend(["Maximise consistency, preserve character features and objects to the following image", self.consistent_with.image])
                if self.actor:
                    contents.extend(self.actor.get_contents(generate_self=False))
                if self.cast:
                    for character in self.cast.all():
                        contents.extend(character.get_contents(generate_self=False))
                if self.props:
                    for prop in self.props.all():
                        contents.extend(prop.get_contents(generate_self=False))
                if self.background:
                    contents.extend(self.background.get_contents(generate_self=False))
                if self.scene:
                    contents.extend(self.scene.get_contents(generate_self=False))
        return contents
    
    def context_text(self, generate_self=True, preset=None):
        out = None
        if not generate_self:
            out = self.name
        elif preset == self.PRESET_IMAGE:
            out = self.prompt
        elif preset == self.PRESET_COMIC:
            out = self.prompt_comic 
        elif preset == self.PRESET_REFINE:
            return self.prompt_refine
        return out

class SceneOrganizer(Scene):
    class Meta:
        proxy = True
        verbose_name = _('Scene Organizer')
        verbose_name_plural = _('Scene Organizers')

class ActionOrganizer(Action):
    class Meta:
        proxy = True
        verbose_name = _('Shot Organizer')
        verbose_name_plural = _('Shot Organizers')

class VideoAction(Action):
    class Meta:
        proxy = True
        verbose_name = _('Video Shot ')
        verbose_name_plural = _('Video Shots')

class ComicAction(Action):
    class Meta:
        proxy = True
        verbose_name = _('Comic Shot')
        verbose_name_plural = _('Comic Shots')


class VoiceAction(Action):
    class Meta:
        proxy = True
        verbose_name = _('Voice Shot')
        verbose_name_plural = _('Voice Shots')


class Render(RenderTypeMixin, models.Model, TaskHolder, ModelDisplayMixin):
    RENDER_TYPE_FILM = 'film'
    RENDER_TYPE_GRAPHIC_NOVEL = 'comic'
    RENDER_TYPE_ANIMATIC = 'animatic'

    RENDER_TYPE_CHOICES = [
        (RENDER_TYPE_FILM, _('Film')),
        (RENDER_TYPE_GRAPHIC_NOVEL, _('Graphic Novel')),
        (RENDER_TYPE_ANIMATIC, _('Animatic')),
    ]

    name = models.CharField(_("name"), max_length=200, default="")
    scene = models.ForeignKey(Scene, verbose_name=_("scene"), related_name='renders', null=True, blank=True, on_delete=models.CASCADE)
    video = FilerFileField(verbose_name=_("video"), null=True, blank=True, on_delete=models.SET_NULL, related_name='render_videos')
    story = models.ForeignKey(Story, verbose_name=_("story"), related_name='renders', null=True, blank=True, on_delete=models.CASCADE)
    render_type = models.CharField(
        _("render type"),
        max_length=50,
        choices=RENDER_TYPE_CHOICES,
        default=RENDER_TYPE_FILM,
        help_text=_("The format in which this scene will be synthesized.")
    )
    def __str__(self):
        return "{}".format(self.name)

    def _create_item_from_action(self, action, order):
        """Helper to create a RenderItem based on the current render_type."""
        item = RenderItem(
            render=self,
            scene=action.scene,
            action=action,
            order=order
        )

        if self.render_type == self.RENDER_TYPE_FILM:
            item.video = action.video
        elif self.render_type == self.RENDER_TYPE_ANIMATIC:
            item.image = action.image_comic or action.image  # Fallback to standard image if comic version isn't generated yet
            item.audio = action.audio_voice
        elif self.render_type == self.RENDER_TYPE_GRAPHIC_NOVEL:
            # Fallback to standard image if comic version isn't generated yet
            item.image = action.image_comic or action.image

        item.save()
        return item

    def refresh_render(self):
        """
        Clears existing RenderItems and recreates them. 
        Iterates through the Story's scenes if available, otherwise just the specific Scene.
        """
        self.render_items.all().delete()

        actions = []
        if self.story:
            actions = list(Action.objects.filter(scene__story=self.story).order_by("order"))
        elif self.scene:
            actions = list(self.scene.actions.all().order_by('order'))

        for i, action in enumerate(actions):
            self._create_item_from_action(action, order=i)

    class Meta:
        verbose_name = _('Render Composition')
        verbose_name_plural = _('Render Compositions')

    @classmethod
    def get_from_scene(cls, scene):
        return cls.objects.get_or_create(scene=scene, story=scene.story, defaults={'name': f"Render for {scene.name}"})[0]
        
class RenderItem(models.Model, TaskHolder, ModelDisplayMixin):
    DEFAULT_IMAGE_DURATION = 8
    image = FilerImageField(verbose_name=_("image"), null=True, blank=True, on_delete=models.SET_NULL, related_name='video_item')
    video = FilerFileField(verbose_name=_("video"), null=True, blank=True, on_delete=models.SET_NULL, related_name='video_item_video')
    audio = FilerFileField(verbose_name=_("audio"), null=True, blank=True, on_delete=models.SET_NULL, related_name='video_item_audio')
    order = models.PositiveIntegerField(_("order"), default=0, db_index=True)
    config = models.JSONField(_("config"), null=True, blank=True)
    params = models.CharField(_("params"), max_length=255, null=True, blank=True)
    render = models.ForeignKey("scene.Render", verbose_name=_("render"), related_name='render_items',null=True, blank=True, on_delete=models.CASCADE)
    scene = models.ForeignKey("scene.Scene", verbose_name=_("scene"), related_name='render_items', null=True, blank=True, on_delete=models.SET_NULL)
    action = models.ForeignKey("scene.Action", verbose_name=_("action"), related_name='render_items', null=True, blank=True, on_delete=models.SET_NULL)
    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Item {self.order} for {self.render.name if self.render else 'Unassigned'}"
    
    class Meta:
        ordering = ['order']
        verbose_name = _('Render Item')
        verbose_name_plural = _('Render Items')

    @property
    def duration(self):
        out = self.DEFAULT_IMAGE_DURATION
        if self.config and 'duration' in self.config: 
            out = self.config['duration']
        return out

class WorkShop(models.Model):
    name = models.CharField(_("name"), max_length=255)
    description = models.TextField(_("description"))
    date = models.DateTimeField(_("date"))
    location = models.CharField(_("location"), max_length=255)

    def __str__(self):
        return self.name

class ContactRequest(models.Model):
    CONTRIBUTION_CHOICES = [
        ("pay", _("I am ok to pay 20/30 dollars/euros for the workshop")),
        ("api_key", _("I am ok to bring my google api key to the project")),
        ("collaborate", _("I want to help testing")),
        ("trial", _("I just want to try it out and use the local open source version")),
    ]

    name = models.CharField(_("name"), max_length=255)
    email = models.EmailField(_("email"))
    contribuition = models.TextField(_("contribution"), choices=CONTRIBUTION_CHOICES, null=True, blank=True)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    prompt = models.TextField(_("A glimpse of your imagination"), null=True, blank=True, help_text=_("Just imagine and write with as many typos as you want. Guillermo will curate your scene"), default="#Location\n#Cast\n#Props\n#Actions\n")
    workshop = models.ForeignKey(WorkShop, verbose_name=_("workshop"), related_name='contact_requests', on_delete=models.CASCADE, null=True, blank=True)
    group_number = models.IntegerField(_("group number"), null=True, blank=True, help_text=_("How many friends and family member you would bring to the workshop?"))

    def __str__(self):
        return f"{self.name} ({self.email})"