import os
from typing import Any
from django import forms
from django.utils.safestring import mark_safe
from django.http import JsonResponse
import markdown
from django.utils.translation import gettext_lazy as _
from django.utils.html import format_html, strip_tags
from django.urls import reverse, path

from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Fieldset, Div
from django.contrib.contenttypes.models import ContentType
from django.contrib.admin.utils import label_for_field, lookup_field
from django.shortcuts import get_object_or_404
from django.db.models import Model
from django.http import HttpRequest
from django.template.loader import render_to_string

from unfold.utils import display_for_field, settings
from unfold.sections import BaseSection, TemplateSection

from .admin_utils import render_image_markup
from scene.models import Author, Prop, Background, Character, Scene

from agent.models import Message

class TableSection(BaseSection):
    fields = []
    related_name = None
    verbose_name = None
    height = None
    template_name ="sections/table_section.html"

    def context_data(self) -> dict:
        return {}
    
    def render(self) -> str:
        if self.related_name is None:
            raise ValueError("TableSection must have a related_name")

        results = getattr(self.instance, self.related_name)
        headers = []
        rows = []

        for field_name in self.fields:
            if hasattr(self, field_name):
                if hasattr(getattr(self, field_name), "short_description"):
                    headers.append(getattr(self, field_name).short_description)
                else:
                    headers.append(field_name)
            else:
                headers.append(label_for_field(field_name, results.model))

        for result in results.all():
            row = []

            for field_name in self.fields:
                if hasattr(self, field_name):
                    row.append(getattr(self, field_name)(result))
                else:
                    field, attr, value = lookup_field(field_name, result)
                    row.append(display_for_field(value, field, "-"))

            rows.append(row)

        context = {
            "request": self.request,
            "table": {
                "headers": headers,
                "rows": rows,
            },
        }
        context.update(self.context_data())
        if hasattr(self, "verbose_name") and self.verbose_name:
            context["title"] = self.verbose_name

        if hasattr(self, "height") and self.height:
            context["height"] = self.height

        return render_to_string(
            self.template_name,
            context=context,
        )
    



class ThemeSection(TemplateSection):
    template_name ="sections/prompt.html"
    
    def get_context_data(self, request, instance) -> dict[str, Any]:
        return {
            "item" : instance.theme,
            "request": request,
        }


class SceneSection(TableSection):
    template_name ="sections/story_scene.html"

    def context_data(self) -> dict:
        return {
            "story": self.instance,
            "author": self.request.user.authors.filter(story=self.instance).first()
        }

    def prompt(self, obj):
        if not obj.prompt:
            return _("No Prompt")

        full_html = markdown.markdown(obj.prompt)
        plain_text = strip_tags(full_html)
        char_limit = 180

        if len(plain_text) <= char_limit:
            return mark_safe(f"<div class='markdown prose prose-sm dark:prose-invert max-w-none'>{full_html}</div>")

        truncated = plain_text[:char_limit].rsplit(' ', 1)[0] + "..."
        return format_html(
            '<div x-data="{{ expanded: false }}" class="relative">'
                '<div x-show="!expanded" class="text-sm text-font-default-light dark:text-font-default-dark opacity-90">'
                    '{} <button type="button" @click="expanded = true" class="text-primary-600 font-semibold hover:text-primary-500 dark:text-primary-400 dark:hover:text-primary-300 ml-1 transition-colors bg-transparent border-none p-0 cursor-pointer inline">{}</button>'
                '</div>'
                '<div x-show="expanded" class="markdown prose prose-sm dark:prose-invert max-w-none bg-base-50/50 dark:bg-white/[.02] p-4 rounded-lg border border-base-200 dark:border-base-800" style="display: none;">'
                    '{} <button type="button" @click="expanded = false" class="text-primary-600 font-semibold hover:text-primary-500 dark:text-primary-400 dark:hover:text-primary-300 mt-2 transition-colors bg-transparent border-none p-0 cursor-pointer block">{}</button>'
                '</div>'
            '</div>',
            truncated, _("Read more"), mark_safe(full_html), _("Read less")
        )
        
    def get_name(self, obj):
        url = "/admin/scene/scene/?id__exact={0}".format(obj.id)
        return format_html(
            '<a href="{}" class="text-primary-600 font-medium hover:text-primary-500 dark:text-primary-400 dark:hover:text-primary-300">{}</a>',
            url,
            obj.name if obj.name else _("Scene {pk}").format(pk=obj.pk)
        )
    get_name.short_description = _("Name")

    fields = ['get_name', "items", 'prompt']
    extra = 0
    show_count = True  # This will run `count()`
    collapsible = True
    related_name = 'scenes'

class AjaxSection(TemplateSection):
    """Base class for sections that need to handle their own AJAX URLs."""

    @classmethod
    def get_section_urls(cls, model_admin):
        """
        Return a list of URL patterns for the admin.
        The ModelAdmin will discover and register these.
        """
        return []

    @classmethod
    def get_url_name(cls, action):
       return f"{cls.__name__.lower()}_{action}"



class AuthorSection(AjaxSection):
    model = Author
    NO_USER_LABEL = _("Use Create User Action")
    title = _("Authors")
    key = 'authors'
    template_name = "sections/author_section.html"
    collapsible = True

    fields = ['name', 'scenes', 'nudges']
    related_name = 'authors'

    @classmethod
    def get_section_urls(cls, model_admin):
        return [
            path(f'refresh-section/{cls.key}/<int:content_type_id>/<int:object_id>/',
                 model_admin.admin_site.admin_view(cls.refresh_view),
                 name=cls.get_url_name('refresh_section')),
        ]

    @classmethod
    def refresh_view(cls, request, content_type_id, object_id):
        content_type = get_object_or_404(ContentType, pk=content_type_id)
        model_class = content_type.model_class()
        instance = get_object_or_404(model_class, pk=object_id)

        results = getattr(instance, cls.related_name)
        
        headers = []
        for field_name in cls.fields:
            if hasattr(cls.model, field_name):
                headers.append(label_for_field(field_name, cls.model))
            else:
                headers.append(field_name.replace('_', ' ').title())

        rows = []

        for result in results.all():
            row_data = {
                'name': f"{result.user.username if result.user else result.email}",
                'scenes': cls._get_scenes_html(request, result),
                'nudges': cls._get_nudges_html(request, result)
            }
            rows.append([row_data[field] for field in cls.fields])

        context = {
            "table": {"headers": headers, "rows": rows},
            "description": _("Manage authors for this story. Add an email to invite a new author."),
        }
        html = render_to_string("sections/author_section_content.html", context, request=request)
        return JsonResponse({"html": html})

    @classmethod
    def _get_scenes_html(cls, request, obj):
        scene_count = obj.scenes.count()
        url = reverse("admin:scene_scene_changelist") + f"?author__id__exact={obj.id}"
        return format_html(
            '<a href="{}" class="text-primary-600 font-medium hover:text-primary-500 dark:text-primary-400 dark:hover:text-primary-300">{}</a>',
            url,
            scene_count
        )

    @classmethod
    def _get_nudges_html(cls, request, obj):
        nudge_link = cls.NO_USER_LABEL
        if obj.user:
            nudge_count = obj.user.received_nudges.count()
            nudge_link = format_html("<a href='/admin/scene/nudge/?receiver__id__exact={0}' class='text-primary-600 font-medium hover:text-primary-500 dark:text-primary-400 dark:hover:text-primary-300'>{1}</a>", obj.user.id, nudge_count) 
            if (obj.user != request.user):
                add_link = format_html("<a href='/admin/scene/nudge/add/?receiver={0}&story={1}&sender={2}' class='text-primary-600 font-medium hover:text-primary-500 dark:text-primary-400 dark:hover:text-primary-300 ml-1'>-></a>", obj.user.id, obj.story.id, request.user.id)
                nudge_link = format_html("{} {}", nudge_link, add_link)
        return mark_safe(nudge_link)
    
    def get_context_data(self, request, instance) -> dict:
        content_type = ContentType.objects.get_for_model(instance)
        return {
            "title": self.title,
            "instance": instance,
            "section_key": self.key,
            "is_loaded": False,
            "collapsible": self.collapsible,
            "content_type_id": content_type.id,
        }


class RenderSection(AjaxSection):
    template_name = "sections/scene_renders.html"
    key = 'renders'
    title = _("Renders & Videos")
    collapsible = True

    @classmethod
    def get_section_urls(cls, model_admin):
        return [
            path(f'refresh-section/{cls.key}/<int:content_type_id>/<int:object_id>/',
                 model_admin.admin_site.admin_view(cls.refresh_view),
                 name=cls.get_url_name('refresh_section')),
        ]

    @classmethod
    def refresh_view(cls, request, content_type_id, object_id):
        content_type = get_object_or_404(ContentType, pk=content_type_id)
        model_class = content_type.model_class()
        instance = get_object_or_404(model_class, pk=object_id)
        html = render_to_string("sections/scene_renders_items.html", {"renders": instance.renders.all()})
        return JsonResponse({"html": html})

    def get_context_data(self, request, instance):
        content_type = ContentType.objects.get_for_model(instance)
        return {
            "title": self.title,
            "instance": instance,
            "section_key": self.key,
            "is_loaded": False,
            "collapsible": self.collapsible,
            "content_type_id": content_type.id,
        }


class SceneBaseCardsSection(AjaxSection):
    template_name = "sections/scene_cards.html"
    key = None
    title = None
    item_method = None
    collapsible = True

    def get_section_urls(cls, model_admin):
        return [
            path(f'refresh-section/{cls.key}/<int:content_type_id>/<int:object_id>/',
                 model_admin.admin_site.admin_view(cls.refresh_view),
                 name=cls.get_url_name('refresh_section')),
        ]

    @classmethod
    def refresh_view(cls, request, content_type_id, object_id):
        content_type = get_object_or_404(ContentType, pk=content_type_id)
        model_class = content_type.model_class()
        instance = get_object_or_404(model_class, pk=object_id)

        items = getattr(instance, cls.item_method)().order_by('name')

        context = {
            "items": items,
            "is_loaded": True,
        }
        html = render_to_string("sections/scene_cards_items.html", context)
        return JsonResponse({"html": html})

    def get_context_data(self, request, instance):
        content_type = ContentType.objects.get_for_model(instance)
        return {
            "title": self.title,
            "items": [],
            "instance": instance,
            "section_key": self.key,
            "collapsible": self.collapsible,
            "is_loaded": False,
            "content_type_id": content_type.id,
        }


class MarkDownSection(AjaxSection):
    template_name = "sections/markdown_section.html"
    field_name = "prompt"
    title = "Script"
    key = "script"

    @classmethod
    def get_section_urls(cls, model_admin):
        return [
            path(f'refresh-section/{cls.key}/<int:content_type_id>/<int:object_id>/',
                 model_admin.admin_site.admin_view(cls.refresh_view),
                 name=cls.get_url_name('refresh_section')),
        ]

    @classmethod
    def refresh_view(cls, request, content_type_id, object_id):
        content_type = get_object_or_404(ContentType, pk=content_type_id)
        model_class = content_type.model_class()
        instance = get_object_or_404(model_class, pk=object_id)
        content = getattr(instance, cls.field_name, "") or ""
        html_content = mark_safe(markdown.markdown(content))
        html = render_to_string("sections/markdown_section_content.html", {"html_content": html_content})
        return JsonResponse({"html": html})

    def get_context_data(self, request, instance) -> dict:
        content_type = ContentType.objects.get_for_model(instance)
        return {
            "title": self.title,
            "instance": instance,
            "section_key": self.key,
            "is_loaded": False,
            "collapsible": True,
            "content_type_id": content_type.id,
        }


class SceneCharactersSection(SceneBaseCardsSection):
    key = 'characters'
    model = Character
    title = _("Characters")
    item_method = 'get_cast'

    


class SceneLocationsSection(SceneBaseCardsSection):
    key = 'locations'
    model = Background
    title = _("Locations")
    item_method = 'get_locations'


class ScenePropsSection(SceneBaseCardsSection):
    key = 'props'
    model = Prop
    title = _("Props")
    item_method = 'get_props'



class ChatMessageForm(forms.Form):
    chat_input = forms.CharField(
        widget=forms.Textarea(attrs={
            'rows': 3,  # Increase the default number of rows
            'placeholder': _('Type your message...'),
        }),
        label=""
    )
    action = forms.ChoiceField(
        choices=[],  # Start with empty choices, will be populated in __init__
        required=False,
        label="",
        widget=forms.Select()  # Explicitly use the Select widget
    )

    def __init__(self, *args, **kwargs):
        # Pop the instance from kwargs before calling super()
        instance = kwargs.pop('instance', None)
        super().__init__(*args, **kwargs)

        # Dynamically set choices based on the instance provided
        action_choices = getattr(instance, 'ACTION_CHOICES', Scene.ACTION_CHOICES)
        self.fields['action'].choices = action_choices

        self.helper = FormHelper()
        self.helper.form_tag = False  # We handle the form tag in the template
        self.helper.disable_csrf = True
        self.helper.form_show_labels = False
        self.helper.layout = Layout(
            Div(
                'chat_input',
                'action',
                css_class="space-y-4"  # Add vertical space between fields
            )
        )

class MessageHistorySection(AjaxSection):
    verbose_name = _("Chat")
    template_name = "sections/chat_section.html"
    collapsible = True
    key = 'messages'
    

    @classmethod
    def get_section_urls(cls, model_admin):
        # The URL name is now unique per model, preventing conflicts.
        return [
            path('chat-message/<int:content_type_id>/<int:object_id>/', 
                 model_admin.admin_site.admin_view(cls.chat_form_submit), 
                 name=cls.get_url_name('chat_message')),
            path('refresh-section/<int:content_type_id>/<int:object_id>/',
                 model_admin.admin_site.admin_view(cls.refresh_view),
                 name=cls.get_url_name('refresh_section')),
        ]
    
    @classmethod
    def chat_form_submit(cls, request, content_type_id, object_id):
        content_type = get_object_or_404(ContentType, pk=content_type_id)
        model_class = content_type.model_class()
        instance = get_object_or_404(model_class, pk=object_id)
        
        # Create the task and get its initial status
        task = instance.task_from_action(
            action_type=request.POST.get('action'), 
            message=request.POST.get('chat_input'), 
            user=request.user
        )
        task_status = task.status if task else None

        # Prepare the response for the frontend
        chat_html = render_to_string(
            "sections/chat_section_content.html", 
            cls.get_context(request, instance, content_type_id, object_id), 
            request=request
        )
        
        return JsonResponse({
            "status": "task_created",
            "task_status": task_status,
            "html": chat_html,
        })

    @classmethod
    def refresh_view(cls, request, content_type_id, object_id):
        content_type = get_object_or_404(ContentType, pk=content_type_id)
        model_class = content_type.model_class()
        instance = get_object_or_404(model_class, pk=object_id)
        
        context = cls.get_context(request, instance, content_type_id, object_id)
        html = render_to_string("sections/chat_section_content.html", context, request=request)
        return JsonResponse({"html": html})

    @classmethod
    def get_context(cls, request, instance, content_type_id, object_id):
        # Pass the instance to the form to dynamically set its choices
        url = f"chat-message/{content_type_id}/{object_id}/"
        form = ChatMessageForm(instance=instance)
        context = {
            "messages": instance.messages.all(),
            "form": form,
            "instance": instance,
            "section_key": cls.key,
            "content_type_id": content_type_id,
            "chat_submit_url": url,
        }
        return context

    def get_context_data(self, request, instance) -> dict:
        content_type = ContentType.objects.get_for_model(instance)
        # The initial context for the section wrapper.
        # The actual content is loaded via AJAX by refresh_view.
        context = super().get_context_data(request, instance)
        context.update({
            "title": self.verbose_name,
            "instance": instance,
            "section_key": self.key,
            "is_loaded": False, # Important: tells the frontend to fetch content
            "collapsible": self.collapsible,
            "content_type_id": content_type.id,
        })
        return context
