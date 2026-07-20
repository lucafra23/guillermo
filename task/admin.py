from django.contrib import admin
from unfold.admin import ModelAdmin
from django.urls import reverse
from django.utils.html import format_html

from .models import Task, TaskLog, TaskPreset


@admin.register(Task)
class TaskAdmin(ModelAdmin):
    list_display = (
        'id', 'created','owner', 'task_type', 'payload', 'html_status', 
        'last_logs', 'retries', 'view_logs_link'
    )
    list_filter = ('task_type', 'status', 'created', 'modified', 'owner', 'subject_ct', 'subject_id')
    search_fields = ('id', 'owner__username', 'task_type', 'status')
    readonly_fields = ('retry_attempts',)
    actions = [
        'reprocess'
    ]

    def retries(self, obj):
        return f"{obj.retry_attempts} / {obj.retry_max_attempts}"
    retries.short_description = "Retries"

    def view_logs_link(self, obj):
        count = obj.tasklog_set.count()
        url = (
            reverse("admin:task_tasklog_changelist")
            + f"?task__id__exact={obj.id}"
        )
        return format_html('<a href="{}">{} Logs</a>', url, count)
    view_logs_link.short_description = "Logs"

    def reprocess(self, request, queryset):
        for item in queryset:
            msg = f"The task {item.id} has been queued for reprocessing"
            item.process()
            self.message_user(
                request,
                msg
            )
    reprocess.short_description = "Retry"

@admin.register(TaskLog)
class TaskLogAdmin(ModelAdmin):
    list_display = ('id', 'task', 'created', 'level', 'text')
    list_filter = ('level', 'created', 'task')
    search_fields = ('text', 'task__id')


@admin.register(TaskPreset)
class TaskPresetAdmin(ModelAdmin):
    list_display = (
        'id', 'name', 'preset_type',
        'description', 'preset', 'system_default'
    )
    list_editable = ('name', 'description', 'preset_type', 'system_default')
