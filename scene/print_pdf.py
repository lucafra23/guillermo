"""Print output: the pages of a comic render, laid out on paper.

A book that can be authored in a browser and not printed is not finished, and HTML has no
opinion about paper. This turns the SAME pages the reader shows into a PDF, so the two lanes
cannot drift: ordering and plate resolution come from `_page_records`, never re-derived here.

WHAT IS DECIDED HERE AND WHAT IS NOT

Geometry is computed, not chosen. `fit()` says where two portrait panels land on one landscape
page and how much paper is left over; which trim to print on, and the real margins, are a
printer's answer and an author's call. The defaults exist so a proof can be built, and they are
labelled provisional rather than presented as a house style.

The panels are sized by HEIGHT, because height is the binding dimension for a portrait panel on
a landscape page. Width is then checked rather than assumed: a trim wider than the layout wants
turns the surplus into side margin, not into bigger art, and that is a fact about the trim worth
seeing before paying for it.
"""
import logging
import os

logger = logging.getLogger(__name__)

MM = 72.0 / 25.4                      # PostScript points per millimetre

# Width x height in mm, as printed. `None` width means "computed from the fit" -- the trim that
# wastes no horizontal paper at this height, which is a useful thing to be able to quote.
TRIMS = {
    "a4-landscape":        (297.0, 210.0),
    "a5-landscape":        (210.0, 148.0),
    "us-letter-landscape": (279.4, 215.9),
    "square-210":          (210.0, 210.0),
    "snug":                (None,  210.0),
}
DEFAULT_TRIM = "a4-landscape"

# Provisional. Real margins need the printer's spec (bleed, safe area, binding creep); these are
# here so a proof can be built and measured, not so a layout can be locked.
MARGIN_MM = 15.0
GUTTER_MM = 10.0

MIN_PER_PAGE, MAX_PER_PAGE = 1, 2
NOMINAL_ASPECT = 1.75                 # h/w of an uncropped plate; only a fallback


class PrintLayoutError(Exception):
    """Raised when a request cannot produce a meaningful document."""


def plate_aspect(path, default=NOMINAL_ASPECT):
    """Measured h/w of a plate. Cropped plates are common, so this is measured, not assumed."""
    try:
        from PIL import Image
        with Image.open(path) as im:
            w, h = im.size
        if w and h:
            return h / float(w)
    except Exception:
        logger.debug("Could not measure %s; using the nominal aspect", path, exc_info=True)
    return default


def fit(trim_name=DEFAULT_TRIM, aspect=NOMINAL_ASPECT, per_page=2,
        margin=MARGIN_MM, gutter=GUTTER_MM):
    """Where the panels land, in mm. `slack_w` is the horizontal paper left over.

    Positive slack means the trim is wider than this layout wants: the panels are height-bound,
    so that width becomes margin rather than art. Negative slack means they do not fit and must
    be sized by width instead, which this reports rather than silently cropping.
    """
    if trim_name not in TRIMS:
        raise PrintLayoutError(
            f"Unknown trim {trim_name!r}. Known: {', '.join(sorted(TRIMS))}.")
    per_page = max(MIN_PER_PAGE, min(MAX_PER_PAGE, int(per_page)))
    tw, th = TRIMS[trim_name]

    panel_h = th - 2 * margin
    panel_w = panel_h / aspect
    gutters = gutter * (per_page - 1)
    need_w = per_page * panel_w + gutters + 2 * margin
    if tw is None:
        tw = need_w                                   # the "snug" trim IS the fit
    slack = tw - need_w

    if slack < 0:                                     # too wide: re-size by width instead
        panel_w = (tw - 2 * margin - gutters) / per_page
        panel_h = panel_w * aspect
        bound = "width"
    else:
        bound = "height"

    return dict(trim=trim_name, trim_w=tw, trim_h=th, per_page=per_page,
                panel_w=panel_w, panel_h=panel_h, need_w=need_w, slack_w=slack,
                margin=margin, gutter=gutter, bound_by=bound, aspect=aspect)


def fit_report(aspect=NOMINAL_ASPECT, per_page=2):
    """Every trim measured against one aspect. A measurement, not a recommendation."""
    lines = ["panels are sized by the binding dimension, then checked against the other",
             f"panel aspect h/w = {aspect:.3f}, margin {MARGIN_MM:.0f} mm, "
             f"gutter {GUTTER_MM:.0f} mm  (provisional)",
             "%-22s %-15s %-15s %-10s %s" % ("trim", "page mm", "panel mm", "slack", "bound by")]
    for name in sorted(TRIMS):
        f = fit(name, aspect, per_page)
        lines.append("%-22s %-15s %-15s %-10s %s" % (
            name,
            "%.0f x %.0f" % (f["trim_w"], f["trim_h"]),
            "%.0f x %.0f" % (f["panel_w"], f["panel_h"]),
            "%+.0f mm" % f["slack_w"],
            f["bound_by"]))
    return "\n".join(lines)


def _wrap(text, width):
    out, line = [], ""
    for word in (text or "").split():
        candidate = f"{line} {word}".strip()
        if len(candidate) > width and line:
            out.append(line)
            line = word
        else:
            line = candidate
    if line:
        out.append(line)
    return out


def build_pdf(pages, out_path, trim=DEFAULT_TRIM, per_page=2, margin=MARGIN_MM,
              gutter=GUTTER_MM, front_matter=None, task=None, compress=True,
              jpeg_quality=None):
    """Write `pages` to a print PDF. Returns (page_count, layout).

    `pages` is the [(order, label, path)] that the reader lane produces, in reading order.
    Nothing here re-derives that order: a print lane with its own idea of the book is how two
    artefacts silently disagree about what the book is.

    `jpeg_quality` is off by default, which means the plates go in exactly as they are: a print
    master should not be lossy because the file was inconvenient. Measured on this book, that is
    ~2.7 MB per panel. Setting it re-encodes, and the log says what that bought, so the trade is
    made on numbers rather than on a feeling about file size.
    """
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    if not pages:
        raise PrintLayoutError("No pages to print.")

    layout = fit(trim, plate_aspect(pages[0][2]), per_page, margin, gutter)
    # Take per_page BACK from the layout. fit() clamps it, and using the raw argument here meant
    # a caller passing 5 got a layout sized for 2 and a row computed for 5 -- panels drawn off
    # the left edge -- while 0 raised from range() and -1 produced a document with no panels in
    # it and no complaint. The clamped value is the only one both halves agree on.
    per_page = layout["per_page"]
    pw, ph = layout["trim_w"] * MM, layout["trim_h"] * MM
    c = canvas.Canvas(out_path, pagesize=(pw, ph))
    # Compression is ON for anything real. It is switchable only because an uncompressed file
    # lets a test read the labels straight out of the bytes; leaving it off by default would
    # trade a verifiable four-page fixture for an unusable six-hundred-page book.
    c.setPageCompression(1 if compress else 0)
    written = 0

    fm = front_matter or {}
    if fm.get("cover") or fm.get("title"):
        _draw_cover(c, pw, ph, fm, layout)
        written += 1
    if fm.get("epigraph"):
        _draw_epigraph(c, pw, ph, fm["epigraph"])
        written += 1

    slot_w = layout["panel_w"] * MM
    slot_h = layout["panel_h"] * MM
    gutter_pt = layout["gutter"] * MM
    row_w = per_page * slot_w + (per_page - 1) * gutter_pt
    x0 = (pw - row_w) / 2.0
    y0 = (ph - slot_h) / 2.0

    for i in range(0, len(pages), per_page):
        chunk = pages[i:i + per_page]
        for j, (_order, label, path) in enumerate(chunk):
            x = x0 + j * (slot_w + gutter_pt)
            try:
                img = ImageReader(_maybe_recompress(path, jpeg_quality))
            except Exception as e:
                # Reported, never skipped in silence: a missing plate is a hole in the book.
                msg = f"Could not place {label!r}: {type(e).__name__}: {e}"
                if task:
                    task.log(msg)
                logger.warning(msg)
                c.setFont("Helvetica", 8)
                c.drawCentredString(x + slot_w / 2, y0 + slot_h / 2, f"[missing plate: {label}]")
                continue
            iw, ih = img.getSize()
            scale = min(slot_w / iw, slot_h / ih)      # fit, never distort
            w, h = iw * scale, ih * scale
            c.drawImage(img, x + (slot_w - w) / 2, y0 + (slot_h - h) / 2,
                        width=w, height=h, preserveAspectRatio=True, mask='auto')
            c.setFont("Helvetica", 6)
            c.drawCentredString(x + slot_w / 2, y0 - 10, str(label))
        c.showPage()
        written += 1

    c.save()
    return written, layout


def _maybe_recompress(path, jpeg_quality):
    """The plate itself, or a JPEG of it when the caller asked for one.

    Returns something ImageReader accepts. A failure here falls back to the original file: a
    smaller book is a preference, a book with a hole in it is a defect.
    """
    if not jpeg_quality:
        return path
    try:
        import io as _io

        from PIL import Image

        with Image.open(path) as im:
            rgb = im.convert("RGB")
            buffer = _io.BytesIO()
            rgb.save(buffer, format="JPEG", quality=int(jpeg_quality), optimize=True)
        buffer.seek(0)
        return buffer
    except Exception:
        logger.warning("Could not re-encode %s; embedding it as-is", path, exc_info=True)
        return path


def _draw_cover(c, pw, ph, fm, layout):
    from reportlab.lib.utils import ImageReader

    cover = fm.get("cover")
    if cover and os.path.exists(str(cover)):
        try:
            img = ImageReader(str(cover))
            iw, ih = img.getSize()
            scale = min((pw - 2 * layout["margin"] * MM) / iw,
                        (ph - 2 * layout["margin"] * MM) / ih)
            w, h = iw * scale, ih * scale
            c.drawImage(img, (pw - w) / 2, (ph - h) / 2, width=w, height=h,
                        preserveAspectRatio=True, mask='auto')
        except Exception:
            logger.warning("Cover image could not be drawn", exc_info=True)
    title = fm.get("title") or ""
    if title:
        c.setFont("Helvetica-Bold", 28)
        c.drawCentredString(pw / 2, ph - layout["margin"] * MM - 30, title)
    subtitle = fm.get("subtitle") or ""
    if subtitle:
        c.setFont("Helvetica", 14)
        c.drawCentredString(pw / 2, ph - layout["margin"] * MM - 55, subtitle)
    c.showPage()


def _draw_epigraph(c, pw, ph, text):
    c.setFont("Helvetica-Oblique", 12)
    lines = _wrap(str(text), 68)
    y = ph / 2 + (len(lines) - 1) * 9
    for line in lines:
        c.drawCentredString(pw / 2, y, line)
        y -= 18
    c.showPage()
