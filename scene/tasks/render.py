from moviepy import ImageClip, VideoFileClip, AudioFileClip, concatenate_videoclips
from moviepy.video.fx import Resize
from audiostretchy.stretch import stretch_audio
from task.models import Task
from agent.models import GetContentsMixin
from django.utils.text import slugify
from django.conf import settings
from django.core.files.base import ContentFile
from moviepy.audio.fx import MultiplyVolume
import random
import os

class VideoRender:
    def __init__(self, task):
        self.task = task
    def process(self):
        item = self.task.subject

        from filer.models.imagemodels import Image as FilerImage
        clips = []
        first = None

        if item.render_type == item.RENDER_TYPE_GRAPHIC_NOVEL:
            return

        for render_item in item.render_items.all():
            clip = None
            if item.render_type == item.RENDER_TYPE_FILM:
                if render_item.video:
                    clip = VideoFileClip(render_item.video.path, audio=True)
                else:
                    self.task.log(f"Render item {render_item.order} skipped: Missing video file for Film render.")

            elif item.render_type == item.RENDER_TYPE_ANIMATIC:
                if render_item.image and render_item.audio:
                    if item.story.conf and "audiostrech" in item.story.conf:
                        stretch_audio("input_audio.wav", "stretched_audio.wav", ratio=1.5)

                    audio_clip = AudioFileClip(render_item.audio.path).with_effects([MultiplyVolume(0.9)])

                    # Get margins from 'params' field (space separated "start_ms end_ms")
                    # Fallback to config or settings if params is empty
                    default_margin = (render_item.config or {}).get('audio_margin', getattr(settings, 'DEFAULT_AUDIO_MARGIN', 0.5))
                    start_ms, end_ms = default_margin, default_margin
                    
                    if render_item.params:
                        try:
                            parts = render_item.params.split()
                            if len(parts) >= 1:
                                # Convention: 10 = 1s, 3 = 0.3s (multiply by 100 for ms)
                                start_ms = float(parts[0]) * 100
                                end_ms = start_ms 
                            if len(parts) >= 2:
                                end_ms = float(parts[1]) * 100
                        except (ValueError, IndexError):
                            pass

                    start_sec, end_sec = start_ms / 1000.0, end_ms / 1000.0

                    # Total duration = start_margin + audio + end_margin
                    duration = audio_clip.duration + start_sec + end_sec
                    clip = ImageClip(render_item.image.path, duration=duration)
                    
                    # Professional subtle zoom-in effect (Ken Burns)
                    clip = clip.with_effects([Resize(lambda t: 1.0 + 0.1 * (t / duration))])
                    clip = clip.with_audio(audio_clip.with_start(start_sec))
                else:
                    self.task.log(f"Render item {render_item.order} skipped: Animatic requires both image and audio.")

            if clip:
                if first:
                    clip = clip.with_effects([Resize(width=first.w)])
                if not first:
                    first = clip
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
