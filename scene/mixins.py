import yaml
from django.contrib import admin, messages
from django.template.response import TemplateResponse
from django.utils.html import format_html
from .schemas import AssetsSchema, BackgroundSchema, CharacterSchema, PropSchema, VoiceSchema
from django.utils.translation import gettext_lazy as _
from django.urls import path
from django.conf import settings
from task.models import Task
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.contrib.auth.models import Group, Permission, User
from django.conf import settings
import secrets
import secrets
import string
from django.http import JsonResponse
from django.apps import apps
from .utils import render_image_markup
from django.shortcuts import get_object_or_404

ELEMENT_FIELDSETS = (
        ("Write", {
            "classes": ["tab"],
            "fields": ["name","prompt",  'action'],
        }),
        ("Settings", {
            "classes": ["tab"],
            "fields": ["image","prompt_refine", "story" ],
        }),
    )

ACTION_FIELDSETS = (
        ("Composition", {
            "classes": ["tab"],
            "fields": ["name", "scene", "prompt","order", "actor", "props", "cast", "background", "consistent_with",  "image"],
        }),
        ("Video", {
            "classes": ["tab"],
            "fields": ["prompt_video", "video", "image_first", "image_last"],
        }),
        ("Refine", {
            "classes": ["tab"],
            "fields": ["prompt_refine", "image_refine"],
        }),
        ("Lettering", {
            "classes": ["tab"],
            "fields": ["text", "lettering"],
        }),
        ("Execute On Save", {
            "classes": ["tab"],
            "fields": ["action"],
        }),
    )

class ModelDisplayMixin:
    MAX_IMAGE_HEIGHT = 400

    @property
    def model_name(self):
        """Exposes the model name to templates (avoids underscore access restriction)."""
        return self._meta.model_name

    def _render_image_with_menu(self, field_name, label, max_height=None):
        image = getattr(self, field_name, None)
        url = image.url if image and hasattr(image, 'url') else ""
        h = max_height or self.MAX_IMAGE_HEIGHT
        model_label = f"{self._meta.app_label}.{self._meta.model_name}"
        
        return render_image_markup(url, model_label, self.pk, field_name, h, label)

    def video_download(self):
        video = getattr(self, 'video', None)
        if video:
            return format_html('<a href="{}" download >{}</a>', video.url, _("Download"))
        return _("No Video")
    video_download.short_description = _("Video Download")

    def pic(self):
        return self._render_image_with_menu('image', _("Image"))
    pic.short_description = _("Image")

    def pic_comic(self):
        return self._render_image_with_menu('image_comic', _("Comic Image"))
    pic_comic.short_description = _("Comic Image")
    
    def pic_refine(self):
        return self._render_image_with_menu('image_refine', _("Refined Image"))
    pic_refine.short_description = _("Refined Image")
    
    def pic_first(self):
        return self._render_image_with_menu('image_first', _("First Frame"))
    pic_first.short_description = _("First Frame")
    
    def pic_last(self):
        return self._render_image_with_menu('image_last', _("Last Frame"))
    pic_last.short_description = _("Last Frame")

    def action_pic(self):
        action = getattr(self, 'action', None)
        if action and hasattr(action, 'pic'):
            return action.pic()
        return "-"
    action_pic.short_description = _("Action Image")
    
    def contents_html(self):
        if hasattr(self, 'get_contents') and self.get_contents():
            return format_html('''
        <a class="btn btn-primary" data-toggle="collapse" href="#collapse{}" role="button" aria-expanded="false" aria-controls="collapseExample">
            {}
        </a>
        <div class="collapse" id="collapse{}">
            <div class="card card-body">
                {}
            </div>
        </div>
        {}
        ''', self.id, _("Get Prompt"), self.id, self.get_contents(), self.features() if hasattr(self, 'features') else "")
        return _("No contents")
    contents_html.short_description = _("Contents")
    
    def contents_refine_html(self):
        if hasattr(self, 'get_contents') and hasattr(self, 'PRESET_REFINE') and self.get_contents(generate_self=True, preset=self.PRESET_REFINE):
            return format_html('''
        <a class="btn btn-primary" data-toggle="collapse" href="#collapse{}" role="button" aria-expanded="false" aria-controls="collapseExample">
            {}
        </a>
        <div class="collapse" id="collapse{}">
            <div class="card card-body">
                {}
            </div>
        </div>
        ''', self.id, _("Get Prompt"), self.id, self.get_contents(generate_self=True, preset=self.PRESET_REFINE))
        return _("No contents")
    contents_refine_html.short_description = _("Refined Contents")

    def video_player(self):
        video = getattr(self, 'video', None)
        if video:
            return format_html('''
        <video controls class="rounded-md shadow-sm">
            <source src="{}" type="video/mp4">
        </video>
        ''', video.url)
        return _("No contents")
    video_player.short_description = _("Video Player")
    
    def voice_player(self):
        audio_voice = getattr(self, 'audio_voice', None)
        if audio_voice:
            uid = secrets.token_hex(4)
            audio_id = f"audio_{self.pk}_{uid}"
            return format_html(
                '<div class="flex items-center justify-center">'
                '<audio id="{0}" src="{1}" preload="none" onended="this.nextElementSibling.querySelector(\'span\').textContent=\'play_circle\'"></audio>'
                '<button type="button" class="p-0 border-none bg-transparent cursor-pointer text-primary-600 hover:text-primary-500 transition-all flex items-center justify-center active:scale-95"'
                ' onclick="const a=document.getElementById(\'{0}\'); if(a.paused){{ a.play(); this.querySelector(\'span\').textContent=\'pause_circle\'; }}else{{ a.pause(); this.querySelector(\'span\').textContent=\'play_circle\'; }}">'
                '<span class="material-symbols-outlined text-[32px]">play_circle</span>'
                '</button>'
                '</div>',
                audio_id, audio_voice.url
            )
        return _("No contents")
    voice_player.short_description = _("Play")

class SceneFilterMixin:
    # anything that has a scene foreign key can use this mixin to filter by the user's current scene

    def save_model(self, request, obj, form, change):
        if hasattr(self, 'scene') and obj.scene is None and request.user.story_profile.scene:
            obj.scene = request.user.story_profile.scene
        save_obj = super().save_model(request, obj, form, change)
        return save_obj

    def get_queryset(self, request):
        qs = super().get_queryset(request) #call original queryset method that you are overriding
        if request.user.story_profile.enable_filters:
            if request.user.story_profile.scene:
                return qs.filter(scene=request.user.story_profile.scene)
            return qs.filter(scene__story=request.user.story_profile.get_current_story())
        return qs

class StoryFilterMixin:
    # anything that has a story foreign key can use this mixin to filter by the user's current scene
    
    def save_model(self, request, obj, form, change):
        if not change:
            story = request.user.story_profile.get_current_story()
            if story and hasattr(obj, 'story') and getattr(obj, 'story') is None:
                obj.story = story
        super().save_model(request, obj, form, change)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        profile = getattr(request.user, 'story_profile', None)
        
            
        story = profile.get_current_story()
        if not story:
            return qs

        model_name = self.model._meta.model_name
        if model_name == 'story':
            return qs.filter(pk=story.pk)
        
        field_names = [f.name for f in self.model._meta.get_fields()]
        if 'story' in field_names:
            return qs.filter(story=story)
        elif 'scene' in field_names:
            return qs.filter(scene__story=story)
            
        return qs

class StaffReadOnlyMixin:
    def get_readonly_fields(self, request, obj=None):
        readonly_fields = list(super().get_readonly_fields(request, obj))
        if not request.user.is_superuser:
            readonly_fields.extend(self.staff_readonly_fields)         
        return readonly_fields

class ViewYourOwnMixin:
    def get_queryset(self, request):
        qs = super().get_queryset(request) #call original queryset method that you are overriding
        if not request.user.is_superuser:
            return qs.filter(user=request.user)
        return qs

class EmailSenderMixin: 
    
    def send_email(self, subject,  context, recipient_list):
        html_message = render_to_string(self.email_template, context)
        plain_message = strip_tags(html_message)
        send_mail(
            subject,
            plain_message,
            settings.DEFAULT_FROM_EMAIL,
            recipient_list,
            html_message=html_message
        )

class UserCreatorMixin:
    
    def create_user(self, obj, email):
        username = email.split('@')[0]
        # 1. Generate a secure random string
        alphabet = string.ascii_letters + string.digits
        password = ''.join(secrets.choice(alphabet) for i in range(12))
        user, created = User.objects.get_or_create(username=username, email=email)
        user.set_password(password)
        user.is_staff = True
        group = Group.objects.get(name='faf')
        user.groups.add(group)
        user.save()
        # Render HTML and create plain text alternative
        html_message = render_to_string(
            'email/invitation.html', 
            {'user': user, 
                'obj': obj,
                'password': password, 
                'cta': settings.SITE_URL + f'/admin/scene/story/?id__exact={obj.id}'
            }
        )
        plain_message = strip_tags(html_message)
        send_mail(
            f'Invitation to co-author: {obj.name}', plain_message, settings.DEFAULT_FROM_EMAIL, [email],
            html_message=html_message # <--- HTML added here
        )
        return user

class PromptPreviewMixin:
    """Mixin to provide an endpoint for previewing prompts based on presets."""
    def get_urls(self):
        return [
            path(
                'prompt-preview/<int:object_id>/',
                self.admin_site.admin_view(self.prompt_preview_view),
                name='prompt_preview',
            ),
        ] + super().get_urls()

    def prompt_preview_view(self, request, object_id):
        obj = get_object_or_404(self.model, pk=object_id)
        preset = request.GET.get('preset') or None
        contents = obj.get_contents(preset=preset)
        
        if isinstance(contents, list):
            # Join string parts with double newlines for readability
            text = "\n\n".join([str(p) for p in contents if isinstance(p, (str, bytes))])
        elif isinstance(contents, dict):
            text = contents.get('prompt', '')
        else:
            text = str(contents)

        return JsonResponse({"content": text})

class AdminActionsMixin:
    @admin.action(description="Add to comic video")
    def comic_to_video(self, request, queryset):
        Render = apps.get_model('scene', 'Render')
        RenderItem = apps.get_model('scene', 'RenderItem')
        for obj in queryset:
            render = Render.get_from_scene(obj.scene)
            RenderItem.objects.create(
                image= obj.image_comic if obj.image_comic else obj.image,
                render=render,
                order=obj.order,
            )

    @admin.action(description="Add to scene video")
    def video_to_scene_video(self, request, queryset):
        Render = apps.get_model('scene', 'Render')
        RenderItem = apps.get_model('scene', 'RenderItem')
        for obj in queryset:
            render = Render.get_from_scene(obj.scene)
            RenderItem.objects.create(
                video= obj.video,
                render=render,
                order=obj.order,
            )

    @admin.action(description="Clone selected items")
    def clone(self, request, queryset):
        for obj in queryset:
            props = None
            cast = None
            if hasattr(obj, 'props'):
                props = list(obj.props.all())
            if hasattr(obj, 'cast'):
                cast = list(obj.cast.all())
            obj.pk = None
            if hasattr(obj, 'name') and obj.name:
                obj.name = f"{obj.name} (Clone)"
            if hasattr(obj, 'order'):
                obj.order = obj.order + 1
            obj.save()
            if props is not None:
                obj.props.set(props)
            if cast is not None:
                obj.cast.set(cast)
        self.message_user(request, "Selected items have been cloned.")

    def _queue_generate_image(self, request, queryset):
        # The cumulative cap is checked HERE, at the single point both generate actions pass
        # through, rather than in each action. A guard placed on the callers is a guard that
        # the next caller forgets; this one cannot be walked around by adding an action.
        if self._refuse_over_spend_cap(request, queryset.count()):
            return
        for obj in queryset:
            if Task.createTaskIfQueueEnabled( obj, settings.TASK_TYPE_GENERATE_IMAGE, owner=request.user) is None:
                obj.generate_image(user=request.user)

    def _refuse_over_spend_cap(self, request, n_pending):
        """True when the budget says no. Tells the user why, in the same breath."""
        from scene.spend import cap_block_reason

        reason = cap_block_reason(n_pending)
        if not reason:
            return False
        self.message_user(request, reason, level=messages.ERROR)
        return True

    # Above this many generations in one action, confirm even when nothing would be overwritten.
    # The first version of this guard only asked when existing art was at risk, which made it an
    # OVERWRITE guard wearing a spend guard's name: selecting 500 image-less rows spent ~$75
    # without a single prompt. Losing money you did not mean to spend does not require the rows to
    # have been filled in already.
    CONFIRM_GENERATE_OVER = 10

    @staticmethod
    def _spend_estimate(count):
        """A money figure, when the deployment has told us what a generation costs.

        Deliberately absent rather than guessed when unset: a wrong number on a confirmation
        screen is worse than no number, because people act on it. Absent too when the configured
        rate is unusable - settings can be overridden per deployment, and a rate that arrives as a
        string formats with :.2f by raising. Raising HERE would be merely ugly; it used to raise
        AFTER the batch was queued, so the operator saw a 500 with the money already spent.
        """
        raw = getattr(settings, "IMAGE_GENERATION_COST", None)
        if raw is None:
            return ""
        try:
            rate = float(raw)
        except (TypeError, ValueError):
            return ""
        if rate <= 0:
            return ""
        return f" (about ${rate * count:.2f})"

    @classmethod
    def _needs_generate_confirmation(cls, n_overwrite, total):
        """Confirm when existing art would be destroyed OR when the batch is simply large.

        Both are ways to lose something you cannot get back. Kept as a named predicate
        rather than inline so it can be tested directly: a test that restates the
        condition tests the restatement, not the guard.
        """
        return bool(n_overwrite) or total > cls.CONFIRM_GENERATE_OVER

    CONFIRM_NONCE_KEY = "_generate_confirm_nonces"

    def _confirmation_token(self, request, pks):
        """A ONE-SHOT token over this exact batch, so a confirmation cannot be replayed.

        Without it, a confirmed POST is an ordinary form submission: a browser refresh, a
        "resend", or a replayed payload fires the whole batch again with no prompt.

        A signature ALONE is not enough, and it is worth being precise about why: a signature is
        stateless, so it verifies every time it is presented. It proves "this batch was offered to
        this user", never "and has not been acted on". The one-shot property has to come from
        server state, so the signed payload carries a nonce that is spent from the session on use.
        """
        from django.core import signing

        nonce = secrets.token_urlsafe(16)
        pending = request.session.get(self.CONFIRM_NONCE_KEY, [])
        pending = (pending + [nonce])[-20:]      # bounded: a session must not grow without limit
        request.session[self.CONFIRM_NONCE_KEY] = pending
        request.session.modified = True
        return signing.dumps(
            {"pks": sorted(str(p) for p in pks), "user": request.user.pk, "nonce": nonce},
            salt="scene.generate.confirm")

    def _confirmation_is_valid(self, request, pks):
        """True exactly once per issued token, and only for the batch it was issued for."""
        from django.core import signing

        token = request.POST.get("confirm_token") or ""
        try:
            data = signing.loads(token, salt="scene.generate.confirm", max_age=600)
        except signing.BadSignature:
            return False
        if data.get("user") != request.user.pk:
            return False
        if data.get("pks") != sorted(str(p) for p in pks):
            return False
        pending = request.session.get(self.CONFIRM_NONCE_KEY, [])
        if data.get("nonce") not in pending:
            return False                          # already spent, or issued to another session
        pending.remove(data["nonce"])
        request.session[self.CONFIRM_NONCE_KEY] = pending
        request.session.modified = True
        return True

    @admin.action(description="Generate image (only the missing ones)")
    def generate_missing_images(self, request, queryset):
        """Generate only for rows that have no image yet.

        The common case this exists for: a 40-panel scene where 3 panels need art. Selecting the
        scene and hitting "Generate image" spends on all 40 AND replaces 37 approved plates, with
        no undo.
        """
        missing = queryset.filter(image__isnull=True)
        count = missing.count()
        skipped = queryset.count() - count
        if not count:
            self.message_user(
                request, f"Nothing to do: all {skipped} selected item(s) already have an image.",
                level=messages.INFO)
            return
        self._queue_generate_image(request, missing)
        self.message_user(
            request,
            f"Queued {count} image(s){self._spend_estimate(count)}. "
            f"Left {skipped} existing image(s) untouched.",
            level=messages.SUCCESS)

    @admin.action(description="Generate image")
    def default_generate_image(self, request, queryset):
        """Generate for every selected row, confirming first if that would destroy existing art.

        `generate_image` overwrites in place and the previous filer row is not reachable from the
        admin afterwards, so an accidental bulk generate is unrecoverable work as well as
        unrecoverable money. The interstitial only appears when something would actually be
        overwritten, so the ordinary "generate a fresh batch" path is unchanged.
        """
        overwrite = queryset.filter(image__isnull=False)
        n_overwrite = overwrite.count()
        total = queryset.count()
        pks = list(queryset.values_list("pk", flat=True))

        needs_confirmation = self._needs_generate_confirmation(n_overwrite, total)
        if needs_confirmation and not self._confirmation_is_valid(request, pks):
            if request.POST.get("confirm_overwrite") == "yes":
                # A confirmation was offered but its token was missing, altered, expired, or was
                # issued for a different selection: a replay. Re-ask rather than spend.
                self.message_user(
                    request,
                    "That confirmation could not be used again. Check the batch and confirm it "
                    "once more.",
                    level=messages.WARNING)
            return TemplateResponse(request, "admin/confirm_generate_overwrite.html", {
                **self.admin_site.each_context(request),
                "title": "Overwrite existing images?" if n_overwrite else "Generate images?",
                "queryset": queryset,
                "overwrite": overwrite,
                "n_overwrite": n_overwrite,
                "n_total": total,
                "spend_total": self._spend_estimate(total),
                "confirm_token": self._confirmation_token(request, pks),
                "action_name": "default_generate_image",
                "opts": self.model._meta,
                "media": self.media,
            })
        # Compute the money figure BEFORE spending: if the configured rate is unusable this must
        # not be the line that raises, with the batch already queued behind it.
        estimate = self._spend_estimate(total)
        self._queue_generate_image(request, queryset)
        self.message_user(
            request,
            f"Queued {total} image(s){estimate}"
            + (f", replacing {n_overwrite} existing image(s)." if n_overwrite else "."),
            level=messages.SUCCESS)

    @admin.action(description="Refine image")
    def default_refine_image(self, request, queryset):
        # Refine is the OTHER paid image path: it calls the same image agent and is billed the
        # same way, so a cap that only covered `generate` would be a budget with a door in it.
        if self._refuse_over_spend_cap(request, queryset.count()):
            return
        for obj in queryset:
            if Task.createTaskIfQueueEnabled( obj, settings.TASK_TYPE_REFINE_IMAGE, owner=request.user) is None:
                obj.refine_image(user=request.user) 
            self.message_user(request, "Image generated for item ID {}.".format(obj.id))

    @admin.action(description="Refined as image")
    def accept_refined_image(self, request, queryset):
        for obj in queryset:
            obj.image=obj.image_refine
            obj.save()
            self.message_user(request, "image accepted for item ID {}.".format(obj.id))

    @admin.action(description="Refined as first frame")
    def accept_refined_first(self, request, queryset):
        for obj in queryset:
            obj.image_first=obj.image_refine
            obj.save()
            self.message_user(request, "image accepted for item ID {}.".format(obj.id))

    @admin.action(description="Refined as last frame")
    def accept_refined_last(self, request, queryset):
        for obj in queryset:
            obj.image_last=obj.image_refine
            obj.save()
            self.message_user(request, "image accepted for item ID {}.".format(obj.id))

    @admin.action(description="Video from image" )
    def generate_video(self, request, queryset):
        for obj in queryset:
            if Task.createTaskIfQueueEnabled( obj, settings.TASK_TYPE_GENERATE_VIDEO, owner=request.user) is None:
                obj.generate_video(obj.PRESET_VIDEO, user=request.user)
            self.message_user(request, "video generated for item ID {}.".format(obj.id))

    @admin.action(description="Comic from image" )
    def generate_comic(self, request, queryset):
        for obj in queryset:
            if Task.createTaskIfQueueEnabled( obj, settings.TASK_TYPE_GENERATE_COMIC, owner=request.user) is None:
                obj.generate_comic(user=request.user)
            self.message_user(request, "comic generated for item ID {}.".format(obj.id))

    @admin.action(description="Letter (composite text onto art — free, keeps the art)")
    def letter_action(self, request, queryset):
        lettered = cleared = failed = queued = 0
        for obj in queryset:
            try:
                if Task.createTaskIfQueueEnabled(obj, settings.TASK_TYPE_LETTER_ACTION, owner=request.user) is None:
                    out = obj.letter(user=request.user)
                    lettered += 1 if out else 0
                    cleared += 0 if out else 1
                else:
                    queued += 1
            except Exception as e:
                failed += 1
                self.message_user(request, f"Action {obj.id} ({obj.name}): {e}", level=messages.ERROR)
        # One summary line rather than one message per panel: a scene-sized selection would
        # otherwise bury a real failure under forty successes. Queued work is counted separately
        # and never reported as done — the task has not run yet, and saying "lettered 40" when
        # all forty are still pending (and may all fail) is worse than saying nothing.
        parts = []
        if queued:
            parts.append(f"Queued {queued} panel(s) for lettering; see each panel's task status.")
        if lettered:
            parts.append(f"Lettered {lettered} panel(s).")
        if cleared:
            parts.append(f"{cleared} had no lettering, so any stale composite was cleared.")
        if failed:
            parts.append(f"{failed} failed — see the errors above.")
        self.message_user(request, " ".join(parts) or "Nothing to letter.",
                          level=messages.WARNING if failed else messages.INFO)

    @admin.action(description="Video from first to last")
    def generate_video_first_last(self, request, queryset):
        for obj in queryset:
            if Task.createTaskIfQueueEnabled( obj, settings.TASK_TYPE_GENERATE_VIDEO_FIRST_LAST, owner=request.user) is None:
                obj.generate_video(obj.PRESET_VIDEO_FIRST_LAST, user=request.user)
            self.message_user(request, "video generated for item ID {}.".format(obj.id))

    @admin.action(description="Omni Video")
    def generate_omni_video(self, request, queryset):
        for obj in queryset:
            if Task.createTaskIfQueueEnabled( obj, settings.TASK_TYPE_GENERATE_OMNI_VIDEO, owner=request.user) is None:
                obj.generate_omni_video(obj.PRESET_OMNI_VIDEO, user=request.user)
            self.message_user(request, "omni video generated for item ID {}.".format(obj.id))

    @admin.action(description="Generate Voice")
    def generate_voice(self, request, queryset):
        for obj in queryset:
            if Task.createTaskIfQueueEnabled( obj, settings.TASK_TYPE_GENERATE_VOICE, owner=request.user) is None:
                obj.generate_voice(obj.PRESET_VOICE, user=request.user)
            self.message_user(request, "voice generated for item ID {}.".format(obj.id))

    @admin.action(description="Generate Elements")
    def generate_scene_elements(self, request, queryset):
        for obj in queryset:
            if Task.createTaskIfQueueEnabled(obj, settings.TASK_TYPE_GENERATE_SCENE_ELEMENTS, owner=request.user) is None:
                pass
            model_label = obj._meta.verbose_name
            self.message_user(request, "Generation task for elements started for {}: {}.".format(model_label, obj.name))

    @admin.action(description="Generate Shots")
    def generate_scene_actions(self, request, queryset):
        for obj in queryset:
            if Task.createTaskIfQueueEnabled(obj, settings.TASK_TYPE_GENERATE_SCENE_ACTIONS, owner=request.user) is None:
                pass
            self.message_user(request, "Generation task for actions started for scene: {}.".format(obj.name))

    @admin.action(description="Generate Voices")
    def generate_scene_voices(self, request, queryset):
        for obj in queryset:
            if Task.createTaskIfQueueEnabled(obj, settings.TASK_TYPE_GENERATE_SCENE_VOICES, owner=request.user) is None:
                # This block would run if queuing is disabled.
                # You could add direct execution here if needed.
                pass
            self.message_user(request, "Generation task for voices started for scene: {}.".format(obj.name))

    @admin.action(description="Generate Comics")
    def generate_scene_comics(self, request, queryset):
        for obj in queryset:
            if Task.createTaskIfQueueEnabled(obj, settings.TASK_TYPE_GENERATE_SCENE_COMICS, owner=request.user) is None:
                pass
            self.message_user(request, "Generation task for comics started for scene: {}.".format(obj.name))

    @admin.action(description="Add me as author")
    def add_me_as_author(self, request, queryset):
        for obj in queryset:
            if obj.add_author(request.user):
                self.message_user(request, f"You have been added as an author to story {obj.name}")

    @admin.action(description="Sync Structure" )
    def extract_scene(self, request, queryset):
        for obj in queryset:
            if Task.createTaskIfQueueEnabled( obj, settings.TASK_TYPE_EXTRACT_SCENE, owner=request.user) is None:
                obj.generate_scene(user=request.user)
            self.message_user(request, f"Extracting scene from contribution {obj.id} in story {obj.story.id}.")

    @admin.action(description="Generate Render Preview (step 3.5)")
    def generate_render(self, request, queryset):
        for obj in queryset:
            if hasattr(obj, 'generate_render'):
                obj.generate_render()
                self.message_user(request, _("Render generated for : {}").format(obj.name))

    @admin.action(description="Refresh Render (step 4)" )
    def refresh_render(self, request, queryset):
        for obj in queryset:
            render = obj.generate_render()
            Task.createTaskIfQueueEnabled(
                subject=render,
                task_type=settings.TASK_TYPE_VIDEO_RENDER,
                thr=obj,
                owner=request.user
            )
            self.message_user(request, _("Render generated and video task queued for: {}").format(obj.name))

class RenderTypeMixin:
    RENDER_TYPE_FILM = 'film'
    RENDER_TYPE_GRAPHIC_NOVEL = 'comic'
    RENDER_TYPE_ANIMATIC = 'animatic'

    RENDER_TYPE_CHOICES = [
        (RENDER_TYPE_FILM, 'Film'),
        (RENDER_TYPE_GRAPHIC_NOVEL, 'Graphic Novel'),
        (RENDER_TYPE_ANIMATIC, 'Animatic'),
    ]
    def __getattr__(self, name):
        if name == "is_comic":
            return getattr(self, "render_type", None) == getattr(self, "RENDER_TYPE_GRAPHIC_NOVEL", "comic")
        if name == "is_film":
            return getattr(self, "render_type", None) == getattr(self, "RENDER_TYPE_FILM", "film")
        if name == "is_animatic":
            return getattr(self, "render_type", None) == getattr(self, "RENDER_TYPE_ANIMATIC", "animatic")
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")


class YAMLAssetsMixin:
    def get_elements_as_yaml(self):
        """
        Serializes the model's main elements (locations, characters, props, voices)
        into a YAML formatted string.
        """
        locations = [BackgroundSchema(name=b.name, prompt=b.prompt) for b in self.get_locations()]
        characters = [CharacterSchema(name=c.name, prompt=c.prompt) for c in self.get_cast()]
        props = [PropSchema(name=p.name, prompt=p.prompt) for p in self.get_props()]
        voices = [
            VoiceSchema(
                name=v.name,
                prompt=v.prompt,
                google_voice=v.google_voice.name if v.google_voice else None
            ) for v in self.get_voices()
        ]

        elements_data = AssetsSchema(
            locations=locations,
            characters=characters,
            props=props,
            voices=voices,
        ).model_dump()

        filtered_data = {k: v for k, v in elements_data.items() if v}
        if not filtered_data:
            return None

        context_key = f"{self._meta.model_name}_context"
        return yaml.dump({context_key: filtered_data}, indent=2, default_flow_style=False)

class ChangelistScrollToEditedMixin:
    """
    Mixin that appends the `#id={obj.id}` anchor hash to the redirect URL 
    after adding or changing an object, so the changelist can scroll back 
    to the edited row.
    """
    def response_add(self, request, obj, post_url_continue=None):
        res = super().response_add(request, obj, post_url_continue)
        if res.status_code in [301, 302] and '_continue' not in request.POST and '_addanother' not in request.POST:
            res['Location'] = f"{res['Location']}#id={obj.id}"
        return res

    def response_change(self, request, obj):
        res = super().response_change(request, obj)
        if res.status_code in [301, 302] and '_continue' not in request.POST and '_addanother' not in request.POST:
            res['Location'] = f"{res['Location']}#id={obj.id}"
        return res
