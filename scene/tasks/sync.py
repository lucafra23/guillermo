from scene.resources import (
    StoryResource, CharacterResource, BackgroundResource,
    PropResource, SceneResource, ActionResource,
    FilerMediaWidget, BundledFilerMediaWidget,
)
from scene.models import Action
from django.utils.text import slugify
from django.conf import settings
from django.core import serializers
from django.core.files.base import ContentFile
from django.db import transaction
import io
import os
import zipfile
import tempfile


class TaskSyncExport:
    def __init__(self, task):
        self.task = task

    def process(self):
        from scene.models import Action
        sync_item = self.task.subject
        sync = sync_item.sync
        story = sync.story
        from filer.models.filemodels import File as FilerFile

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            # 1. Export Data as CSVs
            zip_file.writestr('data/story.csv', StoryResource().export(story.__class__.objects.filter(id=story.id)).csv)
            zip_file.writestr('data/characters.csv', CharacterResource().export(story.characters.all()).csv)
            zip_file.writestr('data/backgrounds.csv', BackgroundResource().export(story.backgrounds.all()).csv)
            zip_file.writestr('data/props.csv', PropResource().export(story.props.all()).csv)
            
            scenes = story.scenes.all().order_by('order')
            zip_file.writestr('data/scenes.csv', SceneResource().export(scenes).csv)

            # Order panels by (scene, order) so consistent_with references (which point
            # at a PRIOR panel) resolve on a single import pass.
            actions = Action.objects.filter(scene__in=scenes).order_by('scene__order', 'order')
            zip_file.writestr('data/actions.csv', ActionResource().export(actions).csv)

            # 2. Helper to add media files
            def add_filer_file(filer_file, folder):
                if filer_file and hasattr(filer_file, 'file') and filer_file.file:
                    try:
                        file_path = filer_file.file.path
                        arcname = os.path.join(folder, os.path.basename(file_path))
                        if arcname not in zip_file.namelist():
                            zip_file.write(file_path, arcname)
                    except Exception:
                        pass

            for char in story.characters.all():
                add_filer_file(char.image, "media/characters")
            for bg in story.backgrounds.all():
                add_filer_file(bg.image, "media/backgrounds")
                add_filer_file(bg.image_refine, "media/backgrounds")
            for prop in story.props.all():
                add_filer_file(prop.image, "media/props")
            for action in actions:
                add_filer_file(action.image, "media/actions")
                add_filer_file(action.image_comic, "media/actions")
                add_filer_file(action.image_refine, "media/actions")
                add_filer_file(action.image_first, "media/actions")
                add_filer_file(action.image_last, "media/actions")
                add_filer_file(action.video, "media/videos")
                add_filer_file(action.audio_voice, "media/audio")

        buffer.seek(0)
        filename = f"export_{slugify(story.name)}_{sync_item.id}.zip"
        
        out_file = FilerFile.objects.create(
            original_filename=filename,
            file=ContentFile(buffer.read(), name=filename),
            name=filename
        )
        sync_item.zip_file = out_file
        sync_item.save()
        
        sync.last_file_out = out_file
        sync.save()

class TaskSyncImport:
    # Extraction guard against zip bombs.
    MAX_EXTRACT_BYTES = 1024 * 1024 * 1024  # 1 GiB

    def __init__(self, task):
        self.task = task

    def _safe_extract(self, zf, dest):
        """Extract with path-traversal and total-size guards (defence in depth on top
        of stdlib sanitisation)."""
        dest_real = os.path.realpath(dest)
        total = 0
        for info in zf.infolist():
            target = os.path.realpath(os.path.join(dest, info.filename))
            if target != dest_real and not target.startswith(dest_real + os.sep):
                raise SyncImportError(f"Unsafe path in zip: {info.filename}")
            total += info.file_size
            if total > self.MAX_EXTRACT_BYTES:
                raise SyncImportError("Refusing to extract: archive exceeds size cap")
        zf.extractall(dest)

    def _backup_story(self, story_name):
        """Snapshot the target story subtree so an admin can restore after a bad import.
        Returns the backup path, or None if there's nothing to back up."""
        from scene.models import Story, Action
        story = Story.objects.filter(name=story_name).first()
        if not story:
            return None
        objs = [story]
        objs += list(story.characters.all())
        objs += list(story.backgrounds.all())
        objs += list(story.props.all())
        scenes = list(story.scenes.all())
        objs += scenes
        objs += list(Action.objects.filter(scene__in=scenes))
        backup_dir = os.path.join(settings.MEDIA_ROOT, 'sync_backups')
        os.makedirs(backup_dir, exist_ok=True)
        path = os.path.join(backup_dir, f"backup_{slugify(story_name)}_{self.task.subject.id}.json")
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(serializers.serialize('json', objs))
        return path

    def process(self):
        from tablib import Dataset

        sync_item = self.task.subject
        sync = sync_item.sync

        if not sync_item.zip_file:
            self.task.log("Missing zip file for import")
            return

        # Track the last imported file.
        sync.last_file_in = sync_item.zip_file
        sync.save()

        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                with zipfile.ZipFile(sync_item.zip_file.file.path, 'r') as zf:
                    self._safe_extract(zf, temp_dir)
            except Exception as e:
                self.task.log(f"Extraction failed: {str(e)}")
                return

            # Index bundled media by basename so the import restores files offline
            # instead of relying on the (possibly unreachable) source URLs.
            media_index = {}
            for root, _dirs, files in os.walk(os.path.join(temp_dir, 'media')):
                for fname in files:
                    media_index.setdefault(fname, os.path.join(root, fname))

            def load(rel_path):
                csv_path = os.path.join(temp_dir, rel_path)
                if not os.path.exists(csv_path):
                    return None
                with open(csv_path, 'r', encoding='utf-8') as fh:
                    return Dataset().load(fh.read(), format='csv')

            # Best-effort backup of any existing story we're about to overwrite.
            story_ds = load('data/story.csv')
            if story_ds and len(story_ds):
                try:
                    name = story_ds['name'][0] if 'name' in story_ds.headers else None
                    if name:
                        backup_path = self._backup_story(name)
                        if backup_path:
                            self.task.log(f"Backed up existing story '{name}' -> {backup_path}")
                except Exception as e:
                    self.task.log(f"Backup skipped: {str(e)}")

            # Import each CSV via its export Resource, in FK-dependency order.
            # NOTE: rows are matched by story-scoped NATURAL KEYS (never raw id), so a
            # re-sync updates our own book in place and cannot clobber unrelated rows.
            plan = [
                ('data/story.csv', StoryResource),
                ('data/characters.csv', CharacterResource),
                ('data/backgrounds.csv', BackgroundResource),
                ('data/props.csv', PropResource),
                ('data/scenes.csv', SceneResource),
                ('data/actions.csv', ActionResource),
            ]

            actions_ds = None
            try:
                # Everything commits together, or not at all.
                with transaction.atomic():
                    for rel_path, ResourceClass in plan:
                        dataset = load(rel_path)
                        if dataset is None:
                            continue
                        if rel_path.endswith('actions.csv'):
                            actions_ds = dataset

                        resource = ResourceClass()
                        # Prefer the bundled media files over the source URL.
                        for field in resource.fields.values():
                            widget = getattr(field, 'widget', None)
                            if isinstance(widget, FilerMediaWidget):
                                field.widget = BundledFilerMediaWidget(
                                    model=widget.model, media_index=media_index
                                )

                        result = resource.import_data(
                            dataset, dry_run=False, raise_errors=False, collect_failed_rows=True
                        )
                        self.task.log(
                            f"Imported {rel_path}: {len(dataset)} rows, errors={result.has_errors()}"
                        )
                        if result.has_errors():
                            try:
                                for line, errors in result.row_errors():
                                    for err in errors:
                                        self.task.log(f"  {rel_path} row {line}: {err.error}")
                            except Exception:
                                pass
                            raise SyncImportError(f"Aborting import: errors in {rel_path}")

                    # Second pass: link any forward consistent_with references whose
                    # target panel didn't exist yet on the first pass.
                    self._link_consistency(actions_ds)

            except SyncImportError as e:
                self.task.log(f"{str(e)} — rolled back, no changes applied.")
                return
            except Exception as e:
                self.task.log(f"Import failed, rolled back: {str(e)}")
                return

        self.task.log("Sync import complete.")

    def _link_consistency(self, actions_ds):
        """Resolve consistent_with for every panel now that all panels exist."""
        if not actions_ds or 'consistent_with' not in actions_ds.headers:
            return
        from scene.models import Action
        widget = ActionResource().fields['consistent_with'].widget
        for row in actions_ds.dict:
            ref = row.get('consistent_with')
            if not ref:
                continue
            target = widget.clean(ref, row=row)
            if not target:
                continue
            story_name = row.get('story')
            qs = Action.objects.all()
            if story_name:
                qs = qs.filter(scene__story__name=story_name)
            panel = qs.filter(scene__order=row.get('scene'), order=row.get('order')).first()
            if panel and panel.consistent_with_id != target.id:
                panel.consistent_with = target
                panel.save(update_fields=['consistent_with'])
