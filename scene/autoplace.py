"""Propose where a panel's balloons should sit, so placing them stops being hand labour.

WHY THIS EXISTS
---------------
Hand-positioning overlays is the only cost in a comic pipeline that is strictly linear in panel
count. On a 500-panel book it is the work that decides whether a revision happens.

HOW IT DECIDES "EMPTY"
----------------------
A plate's gradient energy. Faces, hands, linework and deliberately reserved negative space differ
enormously in local gradient magnitude, and a panel prompt that asks for a clean low-detail upper
third produces exactly that. Minimising mean energy inside a candidate box therefore lands the
balloon in the space the prompt reserved for it.

No face detector is needed, and that is the point rather than a shortcut: a face IS high energy,
so it is avoided by construction. A detector would add a dependency, a model download, and a new
way to be confidently wrong about a stylised drawing.

WHAT IT DOES NOT DECIDE
-----------------------
The tail target. Which character is speaking is an authorial fact, not a geometric one. Given a
tail, this keeps the tail's path off busy art and sits the balloon over the speaker; it will never
invent one.

IT PROPOSES, THE FIELD STORES. Nothing here runs at render time. Suggestions are written into
`Action.lettering` as ordinary boxes, so the composite stays deterministic and a human can
override any of them - the letterer never guesses.
"""
import numpy as np
from PIL import Image, ImageDraw

from .lettering import (
    FONT_MIN, REF_W, STROKE_DIV, STYLES, _OVAL, _OVAL_INSET, _font, _line_h, _wrap,
)

GRID_W, GRID_H = 96, 168          # energy grid; ~8px cells on a 768x1344 plate
MARGIN = 0.025                    # keep boxes off the panel edge

# type -> (default width fraction, default zone for the box top, default font at REF_W)
DEFAULTS = {
    "bubble":  (0.84, (0.02, 0.34), 34),
    "thought": (0.70, (0.04, 0.40), 30),
    "caption": (0.92, (0.72, 0.80), 30),
    "plaque":  (0.80, (0.30, 0.70), 30),
    "ui":      (0.74, (0.15, 0.72), 30),
}


def energy_grid(img):
    """Mean gradient magnitude per cell, plus its integral image for O(1) box queries."""
    g = np.asarray(img.convert("L"), dtype=np.float32)
    gx = np.abs(np.diff(g, axis=1, prepend=g[:, :1]))
    gy = np.abs(np.diff(g, axis=0, prepend=g[:1, :]))
    e = gx + gy
    H, W = e.shape
    ys = (np.arange(H) * GRID_H // H).clip(0, GRID_H - 1)
    xs = (np.arange(W) * GRID_W // W).clip(0, GRID_W - 1)
    acc = np.zeros((GRID_H, GRID_W), np.float64)
    cnt = np.zeros((GRID_H, GRID_W), np.float64)
    np.add.at(acc, (ys[:, None], xs[None, :]), e)
    np.add.at(cnt, (ys[:, None], xs[None, :]), 1.0)
    cell = acc / np.maximum(cnt, 1)
    cell /= max(cell.max(), 1e-6)                       # normalise 0..1
    integral = np.pad(cell.cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    return cell, integral


def box_energy(integral, x0, y0, x1, y1):
    """Mean cell energy in a grid-space box [x0,x1) x [y0,y1)."""
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(GRID_W, x1), min(GRID_H, y1)
    if x1 <= x0 or y1 <= y0:
        return 1.0
    total = integral[y1, x1] - integral[y0, x1] - integral[y1, x0] + integral[y0, x0]
    return total / ((y1 - y0) * (x1 - x0))


def path_energy(cell, p0, p1, skip_tail=0.28):
    """Energy along the tail, ignoring the last stretch - that part must land ON the speaker."""
    n = 24
    cut = int(n * (1 - skip_tail))
    vals = []
    for i in range(cut):
        t = i / max(n - 1, 1)
        gx = int((p0[0] + (p1[0] - p0[0]) * t) * GRID_W)
        gy = int((p0[1] + (p1[1] - p0[1]) * t) * GRID_H)
        vals.append(cell[min(max(gy, 0), GRID_H - 1), min(max(gx, 0), GRID_W - 1)])
    return float(np.mean(vals)) if vals else 0.0


def box_size(el, W, H, width_frac, font_px):
    """Box dims (fractions) that hold this text at `font_px`, honouring the oval inset.

    Measured with the SAME wrapping and metrics the compositor uses, so a proposal is a box the
    text actually fits in rather than an estimate that overflows at render time.
    """
    t = el["type"]
    _fill, _out, _txt, caps, font_path = STYLES[t]
    text = (el.get("text") or "")
    text = text.upper() if caps else text
    pw = int(width_frac * W)
    ow = max(3, round(W / STROKE_DIV))
    oval = t in _OVAL
    fx, fy = _OVAL_INSET[t] if oval else (1.0, 1.0)
    pad = (ow + 2) if oval else max(10, pw // 18)

    d = ImageDraw.Draw(Image.new("L", (1, 1)))
    font = _font(font_path, font_px)
    lines = _wrap(d, text, font, int(pw * fx) - 2 * pad)
    lh = _line_h(font, font_px)
    ph = int((lh * len(lines) + 2 * pad) / fy)
    return pw / W, ph / H


def overlaps(a, b, slack=0.004):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return not (ax + aw + slack <= bx or bx + bw + slack <= ax or
                ay + ah + slack <= by or by + bh + slack <= ay)


def propose(el, img, cell, integral, taken):
    """Lowest-cost non-colliding box for this element, or None if the text cannot fit."""
    W, H = img.size
    t = el["type"]
    hint = el.get("place") or {}
    dw, dzone, dfont = DEFAULTS[t]
    width_frac = hint.get("width", dw)
    zone = tuple(hint.get("zone", dzone))
    font_px = hint.get("font", dfont) * (W / REF_W)

    bw, bh = box_size(el, W, H, width_frac, max(FONT_MIN, int(font_px)))
    if bh > 0.9:
        return None                        # the text simply does not fit at this width

    xz0, xz1 = hint.get("xzone", (MARGIN, 1 - MARGIN))
    tail = el.get("tail")
    best, best_cost = None, float("inf")
    for xi in range(0, 33):
        x = xz0 + (xz1 - xz0 - bw) * xi / 32
        if x < 0 or x + bw > 1 - MARGIN:
            break
        for yi in range(0, 49):
            y = zone[0] + (zone[1] - zone[0]) * yi / 48
            if y < MARGIN or y + bh > 1 - MARGIN:
                continue
            box = [x, y, bw, bh]
            if any(overlaps(box, o) for o in taken):
                continue
            gx0, gy0 = int(x * GRID_W), int(y * GRID_H)
            gx1, gy1 = int((x + bw) * GRID_W), int((y + bh) * GRID_H)
            cost = 3.0 * box_energy(integral, gx0, gy0, gx1, gy1)
            cost += 0.15 * y                        # gentle bias: balloons read better high
            if tail:
                cx, cy = x + bw / 2, y + bh
                cost += 2.0 * path_energy(cell, (cx, cy), tuple(tail))
                cost += 0.9 * abs(cx - tail[0])     # over the speaker, not across the panel
            if cost < best_cost:
                best, best_cost = box, cost
    if best is None:
        return None
    return [round(float(v), 4) for v in best], float(best_cost)


def suggest_for_action(action, only_missing=True):
    """Propose boxes for an Action's lettering elements. Returns (lettering, n_placed, n_failed).

    The first element of the return value is a NEW value in the SAME SHAPE as `action.lettering`
    was - nothing is saved here, so a caller can preview.

    Shape matters. `lettering` may be a bare list OR `{"elements": [...]}` with sibling keys, and
    three rows in this book's database carry a `chronicle` key alongside their elements. Returning
    a bare list for a wrapped input and letting the caller save it would silently delete those
    siblings - destroying authored content while claiming to have placed a balloon.
    """
    from .lettering import normalise_elements

    elements = normalise_elements_lenient(action.lettering)
    wrapper = action.lettering if isinstance(action.lettering, dict) else None

    def shaped(els):
        """Put the elements back in the shape they arrived in."""
        if wrapper is None:
            return els
        out = dict(wrapper)
        out["elements"] = els
        return out

    if not elements or not action.image:
        return shaped(elements), 0, 0

    with Image.open(action.image.path) as img:
        img = img.convert("RGB")
        cell, integral = energy_grid(img)

        taken = [el["box"] for el in elements
                 if only_missing and isinstance(el.get("box"), (list, tuple)) and len(el["box"]) == 4]
        placed = failed = 0
        out, newly_placed = [], []
        for el in elements:
            el = dict(el)
            has_box = isinstance(el.get("box"), (list, tuple)) and len(el["box"]) == 4
            if only_missing and has_box:
                out.append(el)
                continue
            if el.get("type") not in DEFAULTS:
                out.append(el)
                failed += 1
                continue
            result = propose(el, img, cell, integral, taken)
            if result is None:
                out.append(el)
                failed += 1
                continue
            box, _cost = result
            el["box"] = box
            taken.append(box)
            placed += 1
            out.append(el)
            newly_placed.append(el)

    # Every box this produced must survive the compositor's own validation, or "suggest" would
    # write specs that cannot be rendered. Only what WE placed is checked: an element that could
    # not be positioned still has no box, and rejecting the whole panel because one line was too
    # long to fit would throw away the placements that did work.
    if newly_placed:
        normalise_elements(newly_placed)
    return shaped(out), placed, failed


def normalise_elements_lenient(lettering):
    """Like `normalise_elements`, but tolerates a MISSING box - which is the whole input here.

    The compositor rightly refuses an element with no box. This tool exists precisely to fill that
    in, so it needs to read the spec before it is renderable.
    """
    if lettering in (None, "", [], {}):
        return []
    if isinstance(lettering, dict):
        lettering = lettering.get("elements", [])
    if not isinstance(lettering, list):
        return []
    return [dict(el) for el in lettering if isinstance(el, dict)]
