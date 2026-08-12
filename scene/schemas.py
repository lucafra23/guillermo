from pydantic import BaseModel
from typing import List, Optional

from agent.models import Prompt

class SyncReport(dict):
    def __init__(self, name, instance, created, edited, fields_edited):
        super().__init__({
            'name': name,
            'instance': instance,
            'created': created,
            'edited': edited,
            'fields_edited': fields_edited
        })
        self.name = name
        self.instance = instance
        self.created = created
        self.edited = edited
        self.fields_edited = fields_edited

def get_asset_sync_info(instance, created):
    fields_edited = []
    if not created and hasattr(instance, 'history'):
        try:
            latest = instance.history.first()
            if latest:
                prev = latest.prev_record
                if prev:
                    delta = latest.diff_against(prev)
                    fields_edited = [change.field for change in delta.changes]
        except Exception:
            pass
    
    return SyncReport(
        name=getattr(instance, 'name', str(instance)),
        instance=instance,
        created=created,
        edited=not created and len(fields_edited) > 0,
        fields_edited=fields_edited
    )

class Parameters(BaseModel):
    buffer_in: Optional[float] = None
    buffer_out: Optional[float] = None
    duration: Optional[int] = None
    iterations: Optional[int] = None

class VoiceSchema(BaseModel):
    name: str
    prompt: str
    google_voice: Optional[str] = None

class GoogleVoiceSchema(BaseModel):
    name: str
    description: str

class CharacterSchema(BaseModel):
    name: str
    prompt: str

class PropSchema(BaseModel):
    name: str
    prompt: str

class BackgroundSchema(BaseModel):
    name: str
    prompt: str

class ActionSchema(BaseModel):
    name: str
    order: int
    prompt_comic: str
    prompt_video: str
    prompt_voice: str
    prompt: str
    cast: List[str]
    props: List[str]
    background: str
    voice: str
    parameters: Optional[Parameters] = None
    shot_type: Optional[str] = None


class AssetsSchema(BaseModel):
    locations: List[BackgroundSchema]
    characters: List[CharacterSchema]
    props: List[PropSchema]
    voices: List[VoiceSchema]

    def sync_model(self, obj):
        from agent.models import GoogleVoice
        from .models import Character, Prop, Background, Voice, Scene, Story

        scene, story = None, None
        if isinstance(obj, Story):
            story = obj
        elif isinstance(obj, Scene):
            scene = obj
            story = scene.story
        voice_map = {}
        if self.voices:
            for voice_data in self.voices:
                gv = GoogleVoice.objects.filter(name=voice_data.google_voice).first() if voice_data.google_voice else None
                item = Voice.objects.update_or_create(
                    name=voice_data.name,
                    story=story,
                    defaults={
                        'prompt': voice_data.prompt,
                        'google_voice': gv
                    }
                )
                voice_map[item[0].name] = item

        location_map = {}
        if self.locations:
            for back_data in self.locations:
                item = Background.objects.update_or_create(
                    name=back_data.name,
                    story=story,
                    defaults={'prompt': back_data.prompt}
                )   
                location_map[item[0].name] = item

        char_map = {}
        if self.characters:
            for char_data in self.characters:
                item = Character.objects.update_or_create(
                    name=char_data.name,
                    story=story,
                    defaults={'prompt': char_data.prompt}
                )
                char_map[item[0].name] = item

        prop_map = {}
        if self.props:
            for prop_data in self.props:
                item = Prop.objects.update_or_create(
                    name=prop_data.name,
                    story=story,
                    defaults={'prompt': prop_data.prompt}
                )
                prop_map[item[0].name] = item

        if scene is not None:
            if location_map:
                scene.locations.set([item[0] for item in location_map.values()])
            if char_map:
                scene.cast.set([item[0] for item in char_map.values()])
            if prop_map:
                scene.props.set([item[0] for item in prop_map.values()])
            if voice_map:
                scene.voices.set([item[0] for item in voice_map.values()])

        sync_info = {
            'locations': [get_asset_sync_info(item[0], item[1]) for item in location_map.values()],
            'characters': [get_asset_sync_info(item[0], item[1]) for item in char_map.values()],
            'props': [get_asset_sync_info(item[0], item[1]) for item in prop_map.values()],
            'voices': [get_asset_sync_info(item[0], item[1]) for item in voice_map.values()]
        }

        return sync_info, voice_map, location_map, char_map, prop_map


class SceneSchema(BaseModel):
    name: str
    order: Optional[int] = 0
    prompt_plot: Optional[str] = None
    locations: Optional[List[BackgroundSchema]] = []
    characters: Optional[List[CharacterSchema]] = []
    props: Optional[List[PropSchema]] = []
    voices: Optional[List[VoiceSchema]] = []
    shots: Optional[List[ActionSchema]] = []

    def sync_model(self, scene):
        """
        Syncs the structured Pydantic data with Django models.
        Assumes source provides get_story() and get_scene().
        """
        story = scene.story
        was_created = scene._state.adding
        scene.name = self.name
        scene.save()

        # Sync assets using AssetsSchema
        assets = AssetsSchema(
            locations=self.locations or [],
            characters=self.characters or [],
            props=self.props or [],
            voices=self.voices or []
        )
        sync_info, voice_map, location_map, char_map, prop_map = assets.sync_model(scene)

        # 5. Sync Shots for the Scene
        shot_reports = []
        if self.shots:
            from .models import Action
            for i, shot_data in enumerate(self.shots):
                voice_obj = voice_map.get(shot_data.voice)[0] if shot_data.voice in voice_map else None
                bg_obj = location_map.get(shot_data.background)[0] if shot_data.background in location_map else None

                shot, created = Action.objects.update_or_create(
                    scene=scene,
                    name=shot_data.name,
                    defaults={
                        'order': shot_data.order,
                        'prompt': shot_data.prompt,
                        'prompt_comic': shot_data.prompt_comic,
                        'prompt_video': shot_data.prompt_video,
                        'prompt_voice': shot_data.prompt_voice,
                        'parameters': shot_data.parameters.model_dump() if shot_data.parameters else None,
                        'voice': voice_obj,
                        'background': bg_obj,
                        'shot_type': shot_data.shot_type
                    }
                )
                if shot_data.cast:
                    shot.cast.set([char_map[name][0] for name in shot_data.cast if name in char_map])
                if shot_data.props:
                    shot.props.set([prop_map[name][0] for name in shot_data.props if name in prop_map])
                shot_reports.append(get_asset_sync_info(shot, created))

        scene_report = get_asset_sync_info(scene, was_created)

        return {
            **sync_info,
            'actions': shot_reports,
            'scene': scene_report
        }


class MultiSceneSchema(BaseModel):
    scenes: List[SceneSchema]

    def sync_model(self, source):
        results = []
        for scene_data in self.scenes:
            results.append(scene_data.sync_model(source))
        return results


class OutputWithMessageSchema(BaseModel):
    message: str
    output: str

    def sync_model(self, source):
        return dict(self)

    def get_output(self):
        return self.output


class CreateInstructionsSchema(OutputWithMessageSchema):
    def sync_model(self, source):
        Prompt.objects.update_or_create(
            name=f"Prompt Automatically Created",
            defaults={
                'prompt': self.output,
            }
        )
        return dict(self)


class StoryScenesSchema(BaseModel):
    scenes: List[SceneSchema]

    def sync_model(self, story):
        from .models import Scene
        synced_scenes = []
        for scene_data in self.scenes:
            scene, created = Scene.objects.update_or_create(
                story=story,
                name=scene_data.name,
                defaults={
                    'prompt_plot': scene_data.prompt_plot,
                    'order': scene_data.order or 0,
                }
            )
            scene_data.sync_model(scene)
            synced_scenes.append(get_asset_sync_info(scene, created))
        return {
            'scenes': synced_scenes
        }