from .models import Task
class AfterSaveActionMixin:
    """
    Mixin to automatically trigger a task after saving a model 
    if an 'action' field is set.
    """
    def save_model(self, request, obj, form, change):
        action = getattr(obj, 'action', None)
        if action:
            # Clear the action field so it doesn't re-trigger on every save
            obj.action = None
        
        super().save_model(request, obj, form, change)
        
        if action:
            if hasattr(obj, 'task_from_action'):
                obj.task_from_action(action, request.user)
            else:    
                Task.createTaskIfQueueEnabled(
                    subject=obj,
                    task_type=action,
                    owner=request.user
                )
