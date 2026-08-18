
from django.conf import settings
from django.http import JsonResponse
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404
from django.urls import path, reverse
from django.utils.html import format_html
from unfold.admin import ModelAdmin
from django.utils.safestring import mark_safe
from .utils import handle_ajax_field_save
from task.models import Task
from .serializers import get_generic_serializer


class AjaxTaskModelAdmin(ModelAdmin):
    list_refresh = []
    class Media:
        js = ('js/admin_ajax.js',) # We will create this file
        
    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                'ajax-config/',
                self.admin_site.admin_view(self.ajax_config_view),
                name='action_ajax_config',
            ),
            path(
                'ajax-update/<int:object_id>/',
                self.admin_site.admin_view(self.ajax_update_view),
                name='action_ajax_update',
            ),
            path(
                'ajax-last-tasks/<int:object_id>/',
                self.admin_site.admin_view(self.get_last_tasks),
                name='action_ajax_last_tasks',
            ),
        ]
        return custom_urls + urls

    def ajax_config_view(self, request):
        fields = getattr(self, 'ajax_shift_fields', [])
        return JsonResponse({'ajax_shift_fields': fields})

    def get_last_tasks(self, request, object_id):
        """Poll one row's task state. Read-only, but still not public to all staff.

        It serialises the whole object, so a staff account with no rights to this model could
        read every field of every row through it, and the per-story scoping did not apply.
        """
        # 1. Get the object
        obj = get_object_or_404(self.get_queryset(request), pk=object_id)
        if not self.has_view_permission(request, obj):
            raise PermissionDenied
        obj.refresh_from_db()
        last_task = obj.tasks.first()
        status = last_task.status if last_task else None
        serializer_class = get_generic_serializer(self.model)

        refresh_data = {}
        for field_name in getattr(self, "list_refresh", []):
            try:
                # 1. Try Admin method (standard Django Admin behavior)
                if hasattr(self, field_name):
                    attr = getattr(self, field_name)
                    if callable(attr):
                        val = attr(obj)
                    else:
                        val = attr
                # 2. Try Model field/method
                elif hasattr(obj, field_name):
                    attr = getattr(obj, field_name)
                    if callable(attr):
                        val = attr()
                    else:
                        val = attr
                else:
                    continue
                
                refresh_data[field_name] = str(val) if val is not None else ""
            except Exception:
                continue

        # Ensure the HTML is wrapped in the expected JS container ID
        html_content = obj.last_tasks()
        wrapped_html = format_html(
            '<div id="task-{0}" data-status="{1}" class="inline-block task-polling-wrapper" data-model="{2}">{3}</div>',
            obj.pk, status if status is not None else "", 
            f"{obj._meta.app_label}.{obj._meta.model_name}",
            mark_safe(html_content)
        )

        response_data = {
            'html': wrapped_html,
            'status': status,#
            'object': serializer_class(obj).data,
            'refresh': refresh_data
        }
        return JsonResponse(response_data)
    
    def ajax_editable_fields(self):
        """The fields this endpoint may write.

        Declared, never inferred. The previous version walked `obj._meta.fields` and saved any
        POST key that matched a field name, which made the endpoint a general-purpose writer for
        every column on the model -- including ones the admin renders read-only.
        """
        declared = list(getattr(self, "ajax_shift_fields", []) or [])
        declared += list(getattr(self, "list_editable", []) or [])
        declared += list(getattr(self, "list_refresh", []) or [])
        return {name for name in declared}

    def save_ajax_fields(self, obj, request):
        """Updates the editable model fields from POST data."""
        allowed = self.ajax_editable_fields()
        for field in obj._meta.fields:
            if field.name in request.POST and field.name in allowed:
                handle_ajax_field_save(obj, field.name, request.POST.get(field.name))

    def ajax_update_view(self, request, object_id):
        """Standard entry point for AJAX updates: saves fields then triggers tasks.

        `admin_site.admin_view` only asks whether the user is active staff -- it knows nothing
        about this model. Without the checks below, any staff account could write any field on
        any row and start a PAID generation through this URL, while the changelist for the same
        model correctly answered 403. Every other spend guard in the admin sits on the actions,
        so this endpoint walked around all of them.
        """
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])

        # get_queryset(), not the raw manager: it carries the per-story scoping the filter
        # mixins apply, so a user cannot reach a row that their own changelist would hide.
        obj = get_object_or_404(self.get_queryset(request), pk=object_id)
        if not self.has_change_permission(request, obj):
            raise PermissionDenied

        target_field = request.POST.get('target_field')
        self.save_ajax_fields(obj, request)
        self.trigger_ajax_task(request, obj, target_field)
        # Return refreshed data immediately
        return self.get_last_tasks(request, object_id)

    def trigger_ajax_task(self, request, obj, target_field):
        """Hook for triggering specific background tasks based on the updated field."""
        if target_field == 'prompt':
            if Task.createTaskIfQueueEnabled( obj, settings.TASK_TYPE_GENERATE_IMAGE, owner=request.user) is None:
                obj.generate_image(user=request.user)
        elif target_field == 'prompt_refine':
            if Task.createTaskIfQueueEnabled( obj, settings.TASK_TYPE_REFINE_IMAGE, owner=request.user) is None:
                obj.refine_image(user=request.user)
        elif target_field == 'prompt_comic':
            if Task.createTaskIfQueueEnabled( obj, settings.TASK_TYPE_GENERATE_COMIC, owner=request.user) is None:
                obj.generate_comic(user=request.user)
        elif target_field == 'prompt_video':
            if Task.createTaskIfQueueEnabled( obj, settings.TASK_TYPE_GENERATE_VIDEO, owner=request.user) is None:
                obj.generate_omni_video(obj.TASK_TYPE_GENERATE_VIDEO, user=request.user)
        elif target_field == 'prompt_voice':
            if Task.createTaskIfQueueEnabled( obj, settings.TASK_TYPE_GENERATE_VOICE, owner=request.user) is None:
                obj.generate_voice(obj.PRESET_VOICE, user=request.user)
