"""Keep a character looking like themselves across a whole book.

THE PROBLEM THIS SOLVES

Comic panels are generated from `Action.prompt_comic` alone. `Action.get_contents()` adds the
actor, cast, props and background for every other preset and deliberately skips them for
PRESET_COMIC, so nothing about who is IN the panel reaches the model except whatever the author
retyped into that panel's prompt.

Over a handful of panels that is fine. Over a few hundred it is the reason a character's hair,
build or clothing drifts between pages: the description exists, in `Character.prompt`, and is
simply never sent. The production repo this app was built for works around it by keeping a
hand-maintained file of canonical character descriptions and pasting them into every render --
the highest-churn file in that repo, and pure duplication of data the database already holds.

WHY IT IS OFF BY DEFAULT

Turning it on changes what is sent to a paid image model for every comic generation, and the
existing behaviour is what current books were drawn against. A consistency fix that silently
re-renders a book differently is not a fix. So it is opt-in, per deployment, and the default
leaves the prompt byte-identical.

`text` sends the canonical description. `text+image` also attaches the character's reference
plate, which is stronger and costs more per call.
"""
import logging

from django.conf import settings

logger = logging.getLogger(__name__)

OFF = "off"
TEXT = "text"
TEXT_AND_IMAGE = "text+image"
MODES = (OFF, TEXT, TEXT_AND_IMAGE)


def mode():
    """How much character context a comic generation should carry."""
    value = str(getattr(settings, "COMIC_CHARACTER_CONTEXT", OFF) or OFF).strip().lower()
    if value not in MODES:
        logger.warning(
            "COMIC_CHARACTER_CONTEXT=%r is not one of %s; treating it as %r",
            value, ", ".join(MODES), OFF)
        return OFF
    return value


def _describe(character):
    """The canonical description of one character, or None if they have none.

    `Character.prompt` is what the character's own portrait was generated from, which makes it
    the description of record. A character with an empty prompt contributes nothing rather than
    an empty line: padding a paid prompt with blanks is worse than sending less.
    """
    text = (getattr(character, "prompt", "") or "").strip()
    if not text:
        return None
    name = (getattr(character, "name", "") or "").strip()
    return f"Character {name}: {text}" if name else f"Character: {text}"


def contents_for(action):
    """Extra prompt parts that keep this panel's cast on-model. Empty unless switched on.

    Order is deliberate: the actor first, then the rest of the cast in a stable order, so two
    renders of the same panel send the same prompt. `cast.all()` without an ordering is free to
    come back differently between calls, which would make an identical panel produce a
    different image and look like model noise.
    """
    how = mode()
    if how == OFF:
        return []

    people = []
    actor = getattr(action, "actor", None)
    if actor is not None:
        people.append(actor)
    try:
        people += [c for c in action.cast.all().order_by("pk") if c != actor]
    except Exception:
        logger.debug("Could not read the cast for action %s", getattr(action, "pk", "?"),
                     exc_info=True)

    parts = []
    for person in people:
        described = _describe(person)
        if described:
            parts.append(described)
        if how == TEXT_AND_IMAGE:
            image = getattr(person, "image", None)
            if image:
                parts.append(image)
    if parts:
        # Said once, not per character: the instruction is about the list that follows.
        parts.insert(0, "Keep these characters consistent with the descriptions given:")
    return parts
