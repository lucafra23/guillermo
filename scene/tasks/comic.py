"""Graphic-novel render: turn a Render's items into something a person can actually read.

WHY THIS EXISTS
---------------
`RENDER_TYPE_GRAPHIC_NOVEL` has been a declared option with no implementation: both video render
paths open with `if item.render_type == item.RENDER_TYPE_GRAPHIC_NOVEL: return`. So a comic story
could be built panel by panel and then had no output at all — the one render type the platform
advertises for comics produced nothing.

TWO OUTPUTS, FOR TWO DIFFERENT JOBS
-----------------------------------
* **reader** (the default) — a paginated HTML page that points at the media URLs. Measured at 88 KB
  for this book's 484 pages. Always current, right for reading on the instance that made it.
* **portable** — one self-contained file with every page downscaled and embedded as a base64 JPEG.
  Measured at **91.5 MB** for the same 484 pages, built as a single string in memory (~700 MB peak
  in the worker). It survives being emailed or opened on a machine that has never heard of this
  server, which is the whole point, but it is not something to produce by accident. Hence
  `portable` is opt-in via `Render.config`, not the default.

WHERE THE OPTIONS LIVE
----------------------
On `Render.config`, NOT on `RenderItem.config`. `Render.refresh_render()` deletes and recreates
every item, so per-item config is destroyed by the one action you must run to populate the render.

WHICH IMAGE
-----------
`RenderItem.image` is set by `Render._create_item_from_action` to `image_comic or image` — the
lettered composite when there is one, the bare plate otherwise. So a book that has not been
lettered yet still renders, as art without words, rather than failing. That is a real state and a
quiet one, so the page count is reported split by lettered/unlettered: a book that renders 484
wordless plates should say so rather than look like a success.

`RenderItem.image` is a SNAPSHOT taken when the render was refreshed. Lettering an Action after
that does not change this render until "Refresh Render" runs again.
"""
import base64
import html
import io

from django.core.files.base import ContentFile
from django.utils.text import slugify
from filer.models.filemodels import File as FilerFile
from PIL import Image as PILImage

from task.models import Task

# Portable-file defaults. The width is the readable-on-a-laptop point; the quality is where JPEG
# stops visibly hurting halftone-heavy comic art. Both are overridable per render via config.
PORTABLE_MAX_WIDTH = 1100
PORTABLE_JPEG_QUALITY = 78

# Bounds for operator-supplied config. A width of 0 or a negative width makes PIL raise inside the
# per-page try/except, which would silently embed nothing and still report success.
MIN_WIDTH, MAX_WIDTH = 64, 5000
MIN_QUALITY, MAX_QUALITY = 1, 95

# Our own output. Used to decide whether a superseded document is ours to delete.
DOC_SUFFIXES = ("_reader.html", "_portable.html")


class ComicRenderError(Exception):
    """Raised for conditions that make the output meaningless (no pages, nothing embeddable)."""


def _int_option(config, key, default, low, high, task=None):
    """Coerce and clamp one operator-supplied option, reporting rather than raising."""
    raw = config.get(key, default)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        if task:
            task.log(f"config {key}={raw!r} is not a number; using {default}.")
        return default
    clamped = max(low, min(high, value))
    if clamped != value and task:
        task.log(f"config {key}={value} out of range [{low}, {high}]; using {clamped}.")
    return clamped


def _page_records(render, task=None):
    """[(order, label, filer_image)] in reading order, skipping and reporting empty pages.

    Also reports how many pages carry words. `image_comic` being empty across a whole story is the
    normal state before the lettering pass has run, and it is invisible in the output (the art
    renders fine, it just has no words), so it is stated explicitly rather than inferred.
    """
    pages, skipped, lettered = [], 0, 0
    for item in render.render_items.all().order_by("order", "id"):
        if not item.image:
            skipped += 1
            continue
        action = item.action
        if action and action.image_comic_id and item.image_id == action.image_comic_id:
            lettered += 1
        label = (action.name if action else None) or f"Page {item.order + 1}"
        pages.append((item.order, label, item.image))
    if task:
        if skipped:
            task.log(f"{skipped} render item(s) had no image and were skipped.")
        if pages and not lettered:
            task.log(
                f"None of the {len(pages)} pages are lettered: this render is art without words. "
                f"Letter the actions, then re-run 'Refresh Render' so the items pick up the "
                f"composites (render items are a snapshot, not a live view)."
            )
        elif lettered < len(pages):
            task.log(f"{lettered}/{len(pages)} pages are lettered; the rest are bare plates.")
    return pages


def _document_head(title):
    return f"""<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ margin: 0; background: #0a0a0f; color: #e8e8ea;
         font: 15px/1.5 ui-sans-serif, system-ui, -apple-system, sans-serif; }}
  header {{ padding: 1.5rem 1rem 0.5rem; text-align: center; }}
  h1 {{ font-size: 1.25rem; font-weight: 600; margin: 0 0 0.25rem; }}
  .meta {{ opacity: 0.6; font-size: 0.85rem; }}
  main {{ max-width: 900px; margin: 0 auto; padding: 1rem; }}
  figure {{ margin: 0 0 2rem; }}
  figure img {{ display: block; width: 100%; height: auto; border-radius: 3px;
                background: #14141a; }}
  figcaption {{ opacity: 0.45; font-size: 0.75rem; padding-top: 0.4rem; }}
  nav {{ position: sticky; top: 0; background: rgba(10,10,15,0.92); backdrop-filter: blur(6px);
         padding: 0.5rem 1rem; font-size: 0.8rem; text-align: center; z-index: 2; }}
  nav a {{ color: #9ad7ff; text-decoration: none; margin: 0 0.4rem; }}
  @media print {{ body {{ background: #fff; }} nav {{ display: none; }} }}
</style>
"""


def _figure(order, label, src):
    return (
        f"<figure id='p{order}'>"
        f"<img loading='lazy' src='{html.escape(src)}' alt='{html.escape(label)}'>"
        f"<figcaption>{html.escape(label)}</figcaption></figure>"
    )


def build_reader_html(render, pages, task=None):
    """Paginated reader that references media URLs. Small and always current.

    Returns (html, used) so the caller can tell "wrote 484 pages" from "wrote 0 and said 484".
    """
    title = render.name or "Untitled"
    parts = [_document_head(title), "<main>"]
    used, missing = 0, 0
    for order, label, image in pages:
        # filer's File.url is a bare `except: return ''` (filer/models/filemodels.py), so a row
        # with no usable file yields an empty string rather than raising. Emitting src='' would
        # resolve against the document's own URL: a broken image that silently re-requests the
        # page. Check the value, do not guard with try/except.
        url = image.url
        if not url:
            missing += 1
            continue
        parts.append(_figure(order, label, url))
        used += 1
    parts.insert(1, f"<header><h1>{html.escape(title)}</h1>"
                    f"<div class='meta'>{used} pages</div></header>")
    parts.append("</main>")
    if missing and task:
        task.log(f"{missing} page(s) had no reachable media URL and were left out.")
    if not used:
        raise ComicRenderError(
            f"None of the {len(pages)} pages had a reachable media URL; nothing was written."
        )
    return "".join(parts), used


def build_portable_html(render, pages, max_width=PORTABLE_MAX_WIDTH,
                        quality=PORTABLE_JPEG_QUALITY, task=None):
    """One self-contained file: every page downscaled and embedded, no server required.

    Returns (html, embedded). Raises ComicRenderError if nothing could be embedded — an empty
    self-contained file is worse than a failure, because it looks like a finished book.
    """
    title = render.name or "Untitled"
    parts = [_document_head(title), "<main>"]
    embedded, failures, first_error = 0, 0, None
    for order, label, image in pages:
        # filer's File.path is also a bare `except: return ''`. It returns '' for any storage
        # backend without local paths (S3 and friends), where PIL would then fail on every single
        # page. Report that once, as itself, rather than 484 identical per-page failures.
        path = image.path
        if not path:
            raise ComicRenderError(
                "The media storage backend does not expose local file paths, which the portable "
                "export needs in order to embed the art. Use the reader output instead "
                "(Render.config {\"portable\": false})."
            )
        try:
            with PILImage.open(path) as im:
                im = im.convert("RGB")
                if im.width > max_width:
                    im = im.resize((max_width, max(1, round(im.height * max_width / im.width))),
                                   PILImage.LANCZOS)
                buffer = io.BytesIO()
                im.save(buffer, format="JPEG", quality=quality, optimize=True)
            data = base64.b64encode(buffer.getvalue()).decode("ascii")
        except Exception as e:
            failures += 1
            if first_error is None:
                first_error = f"{type(e).__name__}: {e}"
            if task:
                task.log(f"Page {order} ({label}) could not be embedded: {e}")
            continue
        parts.append(_figure(order, label, f"data:image/jpeg;base64,{data}"))
        embedded += 1
    parts.insert(1, f"<header><h1>{html.escape(title)}</h1>"
                    f"<div class='meta'>{embedded} pages &middot; self-contained</div></header>")
    parts.append("</main>")
    if not embedded:
        raise ComicRenderError(
            f"None of the {len(pages)} pages could be embedded "
            f"({failures} failed; first: {first_error}). Nothing was written."
        )
    if failures and task:
        task.log(f"Embedded {embedded}/{len(pages)} pages; {failures} failed.")
    return "".join(parts), embedded


def _is_our_document(filer_file):
    """True when this row is a document THIS renderer wrote.

    A render document is generated HTML, never paid art, so the catastrophic case that
    `Action._discard_composite` guards against (deleting a plate that two fields happen to share)
    cannot arise here. Still checked by name, so a document attached by hand is left alone.
    """
    name = (getattr(filer_file, "original_filename", "") or "")
    return name.endswith(DOC_SUFFIXES)


def _discard_document(filer_file):
    """Delete a superseded render document, but only if it is ours and nothing else points at it.

    Without this every re-render orphans the previous file: at 91.5 MB a portable export, a few
    passes fill a disk with documents no row references and no cleanup finds. Mirrors
    `Action._discard_composite` (scene/models.py): anything unexpected leaves the row alone.
    """
    if not filer_file or not _is_our_document(filer_file):
        return
    try:
        for relation in filer_file._meta.related_objects:
            related_model = relation.related_model
            field_name = relation.field.name
            if related_model.objects.filter(**{field_name: filer_file}).exists():
                return  # still in use somewhere
        filer_file.delete()
    except Exception:  # a missing file or an odd relation must not fail the render
        pass


def _attach(render, filename, text):
    """Write the document, point the Render at it, and retire the one it replaces."""
    previous = render.document
    out = FilerFile.objects.create(
        original_filename=filename,
        file=ContentFile(text.encode("utf-8"), name=filename),
        name=filename,
    )
    try:
        render.document = out
        render.save(update_fields=["document"])
    except Exception:
        # The blob is written but nothing references it: remove it rather than leave a file that
        # no row knows about. Same rule as Action.letter (scene/models.py).
        try:
            out.delete()
        except Exception:
            pass
        raise
    if previous and previous.pk != out.pk:
        _discard_document(previous)
    return out


class ComicRender:
    """Builds the graphic-novel output for a Render and attaches it to `Render.document`."""

    def __init__(self, task):
        self.task = task

    def process(self):
        try:
            return self._process()
        except Exception as e:
            # Deliberately NOT re-raised, for the reason spelled out in TaskLetterAction
            # (scene/tasks/tasks.py): the runner's handler does `e.status in TASK_RETRY_EXCEPTIONS`
            # and a plain ValueError has no `.status`, so re-raising turns a clear message into an
            # AttributeError inside an except block and skips the runner's next_tasks dispatch.
            self.task.log(f"Comic render failed: {type(e).__name__}: {e}")
            self.task.set_status(Task.TASK_STATUS_ERROR)
            return None

    def _process(self):
        render = self.task.subject
        pages = _page_records(render, self.task)
        if not pages:
            raise ComicRenderError(
                "No pages to render: every item is missing an image. Generate the panels first, "
                "then re-run 'Refresh Render' so the items pick them up."
            )

        config = render.config if isinstance(render.config, dict) else {}
        # Defaults to the 88 KB reader. `portable` produces ~91.5 MB for a book this size, which
        # is a deliberate choice, never an accident.
        portable = bool(config.get("portable", False))

        slug = slugify(render.name) or f"render-{render.id}"
        if portable:
            text, written = build_portable_html(
                render, pages,
                max_width=_int_option(config, "max_width", PORTABLE_MAX_WIDTH,
                                      MIN_WIDTH, MAX_WIDTH, self.task),
                quality=_int_option(config, "quality", PORTABLE_JPEG_QUALITY,
                                    MIN_QUALITY, MAX_QUALITY, self.task),
                task=self.task,
            )
            filename = f"{slug}_portable.html"
        else:
            text, written = build_reader_html(render, pages, task=self.task)
            filename = f"{slug}_reader.html"

        out = _attach(render, filename, text)
        size_mb = len(text.encode("utf-8")) / 1e6
        # `written`, not `len(pages)`: the count must describe the file, not the intent.
        self.task.log(f"Rendered {written} pages to {filename} ({size_mb:.1f} MB).")
        return out
