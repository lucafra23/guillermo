from moviepy import ImageClip, VideoFileClip, AudioFileClip, concatenate_videoclips
from moviepy.video.fx import Resize
from task.models import Task
from agent.models import GetContentsMixin
from django.utils.text import slugify
from django.conf import settings
from django.core.files.base import ContentFile
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
                    audio_clip = AudioFileClip(render_item.audio.path)

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


class VideoRender2:
    def __init__(self, task):
        self.task = task

    def process(self):
        item = self.task.subject

        from filer.models.imagemodels import Image as FilerImage
        from scene.models import Action
        clips = []
        first = None

        if item.render_type == item.RENDER_TYPE_GRAPHIC_NOVEL:
            return

        for render_item in item.render_items.all().order_by('order'):
            clip = None
            action = render_item.action

            if not action:
                self.task.log(f"Render item {render_item.order} skipped: no linked action.")
                continue

            shot_type = action.shot_type or Action.SHOT_TYPE_SILENT
            params = action.parameters or {}

            # --- helpers ---
            def get_param(key, default):
                return params.get(key, default)

            # ----------------------------------------------------------------
            # VOICE: static image + audio_voice with buffer margins
            # ----------------------------------------------------------------
            if shot_type == Action.SHOT_TYPE_VOICE:
                image_src = render_item.image or action.image
                audio_src = render_item.audio or action.audio_voice

                if not image_src or not audio_src:
                    self.task.log(
                        f"Render item {render_item.order} [{shot_type}] skipped: "
                        f"requires image and audio_voice."
                    )
                    continue

                buffer_in  = float(get_param('buffer_in',  0.2))
                buffer_out = float(get_param('buffer_out', 0.3))

                audio_clip = AudioFileClip(audio_src.path)
                duration   = audio_clip.duration + buffer_in + buffer_out

                clip = ImageClip(image_src.path, duration=duration)
                clip = clip.with_effects([Resize(lambda t: 1.0 + 0.05 * (t / duration))])
                clip = clip.with_audio(audio_clip.with_start(buffer_in))

            # ----------------------------------------------------------------
            # VIDEO: play action.video once, mix in voice audio if available
            # ----------------------------------------------------------------
            elif shot_type == Action.SHOT_TYPE_VIDEO:
                video_src = action.video

                if not video_src:
                    self.task.log(
                        f"Render item {render_item.order} [{shot_type}] skipped: "
                        f"action has no video file."
                    )
                    continue

                clip = VideoFileClip(video_src.path, audio=True)

                # mix in voice audio (replaces the original video track)
                audio_src = action.audio_voice
                if audio_src:
                    buffer_in  = float(get_param('buffer_in', 0.0))
                    audio_clip = AudioFileClip(audio_src.path)
                    clip = clip.with_audio(audio_clip.with_start(buffer_in))

            # ----------------------------------------------------------------
            # VIDEO_LOOP: action.video repeated N times, voice audio mixed in
            # ----------------------------------------------------------------
            elif shot_type == Action.SHOT_TYPE_VIDEO_LOOP:
                video_src = action.video

                if not video_src:
                    self.task.log(
                        f"Render item {render_item.order} [{shot_type}] skipped: "
                        f"action has no video file to loop."
                    )
                    continue

                iterations = max(int(get_param('iterations', 1)), 1)
                base_clip  = VideoFileClip(video_src.path, audio=False)

                # repeat the clip iterations times by concatenating copies
                clip = concatenate_videoclips([base_clip] * iterations)

                # mix in voice audio track over the full looped duration
                audio_src = action.audio_voice
                if audio_src:
                    buffer_in  = float(get_param('buffer_in', 0.0))
                    audio_clip = AudioFileClip(audio_src.path)
                    clip = clip.with_audio(audio_clip.with_start(buffer_in))

            # ----------------------------------------------------------------
            # COMIC / SILENT: static image for a fixed duration
            # ----------------------------------------------------------------
            else:
                image_src = render_item.image or action.image

                if not image_src:
                    self.task.log(
                        f"Render item {render_item.order} [{shot_type}] skipped: "
                        f"no image."
                    )
                    continue

                duration = float(get_param('duration', render_item.duration))
                clip = ImageClip(image_src.path, duration=duration)
                clip = clip.with_effects([Resize(lambda t: 1.0 + 0.05 * (t / duration))])

            # ----------------------------------------------------------------
            # Normalise width to match the first clip
            # ----------------------------------------------------------------
            if clip:
                if first:
                    clip = clip.with_effects([Resize(width=first.w)])
                else:
                    first = clip
                clips.append(clip)

        if clips:
            final_clip = concatenate_videoclips(clips, method="compose")
            name = f"video_{slugify(item.__class__.__name__)}_{slugify(item.name)}_{random.randint(1000,9999)}.mp4"
            filepath_relative = f"exported_videos/{name}"
            filepath_abs = os.path.join(settings.MEDIA_ROOT, filepath_relative)

            final_clip.write_videofile(
                filepath_abs, fps=24, codec='libx264',
                audio_codec='aac', temp_audiofile='temp-audio.m4a', remove_temp=True
            )
            out = FilerImage.objects.create(
                original_filename=name,
                file=filepath_relative,
                name=name
            )
            item.video = out
            item.save()
