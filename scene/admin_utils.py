
from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import path, reverse
from django.utils.html import format_html
from unfold.admin import ModelAdmin
from django.utils.safestring import mark_safe
from .utils import render_image_markup
from task.models import Task
from .serializers import get_generic_serializer


class AdminLinker:
    def __getattr__(self, name):
        if name.startswith("link_"):
            related_field = name[5:]

            def dynamic_link(obj):
                if not hasattr(obj, related_field):
                    return "-"

                linked_object = getattr(obj, related_field)

                if linked_object is None:
                    return "-"

                if hasattr(linked_object, "all"):  # This is a manager (e.g., ManyToMany or reverse ForeignKey)
                    model = linked_object.model
                    count = linked_object.count()

                    link_field = next((f.name for f in model._meta.get_fields()
                                     if f.is_relation and f.related_model == obj._meta.concrete_model), None)

                    if not link_field:
                        return format_html("View ({0})", count)

                    url = reverse(f"admin:{model._meta.app_label}_{model._meta.model_name}_changelist")
                    return format_html(
                        '<a href="{0}?{1}__id__exact={2}" class="text-primary-600 font-medium hover:underline">{3}</a>',
                        url, link_field, obj.pk, count or 0
                    )
                else:
                    # This is a single instance (e.g., a ForeignKey)
                    model = linked_object._meta.model
                    url = reverse(f"admin:{model._meta.app_label}_{model._meta.model_name}_changelist")
                    return format_html('<a href="{0}?id__exact={1}" class="text-primary-600 font-medium hover:underline">{2}</a>', url, linked_object.pk, str(linked_object))

            dynamic_link.short_description = related_field.replace("_", " ").title()
            return dynamic_link

        if name.startswith("image_"):
            related_field = name[6:]

            def dynamic_image(obj):
                if not hasattr(obj, related_field):
                    return "-"

                img = getattr(obj, related_field)
                if callable(img):
                    img = img()

                url = img.url if img and hasattr(img, "url") else ""
                max_h = getattr(obj, "MAX_IMAGE_HEIGHT", 80)
                model_label = f"{obj._meta.app_label}.{obj._meta.model_name}"
                label = related_field.replace("_", " ").title()
                return render_image_markup(url, model_label, obj.pk, related_field, max_h, label)

            dynamic_image.short_description = related_field.replace("_", " ").title()
            return dynamic_image

        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")
