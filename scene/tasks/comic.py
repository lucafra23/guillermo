"""Graphic-novel render: turn a Render's items into something a person can actually read.

WHY THIS EXISTS
---------------
`RENDER_TYPE_GRAPHIC_NOVEL` has been a declared option with no implementation: both video render
paths open with `if item.render_type == item.RENDER_TYPE_GRAPHIC_NOVEL: return`. So a comic story
could be built panel by panel and then had no output at all — the one render type the platform
advertises for comics produced nothing.

TWO OUTPUTS, FOR TWO DIFFERENT JOBS
-----------------------------------
* **reader** — a paginated HTML page that points at the media URLs. Small (tens of KB), always
  current, right for reading on the instance that made it.
* **portable** — one self-contained file with every page downscaled and embedded as a base64 JPEG.
  Large, but it survives being emailed, dropped in a folder, or opened on a machine that has never
  heard of this server. That property is the whole point: a reader that references relative media
  paths is perfect locally and useless anywhere else.

WHICH IMAGE
-----------
`RenderItem.image` is set by `Render._create_item_from_action` to `image_comic or image` — the
lettered composite when there is one, the bare plate otherwise. So a book that has not been
lettered yet still renders, as art without words, rather than failing. A page with no image at all
is reported in the task log and skipped, never silently dropped.
"""
import base64
import html
import io
import os

from django.conf import settings
from django.core.files.base import ContentFile
from django.utils.text import slugify
from filer.models.filemodels import File as FilerFile
from PIL import Image as PILImage

# Portable-file defaults. The width is the readable-on-a-laptop point; the quality is where JPEG
# stops visibly hurting halftone-heavy comic art. Both are overridable per render via config.
PORTABLE_MAX_WIDTH = 1100
PORTABLE_JPEG_QUALITY = 78


def _page_records(render, task=None):
    """[(order, label, filer_image)] in reading order, skipping and reporting empty pages."""
    pages, skipped = [], 0
    for item in render.render_items.all().order_by("order", "id"):
        if not item.image:
            skipped += 1
            continue
        action = item.action
        label = (action.name if action else None) or f"Page {item.order + 1}"
        pages.append((item.order, label, item.image))
    if skipped and task:
        task.log(f"{skipped} render item(s) had no image and were skipped.")
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


def build_reader_html(render, pages):
    """Paginated reader that references media URLs. Small and always current."""
    title = render.name or "Untitled"
    parts = [_document_head(title),
             f"<header><h1>{html.escape(title)}</h1>"
             f"<div class='meta'>{len(pages)} pages</div></header><main>"]
    for order, label, image in pages:
        try:
            url = image.url
        except Exception:
            continue
        parts.append(
            f"<figure id='p{order}'>"
            f"<img loading='lazy' src='{html.escape(url)}' alt='{html.escape(label)}'>"
            f"<figcaption>{html.escape(label)}</figcaption></figure>"
        )
    parts.append("</main>")
    return "".join(parts)


def build_portable_html(render, pages, max_width=PORTABLE_MAX_WIDTH,
                        quality=PORTABLE_JPEG_QUALITY, task=None):
    """One self-contained file: every page downscaled and embedded, no server required."""
    title = render.name or "Untitled"
    parts = [_document_head(title),
             f"<header><h1>{html.escape(title)}</h1>"
             f"<div class='meta'>{len(pages)} pages &middot; self-contained</div></header><main>"]
    embedded = 0
    for order, label, image in pages:
        try:
            with PILImage.open(image.path) as im:
                im = im.convert("RGB")
                if im.width > max_width:
                    im = im.resize((max_width, round(im.height * max_width / im.width)),
                                   PILImage.LANCZOS)
                buffer = io.BytesIO()
                im.save(buffer, format="JPEG", quality=quality, optimize=True)
            data = base64.b64encode(buffer.getvalue()).decode("ascii")
        except Exception as e:
            if task:
                task.log(f"Page {order} ({label}) could not be embedded: {e}")
            continue
        parts.append(
            f"<figure id='p{order}'>"
            f"<img loading='lazy' src='data:image/jpeg;base64,{data}' alt='{html.escape(label)}'>"
            f"<figcaption>{html.escape(label)}</figcaption></figure>"
        )
        embedded += 1
    parts.append("</main>")
    if task:
        task.log(f"Embedded {embedded}/{len(pages)} pages in the portable file.")
    return "".join(parts)


def _attach(render, filename, text):
    out = FilerFile.objects.create(
        original_filename=filename,
        file=ContentFile(text.encode("utf-8"), name=filename),
        name=filename,
    )
    render.document = out
    render.save(update_fields=["document"])
    return out


class ComicRender:
    """Builds the graphic-novel output for a Render and attaches it to `Render.document`."""

    def __init__(self, task):
        self.task = task

    def process(self):
        render = self.task.subject
        pages = _page_records(render, self.task)
        if not pages:
            self.task.log(
                "No pages to render: every item is missing an image. Generate the panels first, "
                "then re-run 'Refresh Render' so the items pick them up."
            )
            return None

        config = render.render_items.first().config if render.render_items.exists() else None
        config = config if isinstance(config, dict) else {}
        portable = bool(config.get("portable", True))

        slug = slugify(render.name) or f"render-{render.id}"
        if portable:
            text = build_portable_html(
                render, pages,
                max_width=int(config.get("max_width", PORTABLE_MAX_WIDTH)),
                quality=int(config.get("quality", PORTABLE_JPEG_QUALITY)),
                task=self.task,
            )
            filename = f"{slug}_portable.html"
        else:
            text = build_reader_html(render, pages)
            filename = f"{slug}_reader.html"

        out = _attach(render, filename, text)
        size_mb = len(text.encode("utf-8")) / 1e6
        self.task.log(f"Rendered {len(pages)} pages to {filename} ({size_mb:.1f} MB).")
        return out
