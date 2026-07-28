"""Vector-text compositor for graphic-novel panels.

The image model draws art; THIS draws the words — pixel-perfect, exact spelling, no garble.
Panels are generated text-free (clean art with reserved negative space) and every word is
composited here with Pillow, deterministically and for free. Changing a caption re-composites;
it never re-generates, so the art is preserved and nothing is spent.

Seven containers:
  bubble   — character speech (white oval, black outline, tapered tail to the speaker)
  thought  — inner monologue (white scalloped cloud, shrinking dot trail, no hard tail)
  caption  — narrator voice-over (tan box, no tail)
  plaque   — in-world text (proverb / notification): gilded box on near-black
  ui       — on-screen/diegetic text (dark rounded box, cyan)
  namecard — conference-badge intro device; text is "NAME|one-line descriptor"
  screen   — bare letters painted onto the art with NO container, for words that belong to a
             surface inside the scene (a slide, a monitor, a sign)

Element spec (positions are FRACTIONS 0..1 of the image, so a panel re-letters at any
resolution — the same spec composites onto a thumbnail or a 3x print plate):
  {"type": "...", "text": "...", "box": [x, y, w, h], "tail": [tx, ty] | None}
  box = the BODY of the balloon; tail = the point it aims at (a mouth), any direction.

Balloon and tail are rasterised as ONE silhouette, and the outline is derived from that
silhouette (outer minus inset). The tail therefore merges into the body under a single
continuous stroke, with no balloon arc cutting across the tail root.

`draw_overlay()` has no Django dependency and can be exercised standalone; only font
resolution consults settings, and only when Django is configured.
"""
import logging
import math
import os

from PIL import Image, ImageChops, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------- fonts
# Font FACES are deployment-supplied, never vendored: comic lettering faces are typically
# licensed for use but not for redistribution inside another product. Set LETTERING_FONT_DIR
# to a directory holding the two faces named by LETTERING_FONT_BOLD / _REGULAR.
#
# The fallback is deliberately loud. Silently substituting DejaVu changes every balloon in the
# book — same geometry, different letterforms — and a book half-lettered in each face is the
# kind of defect that is only visible once it is expensive to fix.
FONT_BOLD = "bold"
FONT_REG = "regular"

_FALLBACK = {
    FONT_BOLD: "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    FONT_REG: "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
}
_DEFAULT_FILENAMES = {FONT_BOLD: "KOMTXTB_.ttf", FONT_REG: "KOMTXT__.ttf"}

_resolved_cache = {}
_warned = set()


def _setting(name, default=None):
    try:
        from django.conf import settings
    except Exception:
        return os.environ.get(name, default)
    try:
        return getattr(settings, name, os.environ.get(name, default))
    except Exception:  # settings not configured (standalone parity runs)
        return os.environ.get(name, default)


def font_path(role):
    """Absolute path to the face for `role`, or the DejaVu fallback, warning once if so.

    The cache is keyed on the resolved (dir, filename), NOT on the role alone: a worker that
    starts before its font volume is mounted would otherwise pin DejaVu for its whole life while
    a sibling worker letters the same book in the real face. Keying on the inputs means the
    moment the configuration or the file appears, the next call picks it up.
    """
    font_dir = _setting("LETTERING_FONT_DIR")
    filename = _setting(
        "LETTERING_FONT_BOLD" if role == FONT_BOLD else "LETTERING_FONT_REGULAR",
        _DEFAULT_FILENAMES[role],
    )
    cache_key = (role, font_dir, filename)
    if cache_key in _resolved_cache:
        return _resolved_cache[cache_key]
    path = None
    if font_dir:
        candidate = os.path.join(font_dir, filename)
        if os.path.exists(candidate):
            path = candidate
        elif role not in _warned:
            _warned.add(role)
            logger.warning(
                "Lettering font %r not found at %s — falling back to DejaVu. Lettering will "
                "render in a different face than a deployment with the configured font, so "
                "panels lettered now will not match panels lettered there.", role, candidate,
            )
    if path is None:
        if not font_dir and role not in _warned:
            _warned.add(role)
            logger.warning(
                "LETTERING_FONT_DIR is not set — lettering falls back to DejaVu. Set it to the "
                "directory holding the comic faces for output matching the rest of the book.",
            )
        path = _FALLBACK[role]
    _resolved_cache[cache_key] = path
    return path


def reset_font_cache():
    """Forget resolved faces (tests, and after a settings change)."""
    _resolved_cache.clear()
    _warned.clear()


# ---------------------------------------------------------------------------- style table
STYLES = {
    #             fill              outline            text              caps   font
    "bubble":   ((255, 255, 255),  (10, 10, 10),      (10, 10, 10),     True,  FONT_BOLD),
    "thought":  ((255, 255, 255),  (10, 10, 10),      (10, 10, 10),     False, FONT_REG),
    "caption":  ((248, 240, 214),  (40, 30, 18),      (28, 20, 10),     False, FONT_BOLD),
    "plaque":   ((16, 14, 22),     (212, 175, 55),    (236, 208, 130),  True,  FONT_BOLD),
    "ui":       ((14, 16, 26),     (44, 240, 224),    (150, 250, 235),  False, FONT_REG),
    # namecard is drawn by a dedicated two-zone branch; these are only the KeyError-safe colours.
    "namecard": ((250, 248, 242),  (28, 24, 20),      (30, 26, 22),     False, FONT_BOLD),
    # screen returns before any container is drawn, so fill/outline are never used.
    "screen":   ((0, 0, 0, 0),     (0, 0, 0, 0),      (16, 16, 16),     True,  FONT_BOLD),
}

CONTAINER_TYPES = tuple(STYLES.keys())

_OVAL = ("bubble", "thought")     # organic balloons; captions/plaques/ui stay boxes
_TAILED = ("bubble", "ui")        # types whose "tail" draws a pointed tail
SS = 2                            # silhouette supersampling -> anti-aliased balloon edges

# Every dimension below is RELATIVE to this reference width, so a spec lettered onto an upscaled
# plate reproduces the layout exactly — same wrap, same line count, same stroke weight, just
# larger. Absolute pixel sizes would make the type shrink into the balloon as the canvas grows.
REF_W = 768
FONT_MAX = 64                     # at REF_W; scaled by W / REF_W
FONT_MIN = 13
STROKE_DIV = 256                  # outline weight = W / STROKE_DIV

# Text is inset inside an oval so it never rides the outline. The largest rectangle that fits an
# ellipse is 0.707x0.707 of its axes; stay just under that.
_OVAL_INSET = {"bubble": (0.72, 0.68), "thought": (0.68, 0.62)}


def _font(path, size):
    """Load `path` at `size`, falling back to DejaVu bold then regular, then Pillow's default.

    Note on parity with the reference implementation: when the configured faces ARE present the
    two are byte-identical (verified on 326 real panels covering all seven containers). When they
    are absent they diverge — the reference tries its bold fallback first for every role, so it
    renders `thought` and `ui` in DejaVu Bold, while `font_path()` here hands back the role-correct
    fallback and they render in DejaVu Regular. This is the intended behaviour, not drift to fix:
    the fallback is a degraded path that already logs a warning, and honouring the requested weight
    is more correct than reproducing a quirk. Do not letter a real book on the fallback.
    """
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        for fb in (_FALLBACK[FONT_BOLD], _FALLBACK[FONT_REG]):
            try:
                return ImageFont.truetype(fb, size)
            except Exception:
                pass
        return ImageFont.load_default()


def _wrap(draw, text, font, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=font) <= max_w or not cur:
            cur = t
        else:
            lines.append(cur); cur = w
    if cur:
        lines.append(cur)
    return lines


def _line_h(font, size):
    return (font.getbbox("Ay")[3] - font.getbbox("Ay")[1]) + max(2, size // 6)


def _fit(draw, text, font_file, box_w, box_h, pad, max_size=FONT_MAX, min_size=FONT_MIN):
    """Largest font size whose wrapped text fits inside the box."""
    max_size, min_size = max(6, int(max_size)), max(4, int(min_size))
    for size in range(max_size, min_size - 1, -1):
        font = _font(font_file, size)
        lines = _wrap(draw, text, font, box_w - 2 * pad)
        lh = _line_h(font, size)
        if lh * len(lines) <= box_h - 2 * pad:
            return font, lines, lh
    font = _font(font_file, min_size)
    return font, _wrap(draw, text, font, box_w - 2 * pad), _line_h(font, min_size)


def _draw_block(d, text, font_file, zx, zy, zw, zh, col, scale, pad=None):
    """Auto-fit `text` and draw it centred inside the rectangle (zx, zy, zw, zh)."""
    if not text:
        return
    pad = pad if pad is not None else max(6, int(zw // 20))
    font, lines, lh = _fit(d, text, font_file, zw, zh, pad,
                           max_size=FONT_MAX * scale, min_size=FONT_MIN * scale)
    ty = zy + (zh - lh * len(lines)) // 2
    for i, line in enumerate(lines):
        lw = d.textlength(line, font=font)
        d.text((zx + (zw - lw) // 2, ty + i * lh), line, font=font, fill=col)


def _edge_point(cx, cy, a, b, ux, uy, oval):
    """Where the ray from the body centre along (ux, uy) leaves the body."""
    if oval:
        s = 1.0 / math.hypot(ux / a, uy / b)
    else:  # rectangle: first axis the ray crosses
        s = min(a / abs(ux) if ux else math.inf, b / abs(uy) if uy else math.inf)
    return cx + ux * s, cy + uy * s


def _cbez(p0, p1, p2, p3, n=16):
    """Cubic bezier samples — one edge of a tail: flare out, then pinch to the point."""
    out = []
    for i in range(n + 1):
        t = i / n
        m = 1 - t
        out.append((m * m * m * p0[0] + 3 * m * m * t * p1[0] + 3 * m * t * t * p2[0] + t * t * t * p3[0],
                    m * m * m * p0[1] + 3 * m * m * t * p1[1] + 3 * m * t * t * p2[1] + t * t * t * p3[1]))
    return out


def _qbez(p0, p1, p2, n=14):
    """Quadratic bezier samples. Retained for the thought-bubble dot trail."""
    out = []
    for i in range(n + 1):
        t = i / n
        m = 1 - t
        out.append((m * m * p0[0] + 2 * m * t * p1[0] + t * t * p2[0],
                    m * m * p0[1] + 2 * m * t * p1[1] + t * t * p2[1]))
    return out


def _tail_geometry(px, py, pw, ph, tx, ty, oval, ow):
    """Root anchored ON the balloon edge (sunk inside), tapering to the speaker's mouth.

    Returns (root_centre, unit_dir, perp, half_width, tip) or None when the target sits
    inside the body (nothing to point at).
    """
    cx, cy = px + pw / 2.0, py + ph / 2.0
    a, b = pw / 2.0, ph / 2.0
    dx, dy = tx - cx, ty - cy
    dist = math.hypot(dx, dy)
    if dist < 1e-6:
        return None
    ux, uy = dx / dist, dy / dist
    ex, ey = _edge_point(cx, cy, a, b, ux, uy, oval)
    reach = dist - math.hypot(ex - cx, ey - cy)  # visible tail length, edge -> mouth
    if reach <= 2 * ow:
        return None  # mouth is inside/at the balloon — a tail would be a stub
    # sink the root well inside the body so the union seam is buried under the fill
    sink = 3.0 * ow
    root = (ex - ux * sink, ey - uy * sink)
    # narrow, and never wider than the tail is long — a short tail must not read as a wedge
    half = max(4.0, min(min(pw, ph) * 0.10, reach * 0.13))
    return root, (ux, uy), (-uy, ux), half, (float(tx), float(ty))


# A letterer's tail is symmetric ABOUT ITS OWN AXIS: whatever profile one edge has, the other
# edge has the mirror of it. Offsetting BOTH edges by the same +n*bow bends the tail like a
# banana, one edge convex and the other concave, and because n = (-uy, ux) is always 90 degrees
# clockwise of travel the banana has a fixed handedness — a compass of directions then renders
# as a PINWHEEL (measured mirror error 14 to 23 px at every bearing).
# Sign-flipping the bow per quadrant does NOT fix it: straight up and straight down are their own
# mirror image, so the only symmetric bow there is zero, and the flip jumps 15 to 23 px as a tail
# crosses any cardinal, so two speakers a hair either side of directly below the balloon get tails
# bowing opposite ways. Offsets below are in ROOT HALF-WIDTHS, so the two edges can never cross
# and the shape is scale-free: flare wide leaving the balloon, which buries the union seam under
# the balloon wall, then pinch in to the point at the mouth.
_TAIL_FLARE = 0.35   # outward bow at 30% of the run, in root half-widths
_TAIL_PINCH = 0.45   # inward pull at 70% of the run

# Blambot's convention: the tail stops short and the eye continues the line to the mouth. Running
# it the full 100% buries tips in chins and chests. Authored `tail` coords stay AT the mouth; this
# shortens the drawn wedge only.
_TAIL_REACH = 0.62


def _tail_polygon(geo, inset):
    """Tail outline, shrunk by `inset` (0 = outer silhouette, ow = inner silhouette)."""
    (rx, ry), (ux, uy), (nx, ny), half, (tx, ty) = geo
    w = max(1.0, half - inset)
    # stop short of the authored mouth point, then pull back once more so the point gets a
    # stroke cap instead of a bare spike
    full = math.hypot(tx - rx, ty - ry)
    reach = full * _TAIL_REACH
    tip = (rx + ux * (reach - inset * 2.0), ry + uy * (reach - inset * 2.0))
    length = math.hypot(tip[0] - rx, tip[1] - ry)

    def edge(s):
        """One side of the axis; s = +1 / -1 give mirror-identical profiles."""
        p0 = (rx + s * nx * w, ry + s * ny * w)
        p1 = (p0[0] + ux * length * 0.30 + s * nx * _TAIL_FLARE * w,
              p0[1] + uy * length * 0.30 + s * ny * _TAIL_FLARE * w)
        p2 = (p0[0] + ux * length * 0.70 - s * nx * _TAIL_PINCH * w,
              p0[1] + uy * length * 0.70 - s * ny * _TAIL_PINCH * w)
        return _cbez(p0, p1, p2, tip)

    return edge(1.0) + list(reversed(edge(-1.0)))


def _scallops(px, py, pw, ph):
    """Bump circles around an ellipse -> the classic thought-cloud edge."""
    cx, cy = px + pw / 2.0, py + ph / 2.0
    a, b = pw / 2.0, ph / 2.0
    r = max(6.0, min(pw, ph) * 0.13)
    n = max(10, int(math.pi * (a + b) / (1.7 * r)))
    return [(cx + a * math.cos(2 * math.pi * i / n),
             cy + b * math.sin(2 * math.pi * i / n), r) for i in range(n)]


def _dot_trail(px, py, pw, ph, tx, ty):
    """Shrinking bubbles from the cloud toward the thinker."""
    cx, cy = px + pw / 2.0, py + ph / 2.0
    a, b = pw / 2.0, ph / 2.0
    dx, dy = tx - cx, ty - cy
    dist = math.hypot(dx, dy)
    if dist < 1e-6:
        return []
    ux, uy = dx / dist, dy / dist
    ex, ey = _edge_point(cx, cy, a, b, ux, uy, True)
    span = math.hypot(tx - ex, ty - ey)
    if span < 8:
        return []
    dots, base = [], max(6.0, min(pw, ph) * 0.10)
    for i, f in enumerate((0.30, 0.60, 0.86)):  # clear of the scalloped edge
        r = base * (1.0 - 0.28 * i)
        dots.append((ex + ux * span * f, ey + uy * span * f, r))
    return dots


def _silhouette(size, blobs, inset):
    """Rasterise every blob at SSx, unioned, shrunk by `inset` (in 1x px)."""
    W, H = size
    img = Image.new("L", (W * SS, H * SS), 0)
    d = ImageDraw.Draw(img)
    i = inset * SS
    for blob in blobs:
        kind = blob[0]
        if kind == "ellipse":
            _, x0, y0, x1, y1 = blob
            if x1 - x0 > 2 * i and y1 - y0 > 2 * i:
                d.ellipse([x0 * SS + i, y0 * SS + i, x1 * SS - i, y1 * SS - i], fill=255)
        elif kind == "rrect":
            _, x0, y0, x1, y1, r = blob
            if x1 - x0 > 2 * i and y1 - y0 > 2 * i:
                d.rounded_rectangle([x0 * SS + i, y0 * SS + i, x1 * SS - i, y1 * SS - i],
                                    radius=max(1, r * SS - i), fill=255)
        elif kind == "circle":
            _, cx, cy, r = blob
            rr = r * SS - i
            if rr > 0:
                d.ellipse([cx * SS - rr, cy * SS - rr, cx * SS + rr, cy * SS + rr], fill=255)
        elif kind == "tail":
            d.polygon([(x * SS, y * SS) for x, y in _tail_polygon(blob[1], inset)], fill=255)
    return img


def _body_and_edge(size, blobs, ow):
    """Anti-aliased (fill mask, outline mask) for the unioned silhouette."""
    outer = _silhouette(size, blobs, 0)
    inner = _silhouette(size, blobs, ow)
    edge = ImageChops.subtract(outer, inner)
    return (outer.resize(size, Image.LANCZOS), edge.resize(size, Image.LANCZOS))


def draw_overlay(base_path, elements, out_path):
    """Composite `elements` onto the plate at `base_path`, writing `out_path`.

    The source plate is opened read-only and never modified.
    """
    img = Image.open(base_path).convert("RGBA")
    W, H = img.size
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    scale = W / REF_W  # 1.0 at screen res; 3.0 on a 3x plate for print

    for el in elements:
        # Never paint a container with nothing in it, whichever caller built the element list.
        if not (el.get("text") or "").strip():
            continue
        t = el["type"]
        fill, outline, txt_col, caps, font_role = STYLES[t]
        font_file = font_path(font_role)
        x, y, w, h = el["box"]
        px, py, pw, ph = int(x * W), int(y * H), int(w * W), int(h * H)
        text = el["text"].upper() if caps else el["text"]
        oval = t in _OVAL
        ow = max(3, round(W / STROKE_DIV))
        r = min(pw, ph) // 5

        if t == "namecard":
            # Conference badge: kraft-white card, green header strip = NAME, descriptor below,
            # top clip. Text spec: "NAME|one-line descriptor".
            name, _, desc = el["text"].partition("|")
            body, edge = _body_and_edge((W, H), [("rrect", px, py, px + pw, py + ph, r)], ow)
            layer.paste((250, 248, 242), (0, 0), body)
            layer.paste((28, 24, 20), (0, 0), edge)
            d = ImageDraw.Draw(layer)
            inset = ow + max(2, ow // 2)
            strip_h = int(ph * 0.46)
            d.rounded_rectangle([px + inset, py + inset, px + pw - inset, py + strip_h],
                                radius=max(2, r - 4), fill=(20, 150, 120))
            cw, chh = max(8, pw // 9), max(6, ph // 10)
            d.rounded_rectangle([px + pw // 2 - cw // 2, py - chh // 2, px + pw // 2 + cw // 2, py + chh // 2],
                                radius=chh // 2, fill=(96, 96, 102), outline=(28, 24, 20), width=max(1, ow // 2))
            _draw_block(d, name.strip().upper(), font_path(FONT_BOLD), px + inset, py + inset,
                        pw - 2 * inset, strip_h - inset, (250, 250, 248), scale)
            _draw_block(d, desc.strip(), font_path(FONT_REG), px + inset, py + strip_h,
                        pw - 2 * inset, ph - strip_h - inset, (30, 26, 22), scale)
            continue

        if t == "screen":
            # No container at all: just the letters, centred in the box, on the art.
            d = ImageDraw.Draw(layer)
            font, lines, lh = _fit(d, text, font_file, pw, ph, max(6, pw // 20),
                                   max_size=FONT_MAX * scale, min_size=FONT_MIN * scale)
            total_h = lh * len(lines)
            ty0 = py + (ph - total_h) // 2
            for i, line in enumerate(lines):
                lw = d.textlength(line, font=font)
                d.text((px + (pw - lw) // 2, ty0 + i * lh), line, font=font, fill=txt_col)
            continue

        blobs = []
        if t == "thought":
            blobs.append(("ellipse", px, py, px + pw, py + ph))
            blobs += [("circle", cx, cy, rr) for cx, cy, rr in _scallops(px, py, pw, ph)]
            if el.get("tail"):
                tx, ty = el["tail"][0] * W, el["tail"][1] * H
                blobs += [("circle", cx, cy, rr) for cx, cy, rr in _dot_trail(px, py, pw, ph, tx, ty)]
        elif oval:
            blobs.append(("ellipse", px, py, px + pw, py + ph))
        else:
            blobs.append(("rrect", px, py, px + pw, py + ph, r))

        if el.get("tail") and t in _TAILED:
            tx, ty = el["tail"][0] * W, el["tail"][1] * H
            geo = _tail_geometry(px, py, pw, ph, tx, ty, oval, ow)
            if geo:
                blobs.append(("tail", geo))

        body, edge = _body_and_edge((W, H), blobs, ow)
        layer.paste(fill, (0, 0), body)
        layer.paste(outline, (0, 0), edge)

        d = ImageDraw.Draw(layer)
        if t == "plaque":  # inner gold hairline
            d.rounded_rectangle([px + ow + 4, py + ow + 4, px + pw - ow - 4, py + ph - ow - 4],
                                radius=max(2, r - 6), outline=outline, width=max(1, ow // 2))

        # text block, vertically centred on the body
        if oval:
            fx, fy = _OVAL_INSET[t]
            fit_w, fit_h, pad = int(pw * fx), int(ph * fy), ow + 2
        else:
            fit_w, fit_h, pad = pw, ph, max(10, pw // 18)
        font, lines, lh = _fit(d, text, font_file, fit_w, fit_h, pad,
                               max_size=FONT_MAX * scale, min_size=FONT_MIN * scale)
        total_h = lh * len(lines)
        ty0 = py + (ph - total_h) // 2
        for i, line in enumerate(lines):
            lw = d.textlength(line, font=font)
            d.text((px + (pw - lw) // 2, ty0 + i * lh), line, font=font, fill=txt_col)

    out = Image.alpha_composite(img, layer).convert("RGB")
    out.save(out_path)
    return out_path


# ---------------------------------------------------------------------------- validation
# A balloon may legitimately overhang the edge, so coordinates are not clamped to 0..1 — but a
# value orders of magnitude outside it is a unit error, not an intent.
MAX_BOX_SPAN = 4.0
MAX_COORD = 8.0


class LetteringError(ValueError):
    """A lettering spec that cannot be composited."""


def normalise_elements(lettering):
    """Coerce an `Action.lettering` value into a list of drawable elements, or raise.

    Accepts either a bare list of elements or {"elements": [...]}, so the field can grow
    sibling keys later without breaking stored specs.
    """
    if lettering in (None, "", [], {}):
        return []
    if isinstance(lettering, dict):
        lettering = lettering.get("elements", [])
    if not isinstance(lettering, list):
        raise LetteringError("lettering must be a list of elements, or {'elements': [...]}")

    out = []
    for i, el in enumerate(lettering):
        where = f"element {i}"
        if not isinstance(el, dict):
            raise LetteringError(f"{where}: expected an object, got {type(el).__name__}")
        t = el.get("type")
        if t not in STYLES:
            raise LetteringError(
                f"{where}: unknown type {t!r} (expected one of {', '.join(CONTAINER_TYPES)})")
        box = el.get("box")
        if not (isinstance(box, (list, tuple)) and len(box) == 4):
            raise LetteringError(f"{where}: 'box' must be [x, y, w, h] as fractions of the image")
        try:
            box = [float(v) for v in box]
        except (TypeError, ValueError):
            raise LetteringError(f"{where}: 'box' values must be numbers")
        # NaN and inf survive every comparison below (NaN <= 0 is False, inf <= 0 is False) and
        # reach Pillow as a ValueError/OverflowError deep inside the draw. Reject them here.
        if not all(math.isfinite(v) for v in box):
            raise LetteringError(f"{where}: 'box' values must be finite numbers")
        if box[2] <= 0 or box[3] <= 0:
            raise LetteringError(f"{where}: 'box' width and height must be positive")
        # Coordinates are FRACTIONS of the image, so a legitimate box is around 0..1. A wildly
        # large one is a unit mistake (pixels pasted into a fraction field), and it is not merely
        # wrong: `_scallops` scales its circle count with the box, so w=50000 on a 768px plate
        # builds millions of blobs and rasterises each one twice. That is an unbounded draw inside
        # a task with a two-hour soft limit, i.e. a wedged worker rather than an error.
        if box[2] > MAX_BOX_SPAN or box[3] > MAX_BOX_SPAN:
            raise LetteringError(
                f"{where}: 'box' spans {box[2]:.4g}x{box[3]:.4g} of the image; coordinates are "
                f"fractions (0..1), so anything above {MAX_BOX_SPAN} is a unit error"
            )
        if not all(abs(v) <= MAX_COORD for v in box[:2]):
            raise LetteringError(f"{where}: 'box' origin is far outside the image")
        tail = el.get("tail")
        if tail is not None:
            if not (isinstance(tail, (list, tuple)) and len(tail) == 2):
                raise LetteringError(f"{where}: 'tail' must be [x, y] or null")
            try:
                tail = [float(v) for v in tail]
            except (TypeError, ValueError):
                raise LetteringError(f"{where}: 'tail' values must be numbers")
            if not all(math.isfinite(v) for v in tail):
                raise LetteringError(f"{where}: 'tail' values must be finite numbers")
            if not all(abs(v) <= MAX_COORD for v in tail):
                raise LetteringError(f"{where}: 'tail' points far outside the image")
        text = el.get("text") or ""
        if not isinstance(text, str):
            raise LetteringError(f"{where}: 'text' must be a string")
        # An empty element is dropped rather than rejected: clearing the words of one balloon
        # while iterating is normal, and should not fail the whole panel.
        if not text.strip():
            continue
        out.append({"type": t, "text": text, "box": box, "tail": tail})
    return out
