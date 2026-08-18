import logging
import os
import requests
from django.core.files.base import ContentFile
from import_export import resources, fields, widgets
from filer.models.imagemodels import Image as FilerImage
from filer.models.filemodels import File as FilerFile
from .models import Story, Scene, Action, Character, Prop, Background, Voice, Style, Theme, StoryGroup

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cross-instance sync philosophy
# ---------------------------------------------------------------------------
# These resources move a whole Story (and its cast/locations/props/scenes/panels)
# between DIFFERENT Guillermo instances, which have independent autoincrement PKs.
# Two hard rules follow:
#   1. Never key rows by raw `id` (that overwrites unrelated rows on the target and
#      poisons its PK space). Every resource matches on a STORY-SCOPED NATURAL KEY.
#   2. Resolve every relation by a stable business key (name / order), scoped to the
#      row's story, so identical names in a different story can't collide.
# ---------------------------------------------------------------------------


class FilerMediaWidget(widgets.ForeignKeyWidget):
    """
    Widget for FilerImageField / FilerFileField.
    Export: renders the absolute media URL.
    Import (fallback path): downloads the file from that URL and creates a Filer
    record. The bundled-file path (offline, cross-instance) is BundledFilerMediaWidget.
    """
    def __init__(self, model=FilerImage, *args, **kwargs):
        super().__init__(model, *args, **kwargs)

    def clean(self, value, row=None, **kwargs):
        if not value or not str(value).startswith('http'):
            return None

        try:
            response = requests.get(value, timeout=30)
            if response.status_code == 200:
                filename = os.path.basename(str(value).split('?')[0])
                file_content = ContentFile(response.content)
                instance = self.model()
                instance.file.save(filename, file_content, save=True)
                instance.original_filename = filename
                instance.name = filename
                instance.save()
                return instance
        except Exception as e:
            print(f"Failed to import media from {value}: {e}")
        return None

    def render(self, value, obj=None):
        if value and hasattr(value, 'url'):
            return value.url
        return ""


class BundledFilerMediaWidget(FilerMediaWidget):
    """Import-time media widget: rebuild the Filer record from a file BUNDLED in the
    sync zip (offline / cross-instance safe). De-dupes by filename so re-imports don't
    pile up duplicate Filer rows, and falls back to the URL download when the file
    isn't present in the bundle."""

    def __init__(self, model=FilerImage, media_index=None, *args, **kwargs):
        self._media_index = media_index or {}
        super().__init__(model, *args, **kwargs)

    def clean(self, value, row=None, **kwargs):
        if not value:
            return None
        basename = os.path.basename(str(value).split('?')[0])

        # Idempotency: reuse an existing Filer with the same original filename instead
        # of creating a fresh one on every import.
        existing = self.model.objects.filter(original_filename=basename).first()
        if existing:
            return existing

        local_path = self._media_index.get(basename)
        if local_path and os.path.exists(local_path):
            try:
                with open(local_path, 'rb') as fh:
                    content = ContentFile(fh.read())
                instance = self.model()
                instance.file.save(basename, content, save=True)
                instance.original_filename = basename
                instance.name = basename
                instance.save()
                return instance
            except Exception as e:
                print(f"Failed to import bundled media {basename}: {e}")
                return None
        # No bundled file -> fall back to the parent (URL download).
        return super().clean(value, row, **kwargs)


class GetOrCreateForeignKeyWidget(widgets.ForeignKeyWidget):
    """Resolve a related row by `field` (e.g. name); create a minimal one if missing.
    Used for Style/Theme so a missing global on the destination does NOT roll back the
    whole Story import."""
    def clean(self, value, row=None, **kwargs):
        if value in (None, ""):
            return None
        obj, _ = self.model.objects.get_or_create(**{self.field: value})
        return obj


class SkipMissingForeignKeyWidget(widgets.ForeignKeyWidget):
    """Resolve by `field`; return None (skip) if not found instead of raising.
    Used for optional relations (group, voice) that we don't want to auto-create."""
    def clean(self, value, row=None, **kwargs):
        if value in (None, ""):
            return None
        return self.model.objects.filter(**{self.field: value}).first()


class StoryScopedForeignKeyWidget(widgets.ForeignKeyWidget):
    """Resolve a related row by `field`, scoped to the story named in row['story'],
    so identical names/orders across different stories can't collide or mis-attach.
    Returns None (rather than raising) when the target isn't found."""
    def __init__(self, model, field='name', story_path='story__name', **kwargs):
        self.story_path = story_path
        super().__init__(model, field=field, **kwargs)

    def clean(self, value, row=None, **kwargs):
        if value in (None, ""):
            return None
        qs = self.model.objects.all()
        story_name = (row or {}).get('story')
        if story_name:
            qs = qs.filter(**{self.story_path: story_name})
        return qs.filter(**{self.field: value}).first()


class ActionRefWidget(widgets.ForeignKeyWidget):
    """`consistent_with` (Action -> Action self-FK). Rendered as 'sceneorder:order'
    so it survives a PK remap, and resolved within the row's story. Tolerant of a
    not-yet-imported target (returns None; a second import pass links forward refs)."""
    def __init__(self, **kwargs):
        super().__init__(Action, field='id', **kwargs)

    # Rendered as "<scene name>::<panel name>", which is the SAME key Meta.import_id_fields
    # uses. It used to be "<scene.order>:<panel.order>", and that key does not identify a
    # panel: measured on this book, 604 panels produce only 530 distinct (scene.order,
    # order) pairs -- 61 keys shared by 135 panels -- so `.first()` picked an arbitrary one
    # of them. 187 panels carry a consistent_with reference, and a mislinked one is silent:
    # the import reports success and the panel is simply drawn against the wrong character.
    # The same names give 604 distinct keys, zero collisions.
    SEP = "::"
    ESC = "\\"

    @classmethod
    def _escape(cls, text):
        """Make one name safe to sit next to the separator.

        Names are free text. Without this, a panel called "a::b" renders to
        "M00::a::b", which splits back into scene "M00::a" and panel "b", resolves to
        nothing, and drops the link in silence -- the exact defect this key was changed
        to fix, returning through the delimiter itself.
        """
        return (text or "").replace(cls.ESC, cls.ESC * 2).replace(cls.SEP, cls.ESC + cls.SEP)

    @classmethod
    def _split(cls, value):
        """Split on the last UNESCAPED separator. Returns (scene_name, panel_name) or None."""
        out, buf, parts = [], [], []
        i = 0
        while i < len(value):
            ch = value[i]
            if ch == cls.ESC and i + 1 < len(value):
                buf.append(value[i + 1])          # whatever was escaped, verbatim
                i += 2
                continue
            if value.startswith(cls.SEP, i):
                parts.append("".join(buf))
                buf = []
                i += len(cls.SEP)
                continue
            buf.append(ch)
            i += 1
        parts.append("".join(buf))
        if len(parts) < 2:
            return None
        # Everything before the final separator is the scene: a scene name may legitimately
        # contain one, and the panel name is the last field.
        return cls.SEP.join(parts[:-1]), parts[-1]

    def render(self, value, obj=None):
        if not value:
            return ""
        scene = getattr(value, "scene", None)
        return (f"{self._escape(getattr(scene, 'name', ''))}"
                f"{self.SEP}{self._escape(value.name or '')}")

    def clean(self, value, row=None, **kwargs):
        if not value:
            return None
        value = str(value)
        story_name = (row or {}).get('story')
        qs = Action.objects.all()
        if story_name:
            qs = qs.filter(scene__story__name=story_name)

        if self.SEP in value:
            split = self._split(value)
            if split:
                scene_name, action_name = split
                matches = qs.filter(scene__name=scene_name.strip(), name=action_name.strip())
                # Nothing enforces that (scene name, panel name) is unique -- it is a
                # convention, not a constraint -- so an ambiguous reference is REPORTED
                # rather than resolved by whichever row sorts first.
                if matches.count() > 1:
                    logger.warning(
                        "consistent_with %r matches %d panels; linking the first. Rename them, "
                        "or the link is decided by row order.", value, matches.count())
                return matches.first()

        # Legacy "<scene.order>:<panel.order>" from a zip exported before this change. Kept
        # so an older archive still imports, ambiguity and all -- that is what it recorded.
        if ':' in value:
            scene_order, action_order = value.split(':', 1)
            return qs.filter(scene__order=scene_order, order=action_order).first()
        return None


class StoryResource(resources.ModelResource):
    style = fields.Field(column_name='style', attribute='style',
                         widget=GetOrCreateForeignKeyWidget(Style, 'name'))
    theme = fields.Field(column_name='theme', attribute='theme',
                         widget=GetOrCreateForeignKeyWidget(Theme, 'name'))
    group = fields.Field(column_name='group', attribute='group',
                         widget=SkipMissingForeignKeyWidget(StoryGroup, 'name'))

    class Meta:
        model = Story
        import_id_fields = ('name',)
        fields = ('name', 'order', 'style', 'theme', 'group', 'prompt', 'render_type')
        export_order = fields


class CharacterResource(resources.ModelResource):
    image = fields.Field(column_name='image_url', attribute='image', widget=FilerMediaWidget(FilerImage))
    story = fields.Field(column_name='story', attribute='story', widget=widgets.ForeignKeyWidget(Story, 'name'))
    voice = fields.Field(column_name='voice', attribute='voice', widget=SkipMissingForeignKeyWidget(Voice, 'name'))

    class Meta:
        model = Character
        import_id_fields = ('story', 'name')
        fields = ('story', 'name', 'prompt', 'voice', 'image')
        export_order = fields


class BackgroundResource(resources.ModelResource):
    image = fields.Field(column_name='image_url', attribute='image', widget=FilerMediaWidget(FilerImage))
    image_refine = fields.Field(column_name='image_refine_url', attribute='image_refine', widget=FilerMediaWidget(FilerImage))
    story = fields.Field(column_name='story', attribute='story', widget=widgets.ForeignKeyWidget(Story, 'name'))

    class Meta:
        model = Background
        import_id_fields = ('story', 'name')
        fields = ('story', 'name', 'prompt', 'image', 'image_refine')
        export_order = fields


class PropResource(resources.ModelResource):
    image = fields.Field(column_name='image_url', attribute='image', widget=FilerMediaWidget(FilerImage))
    story = fields.Field(column_name='story', attribute='story', widget=widgets.ForeignKeyWidget(Story, 'name'))

    class Meta:
        model = Prop
        import_id_fields = ('story', 'name')
        fields = ('story', 'name', 'prompt', 'image')
        export_order = fields


class SceneResource(resources.ModelResource):
    story = fields.Field(column_name='story', attribute='story', widget=widgets.ForeignKeyWidget(Story, 'name'))

    class Meta:
        model = Scene
        import_id_fields = ('story', 'order')
        fields = ('story', 'order', 'name', 'prompt')
        export_order = fields


class ActionResource(resources.ModelResource):
    # Export-only helper column: the panel's story, so the story-scoped widgets below
    # can resolve their targets on import. Has no model attribute (never written back).
    story = fields.Field(column_name='story')

    scene = fields.Field(column_name='scene', attribute='scene',
                         widget=StoryScopedForeignKeyWidget(Scene, field='order', story_path='story__name'))
    actor = fields.Field(column_name='actor', attribute='actor',
                         widget=StoryScopedForeignKeyWidget(Character, field='name'))
    background = fields.Field(column_name='background', attribute='background',
                              widget=StoryScopedForeignKeyWidget(Background, field='name'))
    cast = fields.Field(column_name='cast', attribute='cast',
                        widget=widgets.ManyToManyWidget(Character, field='name'))
    props = fields.Field(column_name='props', attribute='props',
                         widget=widgets.ManyToManyWidget(Prop, field='name'))
    consistent_with = fields.Field(column_name='consistent_with', attribute='consistent_with',
                                   widget=ActionRefWidget())
    voice = fields.Field(column_name='voice', attribute='voice',
                         widget=SkipMissingForeignKeyWidget(Voice, 'name'))

    image = fields.Field(column_name='image_url', attribute='image', widget=FilerMediaWidget(FilerImage))
    image_comic = fields.Field(column_name='image_comic_url', attribute='image_comic', widget=FilerMediaWidget(FilerImage))
    image_refine = fields.Field(column_name='image_refine_url', attribute='image_refine', widget=FilerMediaWidget(FilerImage))
    image_first = fields.Field(column_name='image_first_url', attribute='image_first', widget=FilerMediaWidget(FilerImage))
    image_last = fields.Field(column_name='image_last_url', attribute='image_last', widget=FilerMediaWidget(FilerImage))
    video = fields.Field(column_name='video_url', attribute='video', widget=FilerMediaWidget(FilerFile))
    audio_voice = fields.Field(column_name='audio_url', attribute='audio_voice', widget=FilerMediaWidget(FilerFile))

    class Meta:
        model = Action
        # Keyed on (scene, NAME), not (scene, order). `Action.order` is not unique within a scene:
        # nothing enforces it, and on a real 501-panel book 105 (scene, order) pairs were shared by
        # 225 panels. Importing on that key silently collapsed them - the log said
        # "501 rows, errors=False" and the database held 381. A quarter of the book disappeared
        # with no error, which is the worst possible way for an import to fail.
        import_id_fields = ('scene', 'name')
        fields = (
            'story', 'scene', 'order', 'name', 'is_intro', 'prompt', 'text', 'lettering',
            'prompt_comic',
            'actor', 'background', 'cast', 'props', 'consistent_with', 'voice',
            'image', 'image_comic', 'image_refine', 'image_first', 'image_last', 'video', 'audio_voice',
        )
        export_order = fields

    def dehydrate_story(self, obj):
        try:
            return obj.scene.story.name if obj.scene and obj.scene.story else ""
        except Exception:
            return ""
