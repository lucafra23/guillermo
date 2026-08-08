from agent.models import GetContentsMixin

class TaskGenerateText:
    def __init__(self, task):
        self.task = task

    def process(self):
        item = self.task.subject
        agent = self.task.thr
        payload = self.task.payload or {}
        preset = payload.get('preset', GetContentsMixin.PRESET_REFINE_PROMPT)
        message = payload.get('message', None)

        prompts = payload.get('prompts', [])
        target_field = payload.get('target_field', "prompt")
        schema = payload.get('schema', None)

        item.generate_text(
            agent=agent, 
            preset=preset, 
            message=message, 
            instructions=prompts, 
            schema=schema,
            user=self.task.owner,
            target_field=target_field
        )