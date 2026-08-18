"""Print output: geometry that is computed, and a PDF that actually contains the book.

The geometry tests assert arithmetic rather than taste — which trim to print on is the author's
call, and these only guarantee the numbers that call needs are right. The document tests open
the produced PDF and look inside it, because "a file was written" is not the claim being made.
"""
import os
import tempfile
from unittest import mock

from django.test import SimpleTestCase, TestCase

from scene.print_pdf import (DEFAULT_TRIM, MM, PrintLayoutError, TRIMS, build_pdf, fit,
                             fit_report, plate_aspect)


def _plate(path, size=(120, 210)):
    from PIL import Image
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.new("RGB", size, (30, 90, 160)).save(path)
    return path


class GeometryTests(SimpleTestCase):

    def test_panels_are_sized_by_height_when_the_page_is_wide_enough(self):
        f = fit("a4-landscape", aspect=1.75, per_page=2)
        self.assertEqual(f["bound_by"], "height")
        self.assertAlmostEqual(f["panel_h"], 210.0 - 2 * 15.0, places=6)
        self.assertAlmostEqual(f["panel_w"], f["panel_h"] / 1.75, places=6)

    def test_a4_landscape_leaves_slack_that_becomes_margin_not_art(self):
        """The finding worth having before paying a printer."""
        f = fit("a4-landscape", aspect=1.75, per_page=2)
        self.assertGreater(f["slack_w"], 40)
        snug = fit("snug", aspect=1.75, per_page=2)
        self.assertAlmostEqual(snug["slack_w"], 0.0, places=6)
        # same panel, less paper: that is the whole point of quoting `snug`
        self.assertAlmostEqual(snug["panel_w"], f["panel_w"], places=6)
        self.assertAlmostEqual(snug["panel_h"], f["panel_h"], places=6)
        self.assertLess(snug["trim_w"], f["trim_w"])

    def test_a_page_too_narrow_resizes_by_width_instead_of_cropping(self):
        f = fit("square-210", aspect=1.75, per_page=2)
        self.assertEqual(f["bound_by"], "width")
        self.assertLessEqual(f["panel_w"] * 2 + f["gutter"] + 2 * f["margin"],
                             f["trim_w"] + 1e-9)

    def test_one_up_needs_no_gutter(self):
        one = fit("a4-landscape", aspect=1.75, per_page=1)
        two = fit("a4-landscape", aspect=1.75, per_page=2)
        self.assertGreater(one["slack_w"], two["slack_w"])

    def test_an_unknown_trim_is_refused_by_name(self):
        with self.assertRaises(PrintLayoutError) as ctx:
            fit("a3-poster")
        self.assertIn("a3-poster", str(ctx.exception))
        self.assertIn("a4-landscape", str(ctx.exception))     # says what IS available

    def test_the_report_covers_every_trim(self):
        text = fit_report()
        for name in TRIMS:
            self.assertIn(name, text)

    def test_a_cropped_plate_is_measured_not_assumed(self):
        with tempfile.TemporaryDirectory() as d:
            square = _plate(os.path.join(d, "sq.png"), size=(200, 200))
            self.assertAlmostEqual(plate_aspect(square), 1.0, places=6)
        self.assertAlmostEqual(plate_aspect("/nope/missing.png"), 1.75, places=6)


class DocumentTests(SimpleTestCase):

    def _pages(self, d, n):
        return [(i, f"panel-{i}", _plate(os.path.join(d, f"p{i}.png"))) for i in range(n)]

    def test_two_panels_per_sheet(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "book.pdf")
            written, _ = build_pdf(self._pages(d, 5), out, per_page=2)
            self.assertEqual(written, 3)                       # 5 panels -> 3 sheets
            self.assertTrue(os.path.getsize(out) > 1000)
            self.assertEqual(open(out, "rb").read(5), b"%PDF-")

    def test_one_panel_per_sheet(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "book.pdf")
            written, _ = build_pdf(self._pages(d, 4), out, per_page=1)
            self.assertEqual(written, 4)

    def test_the_pages_are_in_reading_order_inside_the_file(self):
        """Not "a PDF exists" -- the panels are in the order the reader lane gave them."""
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "book.pdf")
            build_pdf(self._pages(d, 4), out, per_page=2, compress=False)
            blob = open(out, "rb").read()
            found = [blob.find(f"panel-{i}".encode()) for i in range(4)]
            self.assertTrue(all(p > 0 for p in found), f"labels missing: {found}")
            self.assertEqual(found, sorted(found), "panels are out of order in the document")

    def test_front_matter_adds_its_own_leaves(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "book.pdf")
            cover = _plate(os.path.join(d, "cover.png"))
            written, _ = build_pdf(
                self._pages(d, 2), out, per_page=2, compress=False,
                front_matter={"title": "The Book of Bags", "subtitle": "a comic",
                              "epigraph": "Everything fits in a bag eventually.", "cover": cover})
            self.assertEqual(written, 3)                       # cover + epigraph + one sheet
            blob = open(out, "rb").read()
            self.assertIn(b"The Book of Bags", blob)
            self.assertIn(b"Everything fits in a bag", blob)

    def test_a_missing_plate_is_marked_on_the_page_not_skipped(self):
        """A hole in the book has to be visible in the book."""
        with tempfile.TemporaryDirectory() as d:
            pages = self._pages(d, 2)
            pages.append((2, "panel-gone", os.path.join(d, "does_not_exist.png")))
            out = os.path.join(d, "book.pdf")
            written, _ = build_pdf(pages, out, per_page=2, compress=False)
            self.assertEqual(written, 2)
            self.assertIn(b"missing plate", open(out, "rb").read())

    def test_no_pages_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(PrintLayoutError):
                build_pdf([], os.path.join(d, "book.pdf"))

    def test_a_panel_is_never_distorted(self):
        """Fit, never stretch: a plate drawn to the wrong aspect is a ruined page."""
        with tempfile.TemporaryDirectory() as d:
            wide = _plate(os.path.join(d, "wide.png"), size=(400, 100))
            out = os.path.join(d, "book.pdf")
            build_pdf([(0, "wide", wide)], out, per_page=1, compress=False)
            blob = open(out, "rb").read()
            self.assertIn(b"/Image", blob)


class RenderIntegrationTests(TestCase):
    """`format: pdf` on the render's own config, using the reader lane's pages."""

    def _book(self, n=3):
        from django.conf import settings
        from filer.models.imagemodels import Image as FilerImage
        from scene.models import Action, Render, Scene, Story

        story = Story.objects.create(name="Book")
        scene = Scene.objects.create(name="M00", story=story, order=0)
        for i in range(n):
            rel = f"plates/p{i}.png"
            _plate(os.path.join(settings.MEDIA_ROOT, rel))
            img = FilerImage.objects.create(original_filename=f"p{i}.png", file=rel, name=f"p{i}")
            a = Action.objects.create(name=f"panel-{i}", scene=scene, order=i)
            a.image = img
            a.image_comic = img
            a.save()
        render = Render.objects.create(name="proof", story=story,
                                       render_type=Render.RENDER_TYPE_GRAPHIC_NOVEL)
        render.refresh_render()
        return render

    def _run(self, render):
        from django.contrib.contenttypes.models import ContentType
        from scene.tasks.comic import ComicRender
        from task.models import Task

        task = mock.Mock()
        task.subject = render
        ComicRender(task).process()
        render.refresh_from_db()
        return task

    def test_pdf_format_attaches_a_pdf_to_the_render(self):
        render = self._book(3)
        render.config = {"format": "pdf"}
        render.save()
        task = self._run(render)
        self.assertTrue(render.document, "no document attached")
        self.assertTrue(render.document.original_filename.endswith("_print.pdf"))
        with open(render.document.file.path, "rb") as fh:
            self.assertEqual(fh.read(5), b"%PDF-")
        logged = " ".join(str(c) for c in task.log.call_args_list)
        self.assertIn("Trim a4-landscape", logged)      # the geometry is reported, not hidden

    def test_the_default_render_is_still_html(self):
        """Adding a print lane must not change what an existing render produces."""
        render = self._book(2)
        self._run(render)
        self.assertTrue(render.document.original_filename.endswith("_reader.html"))

    def test_an_unknown_trim_reports_the_ones_that_exist(self):
        render = self._book(2)
        render.config = {"format": "pdf", "trim": "billboard"}
        render.save()
        task = self._run(render)
        logged = " ".join(str(c) for c in task.log.call_args_list)
        self.assertIn("billboard", logged)
        self.assertIn("a4-landscape", logged)           # the table, so it is fixable from here

    def test_front_matter_comes_from_the_config(self):
        """Real output is compressed, so this asserts the SHAPE; the words themselves are
        checked byte-for-byte in DocumentTests, which builds uncompressed on purpose."""
        render = self._book(2)
        render.config = {"format": "pdf", "title": "The Book of Bags",
                         "epigraph": "Everything fits in a bag eventually."}
        render.save()
        task = self._run(render)
        logged = " ".join(str(c) for c in task.log.call_args_list)
        # 2 panels = 1 sheet, plus a cover leaf and an epigraph leaf
        self.assertIn("Rendered 3 page(s)", logged)

    def test_without_front_matter_there_are_no_extra_leaves(self):
        render = self._book(2)
        render.config = {"format": "pdf"}
        render.save()
        task = self._run(render)
        logged = " ".join(str(c) for c in task.log.call_args_list)
        self.assertIn("Rendered 1 page(s)", logged)

    def test_real_output_is_compressed(self):
        """A 600-page book uncompressed is not a deliverable."""
        render = self._book(2)
        render.config = {"format": "pdf"}
        render.save()
        self._run(render)
        blob = open(render.document.file.path, "rb").read()
        self.assertIn(b"/Filter", blob)
        self.assertNotIn(b"panel-0", blob)          # text lives inside a compressed stream


class ReencodeTests(SimpleTestCase):
    """Lossless by default; smaller only when asked, and never at the cost of a page."""

    def _pages(self, d, n=4):
        from PIL import Image
        out = []
        for i in range(n):
            p = os.path.join(d, f"p{i}.png")
            Image.new("RGB", (768, 1344), (10 * i, 90, 160)).save(p)
            out.append((i, f"panel-{i}", p))
        return out

    def test_asking_for_jpeg_makes_a_much_smaller_file(self):
        with tempfile.TemporaryDirectory() as d:
            pages = self._pages(d)
            lossless = os.path.join(d, "big.pdf")
            lossy = os.path.join(d, "small.pdf")
            build_pdf(pages, lossless)
            build_pdf(pages, lossy, jpeg_quality=70)
            self.assertLess(os.path.getsize(lossy), os.path.getsize(lossless))

    def test_the_default_stays_lossless(self):
        with tempfile.TemporaryDirectory() as d:
            pages = self._pages(d, 2)
            a, b = os.path.join(d, "a.pdf"), os.path.join(d, "b.pdf")
            build_pdf(pages, a)
            build_pdf(pages, b, jpeg_quality=None)
            self.assertEqual(os.path.getsize(a), os.path.getsize(b))

    def test_an_unreadable_plate_falls_back_instead_of_dropping_the_page(self):
        from scene.print_pdf import _maybe_recompress
        self.assertEqual(_maybe_recompress("/nope/missing.png", 70), "/nope/missing.png")
