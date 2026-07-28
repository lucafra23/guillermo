from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from celery.signals import worker_ready
from billiard.exceptions import Terminated
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
    if task.status == Task.TASK_STATUS_STARTED:
        print(f"Attempt to process a started task {task.id}")
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
        except Terminated as e:
            task.log(
                "Task was terminated abruptly (Celery worker is shutting down or the task was explicitly revoked).\n"
                f"{traceback.format_exc()}"
            )
            task.set_status(Task.TASK_STATUS_ERROR)
        except Exception as e:
            task.log(
                f"Task failed with {e.__class__.__name__}: {e}\n"
                f"{traceback.format_exc()}"
            )
            task.set_status(Task.TASK_STATUS_ERROR)
            # getattr, not e.status: only google-genai's APIError carries `.status`. Any other
            # failure — a ValueError from response handling, an OSError, a bug in a delegate —
            # raised AttributeError right here, which replaced the real error in the Celery log
            # with a confusing one and skipped the next_tasks dispatch below.
            if task.retry_attempts < task.retry_max_attempts \
                    and getattr(e, 'status', None) in settings.TASK_RETRY_EXCEPTIONS:
                task.retry_attempts += 1
                task.retry_countdown += task.retry_countdown + random.randint(0, 5) 
                task.save(update_fields=['retry_attempts', 'retry_countdown'])
                task.set_status(Task.TASK_STATUS_RETRY)
                task.process(countdown=task.retry_countdown)
            return # Stop further processing for this run


@worker_ready.connect
def cleanup_stuck_tasks(sender, **kwargs):
    """
    Fires automatically when a Celery worker starts up.
    Finds tasks stuck in the 'Started' state from a previous crashed run and marks them as Error.
    """
    stuck_tasks = Task.objects.filter(status=Task.TASK_STATUS_STARTED)
    count = stuck_tasks.count()
    for task in stuck_tasks:
        task.log("Task marked as Error because the Celery worker process died or restarted during execution.")
        task.set_status(Task.TASK_STATUS_ERROR)
            

        