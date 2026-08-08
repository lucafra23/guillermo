import json
from django.db import models
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.utils.translation import gettext_lazy as _
from django.db.models import Q
from django.conf import settings
from django.utils.safestring import mark_safe
from django_celery_beat.models import ClockedSchedule, PeriodicTask
from django_celery_beat.models import PeriodicTask, ClockedSchedule
from django.template.loader import render_to_string

def format_html(html):
    return html

class Task(models.Model):
    """
        A task is executed by the queue.
    """
    TASK_STATUS_STARTED = 0
    TASK_STATUS_PENDING = 1
    TASK_STATUS_HOLDING = 2
    TASK_STATUS_ERROR = 3
    TASK_STATUS_SUCCESS = 4
    TASK_STATUS_SCHEDULED = 5
    TASK_STATUS_RETRY = 6
    

    TASK_STATUS_PROCESSABLE = [
        TASK_STATUS_PENDING,
        TASK_STATUS_HOLDING,
        TASK_STATUS_ERROR,
        TASK_STATUS_SCHEDULED,
        TASK_STATUS_RETRY,
    ]
    
    TASK_STATUS_CHOICES = (
        (TASK_STATUS_PENDING, "Pending"),
        (TASK_STATUS_STARTED, "Started"),
        (TASK_STATUS_SUCCESS, "Success"),
        (TASK_STATUS_ERROR, "Error"),
        (TASK_STATUS_HOLDING, "Holding"),
        (TASK_STATUS_SCHEDULED, "Scheduled"),
        (TASK_STATUS_RETRY, "Retry"),
    )
    
    TASK_STATUS_DICT = dict(TASK_STATUS_CHOICES)

    COLOR_DICT = {
        TASK_STATUS_STARTED: 'blue',
        TASK_STATUS_PENDING: 'orange',
        TASK_STATUS_HOLDING: 'lime',
        TASK_STATUS_SUCCESS: 'green',
        TASK_STATUS_ERROR: 'red',
        TASK_STATUS_SCHEDULED: 'amber',
        TASK_STATUS_RETRY: 'black',
    }
    
    
    TASK_TYPE_DICT = dict(settings.TASK_TYPE_CHOICES)

    QUEUE_NORMAL = 'queue.normal'
    QUEUE_HEAVY = 'queue.heavy'
    

    subject_ct = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        related_name="subject"
    )
    subject_id = models.PositiveIntegerField()
    subject = GenericForeignKey('subject_ct', 'subject_id')

    obj_ct = models.ForeignKey(
        ContentType,
        related_name="object",
        on_delete=models.CASCADE,
        blank=True,
        null=True
    )
    obj_id = models.PositiveIntegerField(
        blank=True,
        null=True
    )
    obj = GenericForeignKey('obj_ct', 'obj_id')
    thr_ct = models.ForeignKey(
        ContentType,
        related_name="thr",
        on_delete=models.CASCADE,
        blank=True,
        null=True
    )
    thr_id = models.PositiveIntegerField(
        blank=True,
        null=True
    )
    thr = GenericForeignKey('thr_ct', 'thr_id')

    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)

    status = models.IntegerField(choices=TASK_STATUS_CHOICES, default=1)
    payload = models.JSONField(blank=True, null=True)
    task_type = models.SlugField(choices=settings.TASK_TYPE_CHOICES)
    
    next_tasks = models.ManyToManyField(
        'self',
        blank=True,
        symmetrical=False,
        related_name='previous_tasks'
    )
    owner = models.ForeignKey(
        "auth.User",
        null=True,
        blank=True,
        on_delete=models.CASCADE
    )
    
    retry_attempts = models.PositiveIntegerField(default=0)
    retry_max_attempts = models.PositiveIntegerField(default=3)
    retry_countdown = models.PositiveIntegerField(default=3) # in seconds

    scheduled_task = models.OneToOneField(
        'django_celery_beat.PeriodicTask',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='custom_task',
        help_text=_("Link to the scheduled task in Django Celery Beat.")
    )
    scheduled_at = models.DateTimeField(_("scheduled at"), null=True, blank=True)

    class Meta:
        ordering = ['-modified', 'status']

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

    def set_status(self, status):
        self.status = status
        self.save()

    def color(self):
        return self.COLOR_DICT[self.status]

    def status_label(self):
        return self.TASK_STATUS_DICT[self.status]

    def type_label(self):
        if self.task_type not in self.TASK_TYPE_DICT:
            return self.task_type
        return self.TASK_TYPE_DICT[self.task_type]

    def is_processable(self):
        return self.status in self.TASK_STATUS_PROCESSABLE

    def has_pending_previous(self):
        """
        Checks if any task that must run before this one is not yet successful.
        """
        not_success = ~Q(status=self.TASK_STATUS_SUCCESS)
        return self.previous_tasks.filter(not_success).exists()

    def process(self, countdown=0, timestamp=None):
        from .tasks import process_task
        if timestamp:
            # Create a one-off schedule for the specified timestamp
            schedule, _ = ClockedSchedule.objects.get_or_create(
                clocked_time=timestamp
            )

            # Create a periodic task to run once at the scheduled time
            periodic_task = PeriodicTask.objects.create(
                name=f'task-{self.id}-scheduled-at-{timestamp.isoformat()}',
                task='task.tasks.process_task',
                args=json.dumps([self.id]),
                clocked=schedule,
                one_off=True,
                enabled=True,
            )

            # Link the periodic task, set the status, and save
            self.scheduled_task = periodic_task
            self.scheduled_at = timestamp
            self.set_status(self.TASK_STATUS_SCHEDULED)
        else:
            process_task.apply_async(kwargs={'task_id': self.id}, countdown=countdown)
            
    def get_queue(self):
        queue = self.QUEUE_NORMAL
        if self.task_type in self.QUEUE_DICT:
            return Task.QUEUE_DICT[self.task_type]
        return queue
    
    def last_logs(self):
        out = '--'
        logs = self.tasklog_set.all()
        if logs.count() > 0:
            out = logs.last().text
        return out


    @staticmethod
    def createTaskIfQueueEnabled(subject,
            task_type,
            next=None,
            owner=None,
            obj=None,
            thr=None,
            payload=None,
            process=True):
        if settings.USE_TASK_QUEUE:
            return  Task.createTask(
                subject=subject,
                task_type=task_type,
                next=next,
                owner=owner,
                obj=obj,
                thr=thr,
                payload=payload,
                process=process
            )
        return None

    @staticmethod
    def createTask(
            subject,
            task_type,
            next=None,
            owner=None,
            obj=None,
            thr=None,
            payload=None,
            process=True
    ):
        task = Task.objects.create(
           subject=subject,
           task_type=task_type,
           obj=obj,
           thr=thr,
           owner=owner,
           payload=payload
        )
        if next:
            if hasattr(next, '__iter__') and not isinstance(next, (str, bytes)):
                task.next_tasks.set(next)
            else:
                task.next_tasks.add(next)

        if process:
            task.process()
        return task

    def log(self, message, level='error'):
        log = TaskLog.objects.create(
            task=self,
            level=level,
            text=message
        )
        return log
    
    def html_status(self):
        out = render_to_string(
            'task_status.html',
            {'item': self}
        )
        return mark_safe(out)




class TaskLog(models.Model):
    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)

    TASK_LOG_LEVEL_ERROR = 'error'
    TASK_LOG_LEVEL_WARNING = 'warning'

    TASK_LOG_LEVELS = (
        (TASK_LOG_LEVEL_ERROR, 'Error'),
        (TASK_LOG_LEVEL_WARNING, 'Warning')
    )
    task = models.ForeignKey(Task, on_delete=models.CASCADE)
    
    text = models.TextField()
    level = models.SlugField(
        choices=TASK_LOG_LEVELS,
        default=TASK_LOG_LEVEL_ERROR
    )

class TaskPreset(models.Model):
    PRESET_TYPE_CHOICES = settings.TASK_TYPE_CHOICES
    name = models.CharField(
        max_length=1024,
        unique=True
    )
    description = models.TextField()
    preset_type = models.SlugField(choices=PRESET_TYPE_CHOICES)
    system_default = models.BooleanField(_("Default"), default=False)
    preset = models.JSONField()

    @staticmethod
    def get(preset_type):
        presets = TaskPreset.objects.filter(
            system_default=True,
            preset_type=preset_type
        )
        preset = None
        if presets.exists():
            preset = presets.first().preset

        return preset

    def __str__(self):
        return f'{self.name}'
    
class TaskHolder:
    """
    Mixin to add tasks functionality
    """
    @property
    def tasks(self):
        ctype = ContentType.objects.get_for_model(self.__class__)
        obj = Q(obj_ct__pk=ctype.id, obj_id=self.id)
        subject = Q(
            subject_ct__pk=ctype.id,
            subject_id=self.id
        )
        thr = Q(
            thr_ct__pk=ctype.id,
            thr_id=self.id
        )
        tasks = Task.objects.filter(
           subject | obj | thr
        )
        return tasks

    def last_tasks(self):
        """
        mark in the admin with the last tasks
        """
        out = render_to_string(
            'task_dropdown.html',
            {'tasks': self.tasks, 'instance': self}
        )
        return mark_safe(out)
    
    