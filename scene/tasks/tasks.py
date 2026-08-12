from datetime import timedelta
from django.utils import timezone
from task.models import Task
from agent.models import GetContentsMixin
from django.utils.text import slugify
from django.conf import settings
from django.core.files.base import ContentFile


def get_next_scheduled_timestamp():
    """
    Finds the latest scheduled task time and returns a new timestamp
    just after it, or the current time if no future tasks are scheduled.
    """
    latest_scheduled_task = Task.objects.filter(
        status=Task.TASK_STATUS_SCHEDULED,
        scheduled_at__isnull=False
    ).order_by('-scheduled_at').first()

    if latest_scheduled_task and latest_scheduled_task.scheduled_at > timezone.now():
        return latest_scheduled_task.scheduled_at
    return timezone.now()


class TaskGenerateImage:
    
    def __init__(self, task):
        self.task = task

    def process(self):
        self.task.set_status(Task.TASK_STATUS_STARTED)
        item = self.task.subject
        item.generate_image(user=self.task.owner)
        self.task.set_status(Task.TASK_STATUS_SUCCESS)


class TaskRefineImage:
    
    def __init__(self, task):
        self.task = task
        
    def process(self):
        item = self.task.subject
        old_image = item.image.url
        item.refine_image(user=self.task.owner)
        image = item.image.url
        print(f"Refined image for item ID {item.id} from {old_image} to {image}")

class TaskGenerateVideo:
    def __init__(self, task):
        self.task = task
    def process(self):
        item = self.task.subject
        item.generate_video(GetContentsMixin.PRESET_VIDEO, user=self.task.owner)

class TaskGenerateOmniVideo:
    def __init__(self, task):
        self.task = task
    def process(self):
        item = self.task.subject
        item.generate_image_omni_video(GetContentsMixin.PRESET_OMNI_VIDEO, user=self.task.owner)


class TaskGenerateVoice:
    def __init__(self, task):
        self.task = task
    def process(self):
        item = self.task.subject
        item.generate_voice(GetContentsMixin.PRESET_VOICE, user=self.task.owner)

class TaskGenerateVideoFirstLast:
    def __init__(self, task):
        self.task = task
        
    def process(self):
        item = self.task.subject
        item.generate_video(GetContentsMixin.PRESET_VIDEO_FIRST_LAST, user=self.task.owner)

class TaskGenerateComic:
    def __init__(self, task):
        self.task = task
    def process(self):
        item = self.task.subject
        item.image_comic = item.generate_comic(user=self.task.owner, target_field="image_comic")
        item.save()

class TaskExtractScene:
    def __init__(self, task):
        self.task = task
    def process(self):
        item = self.task.subject
        log = item.generate_scene(user=self.task.owner)
        self.task.log(log)


class TaskGenerateScene:
    """
    Extract scene from text and generate a scene object with associated media.
    """
    def __init__(self, task):
        self.task = task
    def process(self):
        item = self.task.subject
        item.generate_scene(user=self.task.owner)
        item.save()


class TaskGenerateElements:
    """
    Iterates through all elements of a scene or story and triggers image generation 
    for any missing background (location), character (cast/actor), or prop,
    as well as voice generation for missing character voice samples.
    """
    def __init__(self, task):
        self.task = task

    def process(self):
        subject = self.task.subject
        if hasattr(subject, 'get_missing_elements'):
            elements_to_generate = subject.get_missing_elements()
        else:
            elements_to_generate = set()

        timestamp = get_next_scheduled_timestamp()
        for i, element in enumerate(elements_to_generate):
            self.task.log(f"Queueing image generation for {element.name} ({element.__class__.__name__})")
            task_timestamp = timestamp + timedelta(minutes=i + 1)
            task = Task.createTaskIfQueueEnabled(
                subject=element,
                task_type=settings.TASK_TYPE_GENERATE_IMAGE,
                thr=subject,
                owner=self.task.owner,
                process=False
            )
            if task:
                task.process(timestamp=task_timestamp)


class TaskGenerateShots:
    """
    Generates images for all actions in a scene.
    Ensures that if an action's element is missing an image, 
    the element's generation task is queued immediately before the action's task.
    """
    def __init__(self, task):
        self.task = task

    def process(self):
        scene = self.task.subject
        element_tasks_buffer = {}  # key: (model_name, id)
        timestamp = get_next_scheduled_timestamp()

        action_tasks = []
        for action in scene.actions.all().order_by('order'):
            # Elements required for this action
            elements = []
            if action.background: elements.append(action.background)
            if action.actor: elements.append(action.actor)
            elements.extend(list(action.cast.all()))
            elements.extend(list(action.props.all()))

            # Identify tasks for missing element images
            action_dependencies = []
            for element in elements:
                if not element.image:
                    key = (element._meta.model_name, element.id)
                    if key not in element_tasks_buffer:
                        e_task = Task.createTaskIfQueueEnabled(
                            subject=element,
                            task_type=settings.TASK_TYPE_GENERATE_IMAGE,
                            thr=scene,
                            owner=self.task.owner,
                            process=False
                        )
                        if e_task:
                            element_tasks_buffer[key] = e_task
                    
                    if key in element_tasks_buffer:
                        action_dependencies.append(element_tasks_buffer[key])

            # Queue action image generation
            if not action.image:
                action_task = Task.createTaskIfQueueEnabled(
                    subject=action,
                    task_type=settings.TASK_TYPE_GENERATE_IMAGE,
                    thr=scene,
                    owner=self.task.owner,
                    process=False
                )
                if action_task:
                    self.task.log(f"Queuing image generation for action: {action.get_name()}")
                    # we are queue the tasks anyway
                    #for dep_task in action_dependencies:
                    #    dep_task.next_tasks.add(action_task)
                    action_tasks.append(action_task)

        minute_offset = 1
        # Trigger processing for all element tasks in the buffer
        for e_task in element_tasks_buffer.values():
            timestamp = timestamp + timedelta(minutes=minute_offset)
            e_task.process(timestamp=timestamp)
        for a_task in action_tasks:
            timestamp = timestamp + timedelta(minutes=minute_offset)
            a_task.process(timestamp=timestamp)

class TaskGenerateVoices:
    """
    Iterates through all voices associated with a scene (via characters and actions)
    and queues a generation task for any that are missing their audio sample.
    """
    def __init__(self, task):
        self.task = task

    def process(self):
        scene = self.task.subject
        voices = set()

        for action in scene.actions.all():
           if action.prompt_voice and action.voice and not action.voice.audio_voice:
                self.task.log(f"Queueing voice generation for {action.name}")
                Task.createTaskIfQueueEnabled(
                    subject=action,
                    task_type=settings.TASK_TYPE_GENERATE_VOICE,
                    thr=scene,
                    owner=self.task.owner
                )

class TaskGenerateComics:
    def __init__(self, task):
        self.task = task

    def process(self):
        scene = self.task.subject
        timestamp = get_next_scheduled_timestamp()
        comic_tasks = []

        for action in scene.actions.all().order_by('order'):
            if action.prompt_comic and not action.image_comic:
                task = Task.createTaskIfQueueEnabled(
                    subject=action,
                    task_type=settings.TASK_TYPE_GENERATE_COMIC,
                    thr=scene,
                    owner=self.task.owner,
                    process=False
                )
                if task:
                    self.task.log(f"Queuing comic generation for action: {action.get_name()}")
                    comic_tasks.append(task)

        minute_offset = 1
        for i, c_task in enumerate(comic_tasks):
            task_timestamp = timestamp + timedelta(minutes=(i * minute_offset))
            c_task.process(timestamp=task_timestamp)