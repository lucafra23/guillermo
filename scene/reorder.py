"""Move panels around, and leave the numbering clean afterwards.

WHY THIS EXISTS

Reading order is `Action.order`, an integer typed by hand. A revision pass is full of "move this
panel before that one" -- one recorded sweep on a real book was fourteen such moves -- and the
only way to perform one is to work out the integers yourself and retype them, in scenes of thirty
to forty-seven panels.

Worse, nothing keeps those integers distinct. Measured on that book: 604 panels hold only 530
distinct (scene, order) pairs -- 61 groups covering 135 panels share a number with a sibling, so
their relative order is whatever the database happens to return. Renumbering a scene as a side
effect of moving one panel is therefore a repair, not just tidiness.

WHAT IT GUARANTEES

- The destination scene ends up numbered 0..n-1 with no gaps and no ties.
- Panels not being moved keep their relative order.
- Moving across scenes carries the panel to the new scene and renumbers both ends.
- Nothing is written unless the whole move can be written: one transaction.
"""
import logging

from django.db import transaction

logger = logging.getLogger(__name__)

BEFORE = "before"
AFTER = "after"


class ReorderError(Exception):
    """The move cannot be performed, with a reason the author can act on."""


def _ordered(scene, exclude_ids=()):
    """Panels of a scene in their current reading order, ties broken by id.

    `id` is the tie-break because it is the only field that cannot itself be tied, and it
    reflects creation order, which is the closest thing to an intended sequence that exists
    when two panels claim the same number.
    """
    from scene.models import Action

    qs = Action.objects.filter(scene=scene).order_by("order", "id")
    return [a for a in qs if a.pk not in set(exclude_ids)]


def _write(panels):
    """Assign 0..n-1 in list order. Returns how many rows actually changed."""
    from scene.models import Action

    changed = []
    for index, panel in enumerate(panels):
        if panel.order != index:
            panel.order = index
            changed.append(panel)
    if changed:
        Action.objects.bulk_update(changed, ["order"])
    return len(changed)


@transaction.atomic
def move(panels, target, where=BEFORE):
    """Move `panels` so they sit immediately before/after `target`. Returns (moved, renumbered).

    `panels` keep their own relative order, which is what makes moving a run of panels one
    action rather than several.
    """
    if where not in (BEFORE, AFTER):
        raise ReorderError(f"Unknown position {where!r}; expected {BEFORE!r} or {AFTER!r}.")
    panels = list(panels)
    if not panels:
        raise ReorderError("Nothing selected to move.")
    if target is None:
        raise ReorderError("Choose the panel to move these next to.")
    if target.pk in {p.pk for p in panels}:
        raise ReorderError(
            f"{target.name!r} is one of the panels being moved, so there is nothing to move it "
            f"next to. Pick a panel that is staying put.")
    if target.scene_id is None:
        raise ReorderError(f"{target.name!r} is not in a scene, so it has no position to move to.")

    source_scenes = {p.scene_id for p in panels if p.scene_id and p.scene_id != target.scene_id}

    # Order the moved run the way it currently reads, so a multi-panel move preserves its shape.
    panels.sort(key=lambda p: (p.order if p.order is not None else 0, p.pk))

    remaining = _ordered(target.scene, exclude_ids=[p.pk for p in panels])
    try:
        at = next(i for i, p in enumerate(remaining) if p.pk == target.pk)
    except StopIteration:                       # target was in the moved set's old scene only
        raise ReorderError(f"{target.name!r} is no longer in its scene; reload and try again.")

    insert_at = at if where == BEFORE else at + 1
    for panel in panels:
        panel.scene = target.scene
    new_order = remaining[:insert_at] + panels + remaining[insert_at:]

    from scene.models import Action
    moved_scene = [p for p in panels if p.pk]
    if moved_scene:
        Action.objects.bulk_update(moved_scene, ["scene"])
    renumbered = _write(new_order)

    # A panel that left another scene leaves a hole behind; close it.
    for scene_id in source_scenes:
        from scene.models import Scene
        scene = Scene.objects.filter(pk=scene_id).first()
        if scene:
            renumbered += _write(_ordered(scene))

    return len(panels), renumbered


@transaction.atomic
def renumber(scene):
    """Give one scene a clean 0..n-1 sequence. Returns how many panels changed number."""
    if scene is None:
        raise ReorderError("No scene to renumber.")
    return _write(_ordered(scene))


def ties(scene):
    """Panels in this scene that share a number with a sibling."""
    from collections import Counter

    panels = _ordered(scene)
    counts = Counter(p.order for p in panels)
    return [p for p in panels if counts[p.order] > 1]
