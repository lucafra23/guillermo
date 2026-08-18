"""The sync path has to carry a ~1 GiB book without losing or mislinking any of it.

Each test here corresponds to a defect measured on the real 604-panel book, not to a
hypothetical: the extraction cap sat at 95.6% consumed, the exporter held the whole archive
in memory twice, a media file that failed to be added vanished without a word, and
`consistent_with` was keyed on a pair that identifies 604 panels with only 530 values.
"""
import io
import os
import tempfile
import zipfile
from unittest import mock

from django.test import SimpleTestCase, TestCase

from scene.models import Action, Scene, Story
from scene.resources import ActionResource
from scene.tasks.sync import SyncImportError, TaskSyncImport


def _zip_with(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries:
            zf.writestr(name, data)
    buf.seek(0)
    return zipfile.ZipFile(buf)


class ExtractionGuardTests(SimpleTestCase):

    def setUp(self):
        self.importer = TaskSyncImport(task=mock.Mock())

    def test_the_default_cap_clears_the_real_book(self):
        """0.956 GiB measured today. A cap under that is a scheduled failure."""
        self.assertGreater(self.importer.MAX_EXTRACT_BYTES, int(1.5 * 1024 ** 3))

    def test_an_oversized_archive_is_still_refused(self):
        self.importer.MAX_EXTRACT_BYTES = 10
        with tempfile.TemporaryDirectory() as dest:
            with self.assertRaises(SyncImportError) as ctx:
                self.importer._safe_extract(_zip_with([("a.txt", b"x" * 100)]), dest)
        self.assertIn("Refusing to extract", str(ctx.exception))

    def test_path_traversal_is_still_refused(self):
        with tempfile.TemporaryDirectory() as dest:
            with self.assertRaises(SyncImportError) as ctx:
                self.importer._safe_extract(_zip_with([("../../evil.txt", b"x")]), dest)
        self.assertIn("Unsafe path", str(ctx.exception))

    def test_it_refuses_rather_than_filling_the_volume(self):
        """The cap bounds what we will write; this bounds what the disk can take."""
        self.importer.MIN_FREE_BYTES_AFTER = 1 << 62      # nothing could ever satisfy this
        with tempfile.TemporaryDirectory() as dest:
            with self.assertRaises(SyncImportError) as ctx:
                self.importer._safe_extract(_zip_with([("a.txt", b"x" * 10)]), dest)
        self.assertIn("free on the destination volume", str(ctx.exception))

    def test_a_sane_archive_extracts(self):
        with tempfile.TemporaryDirectory() as dest:
            self.importer._safe_extract(_zip_with([("data/story.csv", b"name\nBook\n")]), dest)
            self.assertTrue(os.path.exists(os.path.join(dest, "data", "story.csv")))


class ConsistentWithKeyTests(TestCase):
    """604 panels, 530 distinct (scene.order, order) pairs. The key has to be the name."""

    @classmethod
    def setUpTestData(cls):
        cls.story = Story.objects.create(name="Book")
        cls.scene = Scene.objects.create(name="M00 - The World", story=cls.story, order=0)
        # Two panels in ONE scene sharing order=0: exactly the collision measured on the book,
        # where nothing enforces uniqueness of Action.order within a scene.
        cls.first = Action.objects.create(name="m0_factory", scene=cls.scene, order=0)
        cls.second = Action.objects.create(name="m0_rooftop", scene=cls.scene, order=0)

    def widget(self):
        return ActionResource().fields["consistent_with"].widget

    def test_it_renders_the_name_pair(self):
        self.assertEqual(self.widget().render(self.second), "M00 - The World::m0_rooftop")

    def test_it_resolves_to_the_panel_named_not_an_arbitrary_one(self):
        w = self.widget()
        row = {"story": "Book"}
        self.assertEqual(w.clean("M00 - The World::m0_rooftop", row=row), self.second)
        self.assertEqual(w.clean("M00 - The World::m0_factory", row=row), self.first)

    def test_a_round_trip_survives_the_collision(self):
        """render -> clean returns the same panel, which the order key could not promise."""
        w = self.widget()
        for panel in (self.first, self.second):
            self.assertEqual(w.clean(w.render(panel), row={"story": "Book"}), panel)

    def test_the_legacy_order_form_still_imports(self):
        """An archive exported before this change still loads, ambiguity and all."""
        got = self.widget().clean("0:0", row={"story": "Book"})
        self.assertIn(got, (self.first, self.second))

    def test_an_unresolvable_reference_is_none_not_an_exception(self):
        self.assertIsNone(self.widget().clean("No Such Scene::nope", row={"story": "Book"}))
        self.assertIsNone(self.widget().clean("", row={"story": "Book"}))


class ExportTests(TestCase):
    """The export has to reach disk intact, and say so when part of it did not."""

    def _story_with_a_plate(self):
        from filer.models.imagemodels import Image as FilerImage
        from django.conf import settings as dj_settings
        from PIL import Image as PILImage

        story = Story.objects.create(name="Book")
        scene = Scene.objects.create(name="M00", story=story, order=0)
        rel = "plates/panel.png"
        abs_ = os.path.join(dj_settings.MEDIA_ROOT, rel)
        os.makedirs(os.path.dirname(abs_), exist_ok=True)
        PILImage.new("RGB", (16, 16), (1, 2, 3)).save(abs_)
        plate = FilerImage.objects.create(original_filename="panel.png", file=rel, name="panel")
        action = Action.objects.create(name="m0_factory", scene=scene, order=0)
        action.image = plate
        action.save()
        return story, action, plate

    def _export(self, story):
        from scene.models import Sync, SyncItem
        from scene.tasks.sync import TaskSyncExport

        sync = Sync.objects.create(story=story)
        item = SyncItem.objects.create(sync=sync, type=SyncItem.TYPE_EXPORT)
        task = mock.Mock()
        task.subject = item
        TaskSyncExport(task).process()
        item.refresh_from_db()
        return item, task

    def test_the_export_lands_on_disk_with_its_csvs_and_media(self):
        story, _action, _plate = self._story_with_a_plate()
        item, task = self._export(story)
        self.assertTrue(item.zip_file, "no zip attached to the SyncItem")
        with zipfile.ZipFile(item.zip_file.file.path) as zf:
            names = zf.namelist()
        self.assertIn("data/story.csv", names)
        self.assertIn("data/actions.csv", names)
        self.assertTrue(any(n.startswith("media/actions/") for n in names), names)

    def test_no_temp_file_is_left_behind(self):
        story, _a, _p = self._story_with_a_plate()
        before = set(os.listdir(tempfile.gettempdir()))
        self._export(story)
        leaked = [n for n in set(os.listdir(tempfile.gettempdir())) - before if n.endswith(".zip")]
        self.assertEqual(leaked, [])

    def test_a_media_file_that_cannot_be_added_is_reported(self):
        """The silent `except: pass` shipped a book with holes and called it success."""
        story, _action, plate = self._story_with_a_plate()
        os.unlink(plate.file.path)                      # the row survives, the bytes do not
        item, task = self._export(story)
        self.assertTrue(item.zip_file, "the export should still complete")
        logged = " ".join(str(c) for c in task.log.call_args_list)
        self.assertIn("could NOT be added", logged)
        self.assertIn("panel.png", logged)
