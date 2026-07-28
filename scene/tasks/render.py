from moviepy import ImageClip, VideoFileClip, AudioFileClip, concatenate_videoclips
from moviepy.video.fx import Resize
from audiostretchy.stretch import stretch_audio
import librosa
import soundfile as sf
from audiostretchy.stretch import stretch_audio
from pedalboard import Pedalboard, Reverb
from pedalboard.io import AudioFile
import pyrubberband as pyrb
import soundfile as sf
from task.models import Task
from agent.models import GetContentsMixin
from django.utils.text import slugify
from django.conf import settings
from django.core.files.base import ContentFile
from moviepy.audio.fx import MultiplyVolume
import random
import os

def speed_audio(input_file, stretched_file, speed=1.2):
    stretch_audio(input_file, stretched_file, ratio=1/speed, gap_ratio=1/speed)
    y, sr = librosa.load(stretched_file, sr=None)
    # Trim leading and trailing silence (top_db=30 trims sounds 30 dB below peak volume)
    y_trimmed, _ = librosa.effects.trim(y, top_db=30)
    # Save the trimmed audio back out
    sf.write(stretched_file, y_trimmed, sr)
    board = Pedalboard([
        Reverb(
            room_size=0.25,      # Small room feel for speech (0.0 to 1.0)
            damping=0.5,         # High frequency attenuation
            wet_level=0.15,      # Amount of reverb effect (15%)
            dry_level=0.85,      # Amount of original voice (85%)
        )
    ])
    # Read from the trimmed file, apply the Pedalboard chain, and write to final file
    with AudioFile(stretched_file) as f:
        audio = f.read(f.frames)
        sr = f.samplerate
    # Apply pedalboard effect
    effected_audio = board(audio, sr)
    # Export final result
    with AudioFile(stretched_file, "w", sr, effected_audio.shape[0]) as f:
        f.write(effected_audio)
    print(f"Done! Saved final audio to: {stretched_file}")

def get_stretched_path(original_path):
    """Creates a new file path for the stretched audio by adding a suffix."""
    directory, filename = os.path.split(original_path)
    name, ext = os.path.splitext(filename)
    new_filename = f"{name}_stretched{ext}"
    return os.path.join(directory, new_filename)

class VideoRender:
    def __init__(self, task):
        self.task = task
    def process(self):
        item = self.task.subject

        from filer.models.imagemodels import Image as FilerImage
        clips = []
        first = None

        if item.render_type == item.RENDER_TYPE_GRAPHIC_NOVEL:
            # A graphic novel is a document, not a video track. Hand it to the comic renderer
            # rather than returning nothing, which is what made the advertised comic render type
            # produce no output at all.
            from .comic import ComicRender
            return ComicRender(self.task).process()

        for render_item in item.render_items.all():
            clip = None
            if item.render_type == item.RENDER_TYPE_FILM:
                if render_item.video:
                    clip = VideoFileClip(render_item.video.path, audio=True)
                else:
                    self.task.log(f"Render item {render_item.order} skipped: Missing video file for Film render.")

            elif item.render_type == item.RENDER_TYPE_ANIMATIC:
                if render_item.image and render_item.audio:
                    audio_path = render_item.audio.path # Default to original
                    if item.get_conf("audio_speed"):
                        stretched_path = get_stretched_path(render_item.audio.path)
                        speed_audio(render_item.audio.path, stretched_path, speed=item.get_conf("audio_speed"))
                        audio_path = stretched_path # Use the new stretched audio

                    audio_clip = AudioFileClip(audio_path).with_effects([MultiplyVolume(0.9)])

                    # Get margins from 'params' field (space separated "start_ms end_ms")
                    # Fallback to config or settings if params is empty
                    default_margin = item.get_conf("audio_margin") or 0.4
                    start_sec, end_sec = default_margin, default_margin
                    
                    # Total duration = start_margin + audio + end_margin
                    duration = audio_clip.duration + start_sec + end_sec
                    clip = ImageClip(render_item.image.path, duration=duration)
                    
                    # Professional subtle zoom-in effect (Ken Burns)
                    clip = clip.with_audio(audio_clip.with_start(start_sec))
                else:
                    self.task.log(f"Render item {render_item.order} skipped: Animatic requires both image and audio.")

            if clip:
                zoom_effect = Resize(lambda t : 1+0.02*t)
                clip = clip.with_effects([zoom_effect])
                clips.append(clip)

        if clips:
            final_clip = concatenate_videoclips(clips, method="compose")
            name = f"video_{slugify(item.__class__.__name__)}_{slugify(item.name)}_{random.randint(1000,9999)}.mp4"
            filepath_relative = f"exported_videos/{name}"
            filepath_abs = os.path.join( settings.MEDIA_ROOT, filepath_relative)

            final_clip.write_videofile(filepath_abs, fps=24, codec='libx264',
                     audio_codec='aac', temp_audiofile='temp-audio.m4a', remove_temp=True)
            out = FilerImage.objects.create(
                original_filename=name,
                file=filepath_relative,
                name=name
            )
            item.video = out
            item.save()
