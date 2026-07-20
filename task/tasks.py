from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from .models import Task
import importlib
import traceback
import random

@shared_task
def process_task(task_id):
    try:
        task = Task.objects.get(id=task_id)
    except Task.DoesNotExist:
        print(f"Skipping missing task {task_id}")
        return

    print(f"Processing task {task.id} ")
    if task.has_pending_previous():
        task.set_status(Task.TASK_STATUS_HOLDING)
    elif task.is_processable():
        delegate = settings.TASK_DELEGATES[task.task_type]
        delegate_package = delegate.rsplit(".", 1)[0]
        delegate_class = delegate.rsplit(".", 1)[1]
        Delegate = getattr(importlib.import_module(delegate_package),delegate_class)
        print(f"Delegate the task to {delegate}")
        delegate = Delegate(task)
        try:
            task.set_status(Task.TASK_STATUS_STARTED)
            delegate.process()
            if task.status == Task.TASK_STATUS_STARTED:
                task.set_status(Task.TASK_STATUS_SUCCESS)
                for next_task in task.next_tasks.all():
                    if next_task.is_processable() and not next_task.has_pending_previous():
                        process_task.delay(next_task.id)

        except SoftTimeLimitExceeded as e:
            task.log(
                f"Task timed out after {settings.CELERY_TASK_SOFT_TIME_LIMIT} seconds.\n"
                f"The work was stopped by Celery before it could finish.\n\n"
                f"{traceback.format_exc()}"
            )
            task.set_status(Task.TASK_STATUS_ERROR)
        except Exception as e:
            task.log(
                f"Task failed with {e.__class__.__name__}: {e}\n"
                f"{traceback.format_exc()}"
            )
            task.set_status(Task.TASK_STATUS_ERROR)
            if task.retry_attempts < task.retry_max_attempts and hasattr(e, 'status') and e.status in settings.TASK_RETRY_EXCEPTIONS:
                task.retry_attempts += 1
                task.retry_countdown += task.retry_countdown + random.randint(0, 5) 
                task.save(update_fields=['retry_attempts', 'retry_countdown'])
                task.set_status(Task.TASK_STATUS_RETRY)
                task.process(countdown=task.retry_countdown)
            return # Stop further processing for this run
            

        