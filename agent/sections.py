import markdown
from django import forms
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.urls import path
from django.utils.translation import gettext_lazy as _
from django.contrib.contenttypes.models import ContentType
from .utils import handle_ajax_field_save
from django.apps import apps
from django.forms import modelform_factory
from unfold.widgets import SELECT_CLASSES, Select, UnfoldAdminSelectWidget, UnfoldAdminTextareaWidget
from unfold.sections import TemplateSection
from django.utils.safestring import mark_safe
from .widgets import DynamicMarkdownWidget as MarkdownWidget
from django.db import models as db_models

class AjaxSectionAdminMixin:
    @property
    def media(self):
        media = super().media
        try:
            media = media + MarkdownWidget().media
        except Exception:
            pass
        return media

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                'ajax-section-update/',
                self.admin_site.admin_view(self.ajax_section_update_view),
                name='ajax_section_update',
            ),
        ]
        
        # Discover and add URLs from sections
        if hasattr(self, "list_sections"):
            for section_class in self.list_sections:
                if hasattr(section_class, "get_section_urls"):
                    section_instance = section_class(request=None, instance=None)
                    section_urls = section_instance.get_section_urls(self)
                    custom_urls.extend(section_urls)
        return custom_urls + urls

    def ajax_section_update_view(self, request):
        if request.method != "POST":
            return JsonResponse({"error": "Method not allowed"}, status=405)
        
        model_label = request.POST.get("_model_label")
        object_id = request.POST.get("_id")
        field_name = request.POST.get("_field")
        value = request.POST.get("_value") or ""

        try:
            model = apps.get_model(model_label)
            obj = get_object_or_404(model, pk=object_id)
            
            # Basic validation: ensure the field exists
            if not hasattr(obj, field_name):
                return JsonResponse({"error": f"Invalid field: {field_name}"}, status=400)

            handle_ajax_field_save(obj, field_name, value)

            # Return refresh data for immediate UI updates
            refresh_data = {}
            refresh_keys = [field_name]
            # Convention: if an image field is updated, also try to refresh the 'pic' display helper
            if hasattr(obj, 'pic'):
                refresh_keys.append('pic')
            
            for k in refresh_keys:
                attr = getattr(obj, k, None)
                if callable(attr):
                    refresh_data[k] = str(attr())
                else:
                    refresh_data[k] = str(attr) if attr is not None else ""

            return JsonResponse({
                "status": "success",
                "refresh": refresh_data
            })
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=400)
        
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


class ChatMessageForm(forms.Form):
    chat_input = forms.CharField(
        
        label="",
        widget=UnfoldAdminTextareaWidget(attrs={"rows": 2, "placeholder": "Type your message here..."}),
        required=False
    )
    action = forms.ChoiceField(
        choices=[],
        required=False,
        label="",
        widget=UnfoldAdminSelectWidget()
    )

    def __init__(self, *args, **kwargs):
        instance = kwargs.pop('instance', None)
        super().__init__(*args, **kwargs)

        if instance and hasattr(instance, 'ACTION_CHOICES'):
            self.fields['action'].choices = getattr(instance, 'ACTION_CHOICES', [])


class MessageHistorySection(AjaxSection):
    verbose_name = _("Chat")
    template_name = "sections/chat_section.html"
    collapsible = True
    key = 'messages'

    @classmethod
    def get_section_urls(cls, model_admin):
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

        task = instance.task_from_action(
            action_type=request.POST.get('action'),
            message=request.POST.get('chat_input'),
            user=request.user
        )
        task_status = task.status if task else None

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
        url = f"chat-message/{content_type_id}/{object_id}/"
        context = {
            "messages": instance.messages.all().order_by('-created_at')[:10],  # Get the 10 most recent messages, newest first
            "instance": instance,
            "section_key": cls.key,
            "content_type_id": content_type_id,
        }
        return context

    def get_context_data(self, request, instance) -> dict:
        content_type = ContentType.objects.get_for_model(instance)
        form = ChatMessageForm(instance=instance)
        url = f"chat-message/{content_type.id}/{instance.pk}/"
        context = super().get_context_data(request, instance)
        context.update({
            "request": request,
            "form": form,
            "title": self.verbose_name,
            "instance": instance,
            "section_key": self.key,
            "is_loaded": False,
            "collapsible": self.collapsible,
            "content_type_id": content_type.id,
            "chat_submit_url": url,
        })
        return context

    def render(self) -> str:
        if self.template_name is None:
            raise ValueError("TemplateSection must have a template_name")

        return render_to_string(
            self.template_name,
            context={
                **self.get_context_data(self.request, self.instance),
                "request": self.request,
                "instance": self.instance,
            },
            request=self.request
        )


class MessagePartSection(TemplateSection):
    verbose_name = _("Message Breakdown")
    template_name = "sections/message_parts_section.html"
    collapsible = True
    key = 'message_parts'

    def get_context_data(self, request, instance) -> dict:
        context = super().get_context_data(request, instance)

        def format_part(part):
            content = ""
            if part.text:
                content += f"```\n{part.text}\n```"
            if part.json:
                import json
                content += f"```json\n{json.dumps(part.json, indent=2)}\n```"
            if part.image:
                thumb = part.image.easy_thumbnails_thumbnailer.get_thumbnail({'size': (120, 120)})
                content += f'<img src="{thumb.url}" alt="Image" style="max-width: 120px; max-height: 120px; border-radius: 4px; margin-top: 5px;" />'
            if part.file:
                content += f'\n<a href="{part.file.url}" target="_blank">Download File</a>'
            return mark_safe(content)

        context.update({
            "title": self.verbose_name,
            "instance": instance,
            "section_key": self.key,
            "collapsible": self.collapsible,
            "instructions": [(p, format_part(p)) for p in instance.parts_instructions()],
            "inputs": [(p, format_part(p)) for p in instance.parts_input()],
            "outputs": [(p, format_part(p)) for p in instance.parts_output()],
        })
        return context

    def render(self) -> str:
        if self.template_name is None:
            raise ValueError("TemplateSection must have a template_name")

        return render_to_string(
            self.template_name, self.get_context_data(self.request, self.instance), request=self.request
        )


class MarkDownSection(AjaxSection):
    template_name = "sections/markdown_section.html"
    field_name = "prompt"
    title = "Script"
    key = "script"
    collapsible = True

    @classmethod
    def get_section_urls(cls, model_admin):
        return [
            path(f'refresh-section/{cls.key}/<int:content_type_id>/<int:object_id>/',
                 model_admin.admin_site.admin_view(cls.refresh_view),
                 name=cls.get_url_name('refresh_section')),
        ]

    @classmethod
    def get_markdown_content(cls, instance) -> str:
        return getattr(instance, cls.field_name, "") or ""

    @classmethod
    def refresh_view(cls, request, content_type_id, object_id):
        content_type = get_object_or_404(ContentType, pk=content_type_id)
        model_class = content_type.model_class()
        instance = get_object_or_404(model_class, pk=object_id)
        content = cls.get_markdown_content(instance)
        html_content = mark_safe(markdown.markdown(content))
        
        html = render_to_string("sections/markdown_section_content.html", {
            "html_content": html_content
        })
        return JsonResponse({"html": html})

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


class MessageInstructionsSection(MarkDownSection):
    title = _("System Instructions")
    key = "instructions"

    @classmethod
    def get_markdown_content(cls, instance) -> str:
        parts_text = [p.text for p in instance.parts_instructions() if p.text]
        return "\n\n".join(parts_text) if parts_text else _("No instructions found.")


class MessageInputsSection(MarkDownSection):
    title = _("Inputs")
    key = "inputs"

    @classmethod
    def get_markdown_content(cls, instance) -> str:
        parts_text = []
        for part in instance.parts_input():
            if part.text:
                parts_text.append(part.text)
            if part.json:
                import json
                parts_text.append(f"```json\n{json.dumps(part.json, indent=2)}\n```")
            if part.image:
                parts_text.append(f"![Input Image]({part.image.url})")
            if part.file:
                parts_text.append(f"[Download File]({part.file.url})")
        return "\n\n".join(parts_text) if parts_text else _("No inputs found.")


class MessageOutputsSection(MessageInputsSection):
    title = _("Generated Outputs")
    key = "outputs"

    @classmethod
    def get_markdown_content(cls, instance) -> str:
        parts_text = []
        for part in instance.parts_output():
            if part.text:
                parts_text.append(part.text)
            if part.json:
                import json
                parts_text.append(f"```json\n{json.dumps(part.json, indent=2)}\n```")
            if part.image:
                parts_text.append(f"![Output Image]({part.image.url})")
            if part.file:
                parts_text.append(f"[Download File]({part.file.url})")
        return "\n\n".join(parts_text) if parts_text else _("No outputs found.")


class GenericFormSection(AjaxSection):
    """A generic section that renders a model form for the instance,
    handles AJAX validation/submission, and updates the UI.
    """
    template_name = "sections/generic_form_section.html"
    fields = "__all__"  # Can be a list of fields or "__all__"
    form_class = None   # Optional custom form class
    title = _("Edit Details")
    key = "generic_form"
    collapsible = True

    def get_form_class(self, model):
        if self.form_class:
            FormClass = self.form_class
        else:
            widgets = {}
            for field in model._meta.get_fields():
                if self.fields == "__all__" or field.name in self.fields:
                    if isinstance(field, db_models.TextField):
                        widgets[field.name] = MarkdownWidget()
            FormClass = modelform_factory(model, fields=self.fields, widgets=widgets)

        # Ensure any textarea widget on the form class is defaulted to MarkdownWidget
        for field in FormClass.base_fields.values():
            if isinstance(field.widget, forms.Textarea) and not isinstance(field.widget, MarkdownWidget):
                field.widget = MarkdownWidget(attrs=field.widget.attrs)
        return FormClass

    @classmethod
    def get_section_urls(cls, model_admin):
        return [
            path(f'form-submit/{cls.key}/<int:content_type_id>/<int:object_id>/',
                 model_admin.admin_site.admin_view(cls.form_submit),
                 name=cls.get_url_name('form_submit')),
            path(f'refresh-section/{cls.key}/<int:content_type_id>/<int:object_id>/',
                 model_admin.admin_site.admin_view(cls.refresh_view),
                 name=cls.get_url_name('refresh_section')),
        ]

    @classmethod
    def form_submit(cls, request, content_type_id, object_id):
        if request.method != "POST":
            return JsonResponse({"error": "Method not allowed"}, status=405)

        content_type = get_object_or_404(ContentType, pk=content_type_id)
        model_class = content_type.model_class()
        instance = get_object_or_404(model_class, pk=object_id)

        section = cls(request=request, instance=instance)
        FormClass = section.get_form_class(model_class)
        form = FormClass(request.POST, request.FILES, instance=instance)

        if form.is_valid():
            form.save()
            instance.refresh_from_db()
            context = section.get_context_data(request, instance)
            context["is_loaded"] = True
            html = render_to_string(
                "sections/generic_form_section_content.html",
                context,
                request=request
            )
            return JsonResponse({
                "status": "success",
                "html": html,
            })
        else:
            context = section.get_context_data(request, instance)
            context["is_loaded"] = True
            html = render_to_string(
                "sections/generic_form_section_content.html",
                {
                    **context,
                    "form": form,
                },
                request=request
            )
            return JsonResponse({
                "status": "error",
                "html": html,
            }, status=400)

    @classmethod
    def refresh_view(cls, request, content_type_id, object_id):
        content_type = get_object_or_404(ContentType, pk=content_type_id)
        model_class = content_type.model_class()
        instance = get_object_or_404(model_class, pk=object_id)

        section = cls(request=request, instance=instance)
        context = section.get_context_data(request, instance)
        context["is_loaded"] = True
        html = render_to_string(
            "sections/generic_form_section_content.html",
            context,
            request=request
        )
        return JsonResponse({"html": html})

    def get_context_data(self, request, instance) -> dict:
        content_type = ContentType.objects.get_for_model(instance)
        FormClass = self.get_form_class(instance.__class__)
        form = FormClass(instance=instance)
        submit_url = f"form-submit/{self.key}/{content_type.id}/{instance.pk}/"

        for field in form.fields.values():
            if isinstance(field.widget, MarkdownWidget):
                continue
            elif isinstance(field.widget, forms.Textarea):
                field.widget.attrs.update({"class": "unfold-input border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 rounded px-3 py-2 w-full text-sm"})
            elif isinstance(field.widget, (forms.TextInput, forms.EmailInput, forms.NumberInput)):
                field.widget.attrs.update({"class": "unfold-input border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 rounded h-10 px-3 w-full text-sm"})
            elif isinstance(field.widget, forms.Select):
                field.widget.attrs.update({"class": "unfold-select border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 rounded h-10 px-3 w-full text-sm"})

        context = super().get_context_data(request, instance)
        context.update({
            "request": request,
            "form": form,
            "title": self.title,
            "instance": instance,
            "section_key": self.key,
            "is_loaded": False,
            "collapsible": self.collapsible,
            "content_type_id": content_type.id,
            "submit_url": submit_url,
        })
        return context


class PromptFormSection(GenericFormSection):
    title = _("Prompt")
    key = "prompt"
    fields = ["prompt"]  # Only include the 'prompt' field