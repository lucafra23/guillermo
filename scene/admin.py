import io
import os
import zipfile

from django.contrib import admin
from httpcore import request
from unfold.admin import ModelAdmin
from django.urls import path
from django.urls import path, reverse
from django import forms
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from agent.widgets import DynamicMarkdownWidget as MarkdownWidget
from agent.sections import MessageHistorySection, PromptFormSection
from simple_history.admin import SimpleHistoryAdmin
from agent.sections import AjaxSectionAdminMixin, MessageHistorySection
from django.conf import settings
from task.models import Task
from unfold.admin import StackedInline
from .models import ActionOrganizer, Character, Scene, Preset, Action, Background, SceneOrganizer, StoryGroup, Style, Prop, ComicAction, RenderItem, VideoAction, Render, Story, StoryProfile, Voice, VoiceAction, Author, Nudge, ContactRequest, WorkShop, Sync, SyncItem
from .admin_utils import AdminLinker
from agent.admin_utils import AjaxTaskModelAdmin
from django.utils.translation import gettext_lazy as _
from django.utils.html import format_html
from agent.models import Message
from .sections import AuthorSection, ElementSection, SceneSection, SceneCharactersSection, SceneLocationsSection, ScenePropsSection, RenderSection, MarkDownSection, ScriptSection
from .mixins import ACTION_FIELDSETS, ELEMENT_FIELDSETS, SceneFilterMixin, StaffReadOnlyMixin, StoryFilterMixin, ViewYourOwnMixin, PromptPreviewMixin, AdminActionsMixin, ChangelistScrollToEditedMixin
from unfold.sections import TableSection, TemplateSection, render_to_string
from rangefilter.filters import NumericRangeFilter
from django.http import JsonResponse, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils.text import slugify 
DEFAULT_IMAGE_AGENT_NAME = "DIGA"
from django.apps import apps 
from agent.models import Prompt
from django.utils.safestring import mark_safe
import markdown
from django.contrib.auth.models import User
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from unfold.decorators import action

class PromptMarkdownMixin:
    def formfield_for_dbfield(self, db_field, request, **kwargs):
        is_changelist = False
        if request and getattr(request, 'resolver_match', None):
            url_name = getattr(request.resolver_match, 'url_name', '') or ''
            if url_name.endswith('_changelist'):
                is_changelist = True
        if not is_changelist and (db_field.name == 'prompt' or db_field.name.startswith('prompt_') ):
            kwargs['widget'] = MarkdownWidget()
        return super().formfield_for_dbfield(db_field, request, **kwargs)
        

class PromptPreviewSection(TemplateSection):
    """Section that renders a dropdown to preview model prompts."""
    template_name = "sections/prompt_preview.html"

    def get_context_data(self, request, instance):
        presets = []
        # Automatically discover constants starting with PRESET_ on the model
        for attr in dir(instance):
            if attr.startswith("PRESET_"):
                val = getattr(instance, attr)
                # Don't add if it's a method/callable
                if not callable(val):
                    label = attr.replace("PRESET_", "").replace("_", " ").title()
                    presets.append({"value": val, "label": label})
        
        return {
            "presets": sorted(presets, key=lambda x: x['label']),
            "instance": instance,
            "request": request,
        }
@admin.register(Style)
class StyleAdmin(PromptMarkdownMixin, AdminActionsMixin, ModelAdmin):
    list_display = ('id','name', 'prompt')
    list_editable = ('name', 'prompt')
    list_display_links = ('id',)
    actions = ['clone']
    search_fields = ['name']


class AuthorInline(StackedInline):
    model = Author 
    show_count = True  # This will run `count()`
    collapsible = True
    autocomplete_fields = ['user']

class PlotSection(MarkDownSection):
    field_name = "prompt_plot"
    title = "Plot"
    key = "plot"
    
class DraftSection(MarkDownSection):
    field_name = "prompt_draft"
    title = "Draft"
    key = "draft"   

@admin.register(Story)
class StoryAdmin(ChangelistScrollToEditedMixin, PromptMarkdownMixin, SimpleHistoryAdmin, AjaxSectionAdminMixin, StoryFilterMixin, AdminActionsMixin, AdminLinker, AjaxTaskModelAdmin):
    inlines = [AuthorInline]
    autocomplete_fields = ['group']
    search_fields = ['name']
    list_refresh = ['items']
    list_sections = [
        MessageHistorySection,
        MarkDownSection,
        ElementSection,
        AuthorSection,
        SceneLocationsSection,
        SceneCharactersSection, 
        ScenePropsSection,
        RenderSection,
        PromptFormSection
    ]
    list_display = ['__str__', 'items', 'image_intro', 'add_scene', 'last_tasks']
    actions = ['clone', 'add_me_as_author', 'generate_scene_elements','generate_render', 'refresh_render']

    fieldsets = (
        ("Write",{
            "classes": ["tab"],
            "fields": ["name", "prompt"],
        }),
        ("Refine",{
            "classes": ["tab"],
            "fields": ["prompt_refine", 'action'],
        }),
        ("Settings", {
            "classes": ["tab"],
            "fields": [ "style", "theme", "group", "render_type", "config"],
        })
    )

    def changelist_view(self, request, extra_context=None):
        self.request = request
        return super().changelist_view(request, extra_context)

    def add_scene(self, obj):
        author = Author.objects.filter(user=self.request.user, story=obj).first()
        if author:
            url = reverse("admin:scene_scene_add")
            return format_html(
                '<a href="{}?story={}&author={}&next=/admin/scene/story/" class="bg-primary-600 text-white px-3 py-1.5 rounded-md text-xs font-semibold hover:bg-primary-500 transition-colors shadow-sm inline-flex items-center gap-1.5 whitespace-nowrap" style="color: white !important;">'
                '<span class="material-symbols-outlined text-[18px]">add</span>{}</a>',
                url, obj.id, author.id, _("Add Scene")
            )
        return "-"
    add_scene.short_description = _("Add Scene")

    def scene_links(self, obj):
        return format_html("<a href='/admin/scene/Scene/?story__id__exact={0}'>Edit ({1})</a>", obj.id, obj.scenes.count())
    scene_links.short_description = "Scenes"

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if not change and request.user.is_authenticated:
            if obj.add_author(request.user):
                self.message_user(request, f"You have been added as an author to story {obj.name}")


@admin.register(Scene)
class SceneAdmin(ChangelistScrollToEditedMixin, PromptMarkdownMixin, SimpleHistoryAdmin, AjaxSectionAdminMixin, StoryFilterMixin, AdminActionsMixin, AdminLinker, AjaxTaskModelAdmin):
    search_fields = ['name']
    ajax_shift_fields = ['prompt']
    list_refresh = ['items']
    list_display = ['__str__', 'items', 'last_tasks']
    autocomplete_fields = ['story', 'author', 'instructions', 'locations', 'cast', 'props', 'voices']
    actions = ['clone','extract_scene',  'generate_scene_elements', 'generate_scene_actions', 'generate_scene_voices', 'generate_scene_comics', 'generate_render', 'refresh_render']
    list_filter = ['story', 'id']
    
    fieldsets = (
        ("Write",{
            "classes": ["tab"],
            "fields": [ "prompt_plot", "prompt", "prompt_elements", "instructions"],
        }),
        ("Assets", {
            "classes": ["tab"],
            "fields": ["locations", "cast", "props", "voices"],
        }),
        ("Refine",{
            "classes": ["tab"],
            "fields": ["prompt_refine", 'action'],
        }),
        ("Settings", {
            "classes": ["tab"],
            "fields": ["name", "author", "story"],
        })
    )
    list_sections = [
        MessageHistorySection,
        PlotSection,
        ScriptSection,
        SceneCharactersSection,
        SceneLocationsSection,
        ScenePropsSection,
        RenderSection
    ]

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        if db_field.name == "instructions":
            # Only show prompts that are not marked as global in the autocomplete
            kwargs["queryset"] = Prompt.objects.filter(is_global=False)
        return super().formfield_for_manytomany(db_field, request, **kwargs)


    def save_model(self, request, obj, form, change):
        if not obj.author and obj.story:
            author = Author.objects.filter(user=request.user, story=obj.story).first()
            if author:
                obj.author = author
        super().save_model(request, obj, form, change)

    def trigger_ajax_task(self, request, obj, target_field):
        if target_field == 'prompt':
            # When the main prompt is saved via AJAX, do nothing.
            pass
        elif target_field == 'prompt_refine':
            agent = obj.story.get_mentor()
            if Task.createTaskIfQueueEnabled(obj, settings.TASK_TYPE_GENERATE_TEXT, thr=agent, owner=request.user) is None:
                obj.generate_text(request.user, agent)
        else:
            super().trigger_ajax_task(request, obj, target_field)


@admin.register(StoryGroup)
class StoryGroupAdmin(ModelAdmin):
    list_display = ('id', 'name', 'story')
    list_editable = ('name', 'story')
    list_display_links = ('id',)
    autocomplete_fields = ['story', 'users']
    search_fields = ['name']

@admin.register(StoryProfile)
class StoryProfileAdmin(ViewYourOwnMixin, StaffReadOnlyMixin, ModelAdmin):
    list_display = ('id', 'user','group', 'story', 'scene' )
    list_display_links = ('id',)
    autocomplete_fields = ['story', 'group', 'user', 'scene']
    staff_readonly_fields = ['user']

@admin.register(Character)
class CharacterAdmin(ChangelistScrollToEditedMixin, PromptMarkdownMixin, SimpleHistoryAdmin, AjaxSectionAdminMixin, StoryFilterMixin, AdminActionsMixin, AdminLinker, PromptPreviewMixin, AjaxTaskModelAdmin):
    list_display = ('name', 'pic', 'prompt', 'prompt_refine', 'link_story', 'last_tasks')
    list_refresh = ['pic']
    list_editable = ('prompt', 'prompt_refine')
    list_display_links = ('name',)
    autocomplete_fields = ['story']
    list_filter = ['story', 'id']
    actions = ['clone', 'default_generate_image', 'default_refine_image']
    search_fields = ['name']
    fieldsets = ELEMENT_FIELDSETS
    list_sections = [MessageHistorySection]

@admin.register(Background)
class BackgroundAdmin(ChangelistScrollToEditedMixin, PromptMarkdownMixin, SimpleHistoryAdmin, AjaxSectionAdminMixin, StoryFilterMixin, AdminActionsMixin, AdminLinker, PromptPreviewMixin, AjaxTaskModelAdmin):
    list_display = ('name', 'pic', 'prompt', 'prompt_refine', 'link_story', 'last_tasks')
    list_refresh = ['pic']
    list_editable = ('prompt','prompt_refine')
    list_display_links = ('name',)
    autocomplete_fields = ['story']
    list_filter = ['story', 'id']
    actions = ['clone', 'default_generate_image', 'default_refine_image']
    search_fields = ['name']
    fieldsets = ELEMENT_FIELDSETS
    list_sections = [MessageHistorySection]


@admin.register(Prop)
class PropAdmin(ChangelistScrollToEditedMixin, PromptMarkdownMixin, SimpleHistoryAdmin, AjaxSectionAdminMixin, StoryFilterMixin, AdminActionsMixin, AdminLinker, PromptPreviewMixin, AjaxTaskModelAdmin):
    search_fields = ['name']#
    list_refresh = ['pic']
    list_display = ('name', 'pic', 'prompt','prompt_refine', 'link_story', 'last_tasks')
    list_display_links = ('name',)
    list_editable = ('prompt','prompt_refine')
    autocomplete_fields = ['story']
    list_filter = ['story', 'id']
    actions = ['clone', 'default_generate_image', 'default_refine_image']
    search_fields = ['name']
    fieldsets = ELEMENT_FIELDSETS
    list_sections = [MessageHistorySection]


@admin.register(Author)
class AuthorAdmin(AdminActionsMixin, ModelAdmin):
    list_display = ['user', 'email', 'story', 'scene_count']
    list_editable = ['story']
    list_display_links = ('user',)
    autocomplete_fields = ['user', 'story']
    search_fields = ['user__username', 'email']

@admin.register(Nudge)
class NudgeAdmin(AdminActionsMixin, ModelAdmin):
    list_display = ["id", 'sender', 'receiver', 'story', 'message']
    fieldsets = (
        ("Write",{
            "classes": ["tab"],
            "fields": ["message", ],
        }),
        ("Settings", {
            "classes": ["tab"],
            "fields": ["receiver", "sender", "story"],
        })
    )

@admin.register(Action)
class ActionAdmin(ChangelistScrollToEditedMixin, PromptMarkdownMixin, SimpleHistoryAdmin, AjaxSectionAdminMixin, AdminActionsMixin, PromptPreviewMixin, StoryFilterMixin, AjaxTaskModelAdmin):
    ajax_shift_fields = ['prompt', 'prompt_refine']    
    list_display = ('get_name', 'items', 'pic', 'prompt','prompt_refine', 'last_tasks')
    list_refresh = ['pic']
    list_editable = ( 'prompt', 'prompt_refine')
    list_filter = ["scene__story", "scene", "order", "id"]
    ordering_field = "order"
    hide_ordering_field = True
    list_display_links = ('get_name',)
    autocomplete_fields = ['actor', 'props', 'cast', 'background', 'consistent_with', 'scene', 'voice']
    search_fields = ['get_name']
    actions = ['clone', 'default_generate_image', 'default_refine_image']
    fieldsets = ACTION_FIELDSETS
    list_sections = [MessageHistorySection]

@admin.register(VideoAction)
class VideoActionAdmin(PromptMarkdownMixin, AjaxSectionAdminMixin, AdminActionsMixin, PromptPreviewMixin, StoryFilterMixin, AjaxTaskModelAdmin):
    list_display = ('name', 'items', 'pic', 'prompt_video', 'video_player','last_tasks')
    list_editable = ['prompt_video']
    list_filter = ["scene__story", "scene", "id"]
    list_display_links = ('name',)
    list_refresh = ['video_player']
    search_fields = ['name']
    actions = ['generate_video', 'generate_video_first_last', 'generate_omni_video']
    fieldsets = ACTION_FIELDSETS
    list_sections = [MessageHistorySection]


@admin.register(ComicAction)
class ComicActionAdmin(PromptMarkdownMixin, AjaxSectionAdminMixin, AdminActionsMixin, PromptPreviewMixin, StoryFilterMixin, AjaxTaskModelAdmin):
    ajax_shift_fields = ['prompt_comic']
    list_display = ('name', 'items', 'pic', 'pic_comic', 'prompt_comic', 'last_tasks')
    list_editable = ['prompt_comic']
    list_filter = ["scene__story", "scene", "id"]
    list_refresh = ['pic_comic']
    list_display_links = ('name',)
    search_fields = ['name']
    actions = ['letter_action', 'generate_comic', 'comic_to_video']
    list_sections = [MessageHistorySection]
    fieldsets = ACTION_FIELDSETS


@admin.register(VoiceAction)
class VoiceActionAdmin(PromptMarkdownMixin, AjaxSectionAdminMixin, AdminActionsMixin, PromptPreviewMixin, StoryFilterMixin, AjaxTaskModelAdmin):
    list_display = ('name', 'items', 'pic', 'prompt_voice','voice', 'voice_player', 'last_tasks')
    list_editable = ['prompt_voice', 'voice' ]
    list_filter = ["scene__story", "scene", "id"]
    list_display_links = ('name',)
    autocomplete_fields = ['actor', 'props', 'cast', 'background', 'consistent_with', 'scene', 'voice']
    list_refresh = ['voice_player']
    search_fields = ['name']
    actions = ['generate_voice']
    fieldsets = ACTION_FIELDSETS
    list_sections = [MessageHistorySection]


@admin.register(ActionOrganizer)
class ActionOrganizerAdmin(AjaxSectionAdminMixin, AdminActionsMixin, PromptPreviewMixin, StoryFilterMixin, ModelAdmin):
    list_display = ('id', 'name', 'items', 'pic', 'scene', 'is_intro', 'order')
    list_editable = ['name', 'scene', 'is_intro', 'order']
    list_filter = ["scene__story", "scene"]
    search_fields = ['name']

@admin.register(SceneOrganizer)
class SceneOrganizerAdmin(AjaxSectionAdminMixin, AdminActionsMixin, AdminLinker, StoryFilterMixin, ModelAdmin):
    list_display = ('id', 'name', 'image_intro', 'link_actions', 'story')
    list_editable = ['name',  'story']
    list_filter = ["story"]
    search_fields = ['name']


@admin.register(Voice)
class VoiceAdmin(PromptMarkdownMixin, SimpleHistoryAdmin, AdminActionsMixin, AdminLinker, AjaxTaskModelAdmin):
    list_display = ('__str__', 'prompt', 'google_voice', 'sample_text', 'link_story' , 'voice_player', 'last_tasks')
    list_editable = ['prompt']
    list_refresh = ['voice_player']    
    
    actions = ['generate_voice']
    list_filter = (
        'story',
        'global_default'
    )
    search_fields= ['name']
    
    def trigger_ajax_task(self, request, obj, target_field):
        if target_field in ['prompt', 'sample_text']:
            if Task.createTaskIfQueueEnabled(obj, settings.TASK_TYPE_GENERATE_VOICE, owner=request.user) is None:
                obj.generate_voice(obj.PRESET_VOICE, user=request.user)



@admin.register(RenderItem)
class RenderItemAdmin(ModelAdmin):
    list_display = ('id', 'video_player', 'pic', 'params', 'config', 'order', 'render')
    list_editable = ('order', 'params', 'config')

@admin.register(Render)
class RenderAdmin(AjaxSectionAdminMixin, AdminActionsMixin, ModelAdmin):
    list_display = ('name', 'scene', 'render_type', 'video_player', 'video_download',
                    'document_download', 'last_tasks')
    list_display_links = ('name',)
    # `refresh_scene_video` was listed here but is not defined anywhere in the codebase, so
    # ModelAdmin.get_action dropped it silently and this admin offered no working action at all.
    actions = ['rerender']

    @admin.action(description="Re-run this render")
    def rerender(self, request, queryset):
        """Queue the render task against an EXISTING Render row.

        Every other route creates a brand-new Render: "Refresh Render" on a Story or Scene calls
        `generate_render()`, which does `Render.objects.create(...)` and therefore always starts
        from an empty `config`. Without this action `Render.config` is write-only - you can set
        `{"portable": true}` on the change form and nothing will ever read it - and a re-render
        leaves the previous Render row and its document behind rather than replacing them.
        """
        for obj in queryset:
            obj.refresh_render()
            Task.createTaskIfQueueEnabled(
                subject=obj,
                task_type=settings.TASK_TYPE_VIDEO_RENDER,
                owner=request.user,
            )
            self.message_user(request, _("Re-render queued for: {}").format(obj.name or obj.pk))


@admin.register(ContactRequest)
class ContactRequestAdmin(PromptMarkdownMixin, ModelAdmin):
    pass

@admin.register(WorkShop)
class WorkShopAdmin(ModelAdmin):
    pass

@admin.register(Preset)
class PresetAdmin(ModelAdmin):
    pass

@admin.register(Sync)
class SyncAdmin(ModelAdmin):
    list_display = ('story', 'last_file_in', 'last_file_out')
    autocomplete_fields = ['story']
    search_fields = ['story__name']

@admin.register(SyncItem)
class SyncItemAdmin(AjaxSectionAdminMixin, AjaxTaskModelAdmin):
    list_display = ('id', 'sync', 'type', 'zip_file', 'last_tasks')
    list_filter = ('type', 'sync__story')
    autocomplete_fields = ['sync']
    actions = ['trigger_sync']

    @admin.action(description=_("Trigger Sync Task"))
    def trigger_sync(self, request, queryset):
        for obj in queryset:
            task_type = settings.TASK_TYPE_SYNC_EXPORT if obj.type == obj.TYPE_EXPORT else settings.TASK_TYPE_SYNC_IMPORT
            Task.createTaskIfQueueEnabled(obj, task_type, owner=request.user)