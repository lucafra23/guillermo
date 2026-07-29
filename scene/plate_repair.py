"""Repair two image-model artifacts for free, without re-generating the plate.

WHY THIS EXISTS
---------------
Both of these are generic failures of image generation, not quirks of any one story:

* **A baked letterbox matte.** A flat light band across an edge of the art, produced despite an
  explicit instruction not to. It is baked into the plate, so re-compositing text does not touch
  it. It was the single most-repeated complaint on a 500-panel book.
* **Baked stray text.** The model writes words into the picture in roughly one plate in five even
  when told not to. On a flat surface the word can be removed by resynthesising the surface.

A re-generation costs money AND returns different art, so a panel already approved for composition
and colour has to be re-approved. These repairs cost nothing and change only the defect.

DIMENSIONS ARE NEVER CHANGED. Lettering geometry is stored as fractions of width and height, so a
crop would silently move every balloon on the panel. Repairs repaint; they never resize.

THE GUARD THAT MATTERS
----------------------
"Flat and bright" describes a baked letterbox band. It also describes a pale sky above a horizon,
or a blank wall behind someone's head. On the first real run of the original tool over a finished
book, 7 of its 13 hits were real art - it wanted to repaint a desert sky and the backdrop behind a
character, one of them an hour after that panel had been re-rendered at cost. Applying it unchecked
would have destroyed all seven.

What separates them: a letterbox band is pasted on, so it meets the art at a hard discontinuity. A
sky blends into its horizon. Measured across those 13 plates the genuine mattes step 36-176 at the
boundary and every false positive stepped 1-24, so the classes separate cleanly and STEP_MIN sits
in open space between them. Do not "simplify" this check away.
"""
import numpy as np
from django.conf import settings
from PIL import Image

# ---------------------------------------------------------------- letterbox matte

# The colour a repaired band is filled with. A band filled with the page background vanishes when
# the page is viewed; the default suits a dark reader. Deployments with a light page should set
# PLATE_MATTE_FILL accordingly.
DEFAULT_MATTE_FILL = (10, 10, 15)

STD_MAX = 8.0        # a line is "flat" when its max per-channel std along its length is under this
LIGHT_MIN = 140      # ...and its mean brightness is over this (a light matte, not dark night art)
MIN_PX = 6           # ignore a 1-5px anti-alias fringe
MAX_FRAC = 0.45      # a flat light band bigger than this share of the axis is suspicious -> skip
STEP_MIN = 30.0      # ...and it must END in a discontinuity at least this big. See module docstring.
STEP_PX = 10         # how much art past the boundary is averaged to measure that step


def _matte_fill():
    return tuple(getattr(settings, "PLATE_MATTE_FILL", DEFAULT_MATTE_FILL))


def _run(mean, std, order):
    n = 0
    for i in order:
        if std[i] < STD_MAX and mean[i] > LIGHT_MIN:
            n += 1
        else:
            break
    return n


def _step(im, side, n):
    """Brightness discontinuity where a candidate band meets the art it abuts."""
    H, W, _ = im.shape
    if side == "top":
        band, art = im[:n], im[n:n + STEP_PX]
    elif side == "bot":
        band, art = im[H - n:], im[H - n - STEP_PX:H - n]
    elif side == "left":
        band, art = im[:, :n], im[:, n:n + STEP_PX]
    else:
        band, art = im[:, W - n:], im[:, W - n - STEP_PX:W - n]
    if art.size == 0:                      # band covers the whole axis; nothing to compare against
        return 0.0
    return abs(float(band.mean()) - float(art.mean()))


def analyse_matte(path):
    """(height, width, bands, skipped) - px of repairable band on each of the four sides.

    Every side is measured independently, because the artifact is often a full picture frame rather
    than just top and bottom. A side is only reported when it is flat, light, thicker than a
    fringe, smaller than MAX_FRAC of the axis, AND ends in a hard step.
    """
    im = np.asarray(Image.open(path).convert("RGB")).astype(np.int16)
    H, W, _ = im.shape
    rmean, rstd = im.mean(axis=(1, 2)), im.std(axis=1).max(axis=1)      # per-row
    cmean, cstd = im.mean(axis=(0, 2)), im.std(axis=0).max(axis=1)      # per-column
    raw = {"top": _run(rmean, rstd, range(H)), "bot": _run(rmean, rstd, range(H - 1, -1, -1)),
           "left": _run(cmean, cstd, range(W)), "right": _run(cmean, cstd, range(W - 1, -1, -1))}
    cap = {"top": MAX_FRAC * H, "bot": MAX_FRAC * H, "left": MAX_FRAC * W, "right": MAX_FRAC * W}
    bands, skip = {}, {}
    for k, v in raw.items():
        skip[k] = v > cap[k]
        soft = v >= MIN_PX and not skip[k] and _step(im, k, v) < STEP_MIN
        bands[k] = 0 if (v < MIN_PX or skip[k] or soft) else v
    return H, W, bands, skip


def repair_matte(src_path, dst_path, bands):
    """Repaint the flagged bands. Dimensions are preserved exactly."""
    arr = np.asarray(Image.open(src_path).convert("RGB")).copy()
    H, W = arr.shape[:2]
    fill = _matte_fill()
    if bands.get("top"):
        arr[:bands["top"]] = fill
    if bands.get("bot"):
        arr[H - bands["bot"]:] = fill
    if bands.get("left"):
        arr[:, :bands["left"]] = fill
    if bands.get("right"):
        arr[:, W - bands["right"]:] = fill
    out = Image.fromarray(arr)
    assert out.size == (W, H), "a repair must never change dimensions"
    out.save(dst_path)
    return out.size


# ---------------------------------------------------------------- baked stray text

RING = 14          # px of surrounding context sampled to model the background
FEATHER = 10       # px of soft edge inside the box, so the patch has no hard seam
FLAT_STD = 12.0    # ring std below which a paint-out reads as invisible
GRAIN_MAX = 5.0    # ceiling on synthesised grain, in levels


def _box_px(size, box):
    w, h = size
    x0, y0, x1, y1 = box
    return (int(round(x0 * w)), int(round(y0 * h)), int(round(x1 * w)), int(round(y1 * h)))


def _ring_pixels(a, x0, y0, x1, y1):
    h, w = a.shape[:2]
    oy0, ox0 = max(0, y0 - RING), max(0, x0 - RING)
    oy1, ox1 = min(h, y1 + RING), min(w, x1 + RING)
    mask = np.ones((oy1 - oy0, ox1 - ox0), bool)
    mask[y0 - oy0:y1 - oy0, x0 - ox0:x1 - ox0] = False
    return a[oy0:oy1, ox0:ox1][mask]


def flatness(path, box):
    """(is_flat, ring_mean, ring_std) for the surround of a box.

    The operator's "will this work?" probe. On a flat surface a paint-out is invisible; on halftone
    or detailed art it leaves a smooth patch that is more obvious than the text was, and the panel
    should be re-generated instead.
    """
    img = Image.open(path).convert("RGB")
    a = np.asarray(img).astype(np.float32)
    x0, y0, x1, y1 = _box_px(img.size, box)
    ring = _ring_pixels(a, x0, y0, x1, y1).reshape(-1, 3)
    std, mean = ring.std(axis=0), ring.mean(axis=0)
    return bool(std.max() < FLAT_STD), mean.round(1).tolist(), std.round(1).tolist()


def _grain(sub, mask, shape):
    """Grain matched to the surface's true NOISE, monochrome, and capped.

    Measuring the ring's raw spread instead would be dominated by the STRUCTURE being removed, not
    by noise: an early version of this replaced a legible word with a rainbow confetti rectangle,
    louder than the defect. So it measures the high-frequency residual over unmasked pixels only,
    and drives all three channels from one sample, so the grain is monochrome like film grain
    rather than chroma speckle no printed page has ever had.
    """
    ok = (~mask)[:, 1:] & (~mask)[:, :-1]
    if ok.sum() < 32:
        s = 1.0
    else:
        d = (sub[:, 1:] - sub[:, :-1]).mean(axis=2)[ok]
        s = float(np.std(d)) / np.sqrt(2.0)
    s = float(np.clip(s, 0.0, GRAIN_MAX))
    return np.repeat(np.random.normal(0, s, shape[:2])[:, :, None], shape[2], axis=2)


def _balloon_mask(a, x0, y0, x1, y1, grow=6):
    """Near-white, near-neutral pixels: a baked word balloon or plaque body on darker art."""
    sub = a[y0:y1, x0:x1]
    m = (sub.min(axis=2) > 200) & ((sub.max(axis=2) - sub.min(axis=2)) < 30)
    return _dilate(m, grow)


def _ink_mask(a, x0, y0, x1, y1, grow=2, k=0.42):
    """Dark glyph strokes on a lighter flat field - the commonest baked-text case."""
    sub = a[y0:y1, x0:x1]
    lum = sub.mean(axis=2)
    field = np.percentile(lum, 75)          # the surface the glyphs are printed on
    floor = float(lum.min())
    return _dilate(lum < (field - k * (field - floor)), grow)


def _glow_mask(a, x0, y0, x1, y1, grow=2, k=0.42):
    """Light glyph strokes on a darker flat field - screens, signage, lit plaques.

    Relative rather than absolute, because pale-cyan lettering on a dark navy panel never reaches
    the brightness a white balloon does.
    """
    sub = a[y0:y1, x0:x1]
    lum = sub.mean(axis=2)
    field = np.percentile(lum, 25)
    ceil = float(lum.max())
    return _dilate(lum > (field + k * (ceil - field)), grow)


def _dilate(m, grow):
    for _ in range(grow):
        g = m.copy()
        g[1:] |= m[:-1]
        g[:-1] |= m[1:]
        g[:, 1:] |= m[:, :-1]
        g[:, :-1] |= m[:, 1:]
        m = g
    return m


MASKS = {"balloon": _balloon_mask, "ink": _ink_mask, "glow": _glow_mask}


def erase(src_path, dst_path, box, mode="ink"):
    """Rebuild masked pixels from the nearest unmasked pixel in each of four directions.

    Structure running under the mask - a slat ceiling, a door frame, a screen bezel, a wall seam -
    continues across the patch instead of flattening to a slab, and only the masked silhouette is
    touched, so a face overlapping the box is left alone.

    Returns the number of pixels changed; 0 means the mask found nothing and nothing was written.
    """
    img = Image.open(src_path).convert("RGB")
    size = img.size
    a = np.asarray(img).astype(np.float32)
    x0, y0, x1, y1 = _box_px(size, box)
    sub = a[y0:y1, x0:x1]
    m = MASKS[mode](a, x0, y0, x1, y1)
    if not m.any():
        return 0
    h, w = m.shape
    acc, wsum = np.zeros_like(sub), np.zeros((h, w, 1), np.float32)
    for axis, flip in ((0, False), (0, True), (1, False), (1, True)):
        s = sub[::-1] if (axis == 0 and flip) else sub[:, ::-1] if (axis == 1 and flip) else sub
        mm = m[::-1] if (axis == 0 and flip) else m[:, ::-1] if (axis == 1 and flip) else m
        last = np.zeros((w, 3), np.float32) if axis == 0 else np.zeros((h, 3), np.float32)
        dist = np.full(last.shape[0], 1e6, np.float32)
        out = np.zeros_like(s)
        dst = np.zeros(s.shape[:2], np.float32)
        n = s.shape[0] if axis == 0 else s.shape[1]
        for i in range(n):
            row, mrow = (s[i], mm[i]) if axis == 0 else (s[:, i], mm[:, i])
            last[~mrow] = row[~mrow]
            dist[~mrow] = 0.0
            dist[mrow] += 1.0
            if axis == 0:
                out[i], dst[i] = last, dist
            else:
                out[:, i], dst[:, i] = last, dist
        if axis == 0 and flip:
            out, dst = out[::-1], dst[::-1]
        elif axis == 1 and flip:
            out, dst = out[:, ::-1], dst[:, ::-1]
        wgt = (1.0 / np.maximum(dst, 1.0) ** 2)[:, :, None]
        acc += out * wgt
        wsum += wgt
    fill = acc / np.maximum(wsum, 1e-6)
    fill = fill + _grain(sub, m, fill.shape)
    sub[m] = np.clip(fill, 0, 255)[m]
    a[y0:y1, x0:x1] = sub
    out_img = Image.fromarray(a.astype(np.uint8))
    assert out_img.size == size, "a repair must never change dimensions"
    out_img.save(dst_path)
    return int(m.sum())
