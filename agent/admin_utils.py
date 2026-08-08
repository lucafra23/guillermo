
from django.conf import settings
from django.http import JsonResponse
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
        # 1. Get the object
        obj = get_object_or_404(self.model, pk=object_id)
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
    
    def save_ajax_fields(self, obj, request):
        """Updates model fields from POST data, handling Booleans and Foreign Keys."""
        for field in obj._meta.fields:
            if field.name in request.POST:
                handle_ajax_field_save(obj, field.name, request.POST.get(field.name))

    def ajax_update_view(self, request, object_id):
        """Standard entry point for AJAX updates: saves fields then triggers tasks."""
        obj = get_object_or_404(self.model, pk=object_id)
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
