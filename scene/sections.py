import os
from typing import Any
from django.utils.safestring import mark_safe
import markdown
from django.utils.translation import gettext_lazy as _
from django.utils.html import format_html, strip_tags
from django.urls import reverse

from django.contrib.admin.utils import label_for_field, lookup_field
from django.db.models import Model
from django.http import HttpRequest
from django.template.loader import render_to_string

from unfold.utils import display_for_field
from unfold.sections import BaseSection, TemplateSection

from .admin_utils import render_image_markup
from scene.models import Author

class TableSection(BaseSection):
    fields = []
    related_name = None
    verbose_name = None
    height = None
    template_name ="sections/base.html"

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

class AuthorSection(TableSection):
    model = Author
    NO_USER_LABEL = _("Use Create User Action")
    verbose_name = _("Authors")
    
    fields = ['name', 'scenes', 'nudges']
    extra = 0
    show_count = True  # This will run `count()`
    collapsible = True
    related_name = 'authors'
    
    def name(self, obj):
        return f"{obj.user.username if obj.user else obj.email}"

    def context_data(self) -> dict:
        return {"description": _("Manage authors within the story edit page. If you add emails, guillermo will send an invitation by email.")}

    def scenes(self, obj):
        if obj.user:
            if obj.user == self.request.user:
                url = reverse("admin:scene_scene_add")
                return format_html(
                    '<a href="{}?story={}&author={}&next=/admin/scene/story/" class="bg-primary-600 text-white px-2 py-1 rounded-md text-[10px] font-bold hover:bg-primary-500 transition-colors shadow-sm inline-flex items-center gap-1">'
                    '<span class="material-symbols-outlined text-[14px]">add</span>{}</a>',
                    url, obj.story.id, obj.id, _("Add Scene")
                )
            else:
                url = f"/admin/scene/scene/?story__id__exact={obj.story.id}&author__id__exact={obj.id}"
                return format_html(
                    '<a href="{}" class="text-primary-600 font-medium hover:text-primary-500 dark:text-primary-400 dark:hover:text-primary-300 inline-flex items-center gap-1">'
                    '<span class="material-symbols-outlined text-[16px]">visibility</span>{}</a>',
                    url, _("View")
                )
        return self.NO_USER_LABEL
    
    def nudges(self, obj):
        nudge_link = self.NO_USER_LABEL
        if obj.user:
            nudge_count = obj.user.received_nudges.count()
            nudge_link = format_html("<a href='/admin/scene/nudge/?receiver__id__exact={0}' class='text-primary-600 font-medium hover:text-primary-500 dark:text-primary-400 dark:hover:text-primary-300'>{1}</a>", obj.user.id, nudge_count) 
            if (obj.user != self.request.user):
                add_link = format_html("<a href='/admin/scene/nudge/add/?receiver={0}&story={1}&sender={2}' class='text-primary-600 font-medium hover:text-primary-500 dark:text-primary-400 dark:hover:text-primary-300 ml-1'>-></a>", obj.user.id, obj.story.id, self.request.user.id)
                nudge_link = format_html("{} {}", nudge_link, add_link)
        return mark_safe(nudge_link)
    


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
    collapsible = False
    related_name = 'scenes'

class RenderSection(TemplateSection):
    template_name = "sections/scene_renders.html"
    key = 'renders'

    def get_context_data(self, request, instance):
        return {
            # The card template tests `render.video` and `render.document`; without this each
            # render with either costs its own query to draw one row.
            "renders": instance.renders.all().select_related("video", "document"),
            "instance": instance,
            "section_key": self.key
        }
class SceneBaseCardsSection(TemplateSection):
    template_name = "sections/scene_cards.html"
    key = None
    title = None
    item_method = None
    collapsible = True

    def get_context_data(self, request, instance):
        items = []
        # Load items immediately for initial render
        if self.item_method and hasattr(instance, self.item_method):
            items = getattr(instance, self.item_method)()

        return {
            "title": self.title,
            "items": items,
            "instance": instance,
            "section_key": self.key,
            "collapsible": self.collapsible,
            "is_loaded": True,
        }


class MarkDownSection(TemplateSection):
    template_name = "sections/markdown_section.html"
    field_name = "prompt"
    title = "Script"
    key = "script"

    def get_context_data(self, request, instance) -> dict:
        content = getattr(instance, self.field_name, "") or ""
        html_content = mark_safe(markdown.markdown(content))

        return {
            "title": self.title,
            "instance": instance,
            "section_key": self.key,
            "html_content": html_content,
            "is_loaded": True,
        }


class SceneCharactersSection(SceneBaseCardsSection):
    key = 'characters'
    title = _("Characters")
    item_method = 'get_cast'


class SceneLocationsSection(SceneBaseCardsSection):
    key = 'locations'
    title = _("Locations")
    item_method = 'get_locations'


class ScenePropsSection(SceneBaseCardsSection):
    key = 'props'
    title = _("Props")
    item_method = 'get_props'


class MessageHistorySection(TableSection):
    verbose_name = _("AI Generation History")
    related_name = 'messages'
    fields = ['created_at_fmt', 'agent_info', 'input_parts', 'output_result']

    def created_at_fmt(self, obj):
        return obj.created_at.strftime("%Y-%m-%d %H:%M")
    created_at_fmt.short_description = _("Date")

    def agent_info(self, obj):
        target = obj.target_field if obj.target_field else "-"
        return format_html(
            '<span class="font-bold text-font-important-light dark:text-font-important-dark">{}</span><br/>'
            '<span class="text-[10px] uppercase tracking-wider text-base-500">Target: {}</span>',
            obj.agent.name if obj.agent else "-",
            target
        )
    agent_info.short_description = _("Agent / Target")

    def input_parts(self, obj):
        if not obj.input_data:
            return "-"

        items = []
        if isinstance(obj.input_data, list):
            for i, part in enumerate(obj.input_data):
                items.append(format_html(
                    '<div class="mb-3 last:mb-0 pb-2 border-b border-base-200 dark:border-base-700 last:border-0">'
                    '<span class="text-[9px] font-bold opacity-50 uppercase block mb-1">Part {}</span>'
                    '{}</div>', 
                    i + 1, part
                ))
        elif isinstance(obj.input_data, dict):
            for key, val in obj.input_data.items():
                items.append(format_html('<div><b class="capitalize text-primary-600">{}</b>: {}</div>', key, val))
        else:
            items.append(str(obj.input_data))

        return format_html(
            '<div class="max-h-48 min-w-[300px] overflow-y-auto text-[10px] font-mono bg-base-50 dark:bg-base-800/50 p-3 rounded-lg border border-base-200 dark:border-base-700 text-base-600 dark:text-base-400">{}</div>',
            mark_safe("".join(items))
        )
    input_parts.short_description = _("Input Context")

    def output_result(self, obj):
        if obj.output_image:
            model_label = f"{obj._meta.app_label}.{obj._meta.model_name}"
            return render_image_markup(obj.output_image.url, model_label, obj.pk, 'output_image', 80, _("Output Image"))
        
        if obj.output_file:
            ext = os.path.splitext(obj.output_file.name)[1].lower()
            if ext in ['.mp4', '.mov', '.webm']:
                return format_html('<video src="{}" class="h-20 w-auto rounded bg-black" controls muted></video>', obj.output_file.url)
            if ext in ['.mp3', '.wav']:
                return format_html('<audio src="{}" controls class="h-8 w-48 scale-90 origin-left"></audio>', obj.output_file.url)
            return format_html('<a href="{}" class="text-primary-600 underline text-xs" download>{}</a>', obj.output_file.url, _("Download File"))
            
        if obj.output_text:
            html = markdown.markdown(obj.output_text)
            return format_html('<div class="max-h-32 overflow-y-auto text-xs prose prose-sm dark:prose-invert max-w-sm">{}</div>', mark_safe(html))
            
        return "-"
    output_result.short_description = _("Output")
