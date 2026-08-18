"""Tests for the zip guards in TaskSyncImport.

SimpleTestCase throughout: nothing here touches the database, and a guard that refuses
a malicious archive should be provable without one.

What these exist to catch is specific. The guards raise SyncImportError, and that name
was for a while defined nowhere -- lost when the monolithic scene/tasks.py was split
into a package. A guard that raises NameError instead of its own exception does not
roll back and does not log; it just kills the task, and the broad `except Exception`
underneath never runs either, because evaluating the except clause raises too.

So every test below asserts the exception TYPE. Deleting the class again turns them
red rather than leaving them passing on some other exception.
"""
import io
import os
import tempfile
import zipfile

from django.test import SimpleTestCase

from scene.tasks.sync import TaskSyncImport, SyncImportError


def zip_containing(*names):
    """An in-memory zip with one tiny entry per name."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name in names:
            zf.writestr(name, b"x")
    buf.seek(0)
    return buf


class SyncImportErrorTests(SimpleTestCase):

    def test_is_an_exception_subclass(self):
        """Defined, and catchable as an exception rather than by accident."""
        self.assertTrue(issubclass(SyncImportError, Exception))


class SafeExtractTests(SimpleTestCase):

    def setUp(self):
        # __new__ rather than __init__: _safe_extract needs no task, and constructing a
        # real one would drag in a Task row for no reason.
        self.importer = TaskSyncImport.__new__(TaskSyncImport)
        self.tmp = tempfile.TemporaryDirectory()
        self.dest = self.tmp.name
        self.addCleanup(self.tmp.cleanup)

    def extract(self, buf, importer=None):
        with zipfile.ZipFile(buf) as zf:
            (importer or self.importer)._safe_extract(zf, self.dest)

    def test_path_traversal_is_refused(self):
        """An entry escaping the destination raises SyncImportError, not NameError."""
        with self.assertRaises(SyncImportError) as ctx:
            self.extract(zip_containing("../../evil.txt"))
        self.assertIn("Unsafe path", str(ctx.exception))
        escaped = os.path.join(os.path.dirname(self.dest), "evil.txt")
        self.assertFalse(os.path.exists(escaped), "the guard let a file escape")

    def test_nested_traversal_is_refused(self):
        """Traversal buried mid-path is caught too, not just a leading '..'."""
        with self.assertRaises(SyncImportError):
            self.extract(zip_containing("nested/../../../etc/passwd"))

    def test_size_cap_is_enforced(self):
        """Total declared size over the cap raises SyncImportError, not NameError."""
        importer = TaskSyncImport.__new__(TaskSyncImport)
        importer.MAX_EXTRACT_BYTES = 0
        with self.assertRaises(SyncImportError) as ctx:
            self.extract(zip_containing("a.bin"), importer=importer)
        self.assertIn("size cap", str(ctx.exception))

    def test_ordinary_archive_extracts(self):
        """The guards must not refuse a well-formed archive.

        A check that can only fail is as useless as one that can only pass, and without
        this the three above would still be green if _safe_extract simply always raised.
        """
        self.extract(zip_containing("story.csv", "sub/action.csv"))
        self.assertTrue(os.path.exists(os.path.join(self.dest, "story.csv")))
        self.assertTrue(os.path.exists(os.path.join(self.dest, "sub", "action.csv")))
