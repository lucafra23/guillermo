from pydantic import BaseModel
from typing import List, Optional

from agent.models import Prompt


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


class StoryElementsSchema(BaseModel):
    locations: List[BackgroundSchema]
    characters: List[CharacterSchema]
    props: List[PropSchema]
    voices: List[VoiceSchema]


class SceneSchema(BaseModel):
    name: str
    locations: List[BackgroundSchema]
    characters: List[CharacterSchema]
    props: List[PropSchema]
    shots: List[ActionSchema]
    voices: List[VoiceSchema]

    def sync_model(self, scene):
        """
        Syncs the structured Pydantic data with Django models.
        Assumes source provides get_story() and get_scene().
        """
        story = scene.story
        scene.name = self.name
        scene.save()

        # 1. Sync Voices for the Story
        voice_map = {}
        if self.voices:
            from agent.models import GoogleVoice
            from .models import Scene, Action, Character, Prop, Background, Voice

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

        # 2. Sync Global Locations (Backgrounds) for the Story
        location_map = {}
        if self.locations:
            for back_data in self.locations:
                item = Background.objects.update_or_create(
                    name=back_data.name,
                    story=story,
                    defaults={'prompt': back_data.prompt}
                )   
                location_map[item[0].name] = item

        # 3. Sync Global Characters for the Story
        char_map = {}
        if self.characters:
            for char_data in self.characters:
                item = Character.objects.update_or_create(
                    name=char_data.name,
                    story=story,
                    defaults={'prompt': char_data.prompt}
                )
                char_map[item[0].name] = item

        # 4. Sync Global Props for the Story
        prop_map = {}
        if self.props:
            for prop_data in self.props:
                item = Prop.objects.update_or_create(
                    name=prop_data.name,
                    story=story,
                    defaults={'prompt': prop_data.prompt}
                )
                prop_map[item[0].name] = item

        # 5. Sync Shots for the Scene
        shot_map = {}
        if self.shots:
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
                shot_map[shot.name] = shot

        return {
            'locations': [(item[0].name, item[1]) for item in location_map.values()],
            'characters': [(item[0].name, item[1]) for item in char_map.values()],
            'props': [(item[0].name, item[1]) for item in prop_map.values()],
            'voices': [(item[0].name, item[1]) for item in voice_map.values()],
            'actions': [item.name for item in shot_map.values()]
        }


class MultiSceneSchema(BaseModel):
    scenes: List[SceneSchema]

    def sync_model(self, source):
        last_scene = None
        for scene_data in self.scenes:
            last_scene = scene_data.sync_model(source)
        return last_scene


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
    

class StorySceneItemSchema(BaseModel):
    name: str
    prompt_plot: str
    order: int


class StoryScenesSchema(BaseModel):
    scenes: List[StorySceneItemSchema]

    def sync_model(self, story):
        from .models import Scene
        synced_scenes = []
        for scene_data in self.scenes:
            scene, created = Scene.objects.update_or_create(
                story=story,
                name=scene_data.name,
                defaults={
                    'prompt_plot': scene_data.prompt_plot,
                    'order': scene_data.order,
                }
            )
            synced_scenes.append(scene)
        return {
            'scenes': [
                {'name': s.name, 'prompt_plot': s.prompt_plot, 'order': s.order}
                for s in synced_scenes
            ]
        }