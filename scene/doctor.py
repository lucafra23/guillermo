"""Validate a story's configuration BEFORE anyone spends money generating from it.

WHY THIS EXISTS
---------------
On 2026-07-24, 118 of 357 plates in a 500-panel book (33% of it) came back with speech balloons,
plaques and caption boxes baked into the art, including a model-invented line of dialogue on page
one. None of it is removable: a baked balloon is part of the picture. The whole third had to be
paid for twice.

The cause was pure configuration drift, and it was invisible. The story was set up when one agent
both drew and lettered, which was reasonable at the time. The architecture later changed so art is
generated text-free and every word is composited afterwards - but that change was made to the
panel prompts and never to the AGENT CONFIG. The "letter it into the panel" instruction stayed
attached to the *image* agent, where guillermo appends instructions LAST, after the panel's own
"no text" clause. Nothing ever compared the two.

It survived for months because the finished pages have clean overlays composited on top, which
partially masks a baked balloon underneath.

So this is not a linter for tidiness. Every check here is something that silently converts money
into unusable art, and every one is cheap to run and cheap to fix BEFORE the spend.

WHAT IT IS NOT
--------------
It does not check that the writing is good, that panels are in a sensible order, or anything else
subjective. A check earns its place here only if a machine can be certain, because a validator
that cries wolf is a validator people learn to skip - which is exactly how the original drift
survived a code review.
"""
import re

from django.db.models import Count

# Phrases that must never reach the IMAGE agent on a comic story. Each one asks a text-free
# renderer to letter. This list is scar tissue: it is the wording that actually caused the
# 2026-07-24 incident, not a guess at what might.
LETTERING_DIRECTIVES = [
    "letter it into",
    "render legible text",
    "legible and well-placed",
    "exact text match",
    "render bubbles and captions",
    "into the panel",
    "gilded plaque",
    "monospace helpdesk card",
    "speech bubble",
    "caption box",
    "thought cloud",
]

# The same nouns appear in the CURE as in the disease. The fix for the 2026-07-24 incident was to
# add "Draw no speech bubbles, thought clouds, caption boxes or lettering of any kind" to the
# prompts - so a naive substring match flags the negative clause and reports the correctly
# configured story as broken. That is not a cosmetic bug: a validator that fires on a healthy book
# is one you learn to skip, which is how the original drift survived a review in the first place.
#
# So a hit only counts when it is NOT negated within its own clause. Clause, not whole prompt:
# "never draw a bubble. Render legible text" must still be caught.
NEGATION_CUES = (
    "no ", "not ", "never", "without", "avoid", "don't", "do not", "free of",
    "excluding", "omit", "refrain", "must not", "text-free",
)

_CLAUSE_BREAK = re.compile(r"[.;:\n]")


def _clause_before(text_lower, start):
    """The text from the previous clause break up to the match."""
    breaks = [m.end() for m in _CLAUSE_BREAK.finditer(text_lower, 0, start)]
    return text_lower[(breaks[-1] if breaks else 0):start]


def _is_negated(text_lower, start):
    return any(cue in _clause_before(text_lower, start) for cue in NEGATION_CUES)

# Below this, a prompt cannot direct an image well enough to be worth paying for. Chosen from the
# real corpus: genuine panel prompts in this book run 400-2000 characters, and the rows that came
# back unusable were the near-empty ones.
MIN_USEFUL_PROMPT = 120

# A row whose name opens with a bracketed tag - "[archive] ...", "[dead-legacy] ..." - is parked
# on purpose: kept for provenance, bound to nothing, never rendered. Complaining that a shelved
# panel has no prompt is the definition of a false alarm, and on this book it accounted for every
# single empty-prompt warning. Checks that ask "would rendering this waste money?" skip them,
# because nobody is going to render them. Checks about CONFIG (an agent told to letter) do not
# skip anything, since those affect the whole story.
PARKED_PREFIX = "["

# House rules for the words that end up ON THE PAGE. Both default to off, because they are
# editorial policy rather than facts about images - a platform must not invent a style guide. A
# deployment that has one declares it:
#
#     LETTERING_BANNED_CHARACTERS = "—–"      # em-dash, en-dash
#     LETTERING_MAX_CHARS = {"caption": 110}
#
# These exist because moving authoring out of spec files loses whatever the spec files enforced.
# Rules that were checked before every render become things you remember, and a rule you remember
# is a rule you break on the day you are busy.
DEFAULT_BANNED_CHARACTERS = ""
DEFAULT_MAX_CHARS = {}

FAIL = "FAIL"
WARN = "WARN"


class Finding:
    """One problem, with enough detail to act on without going looking."""

    __slots__ = ("severity", "code", "message", "obj")

    def __init__(self, severity, code, message, obj=None):
        self.severity = severity
        self.code = code
        self.message = message
        self.obj = obj

    def __str__(self):
        return f"{self.severity}  [{self.code}] {self.message}"

    def __repr__(self):
        return f"<Finding {self}>"


def _matched_directives(text):
    """Directives that appear as an INSTRUCTION TO DRAW, ignoring the ones being forbidden."""
    lowered = (text or "").lower()
    hits = []
    for directive in LETTERING_DIRECTIVES:
        for match in re.finditer(re.escape(directive), lowered):
            if not _is_negated(lowered, match.start()):
                hits.append(directive)
                break
    return hits


def check_image_agents_do_not_letter(story):
    """The 2026-07-24 failure mode itself.

    Only meaningful for a comic story: on a film, an image agent drawing a title card is fine.
    Guillermo appends agent instructions AFTER the panel prompt, so an instruction here overrides
    a per-panel "no text" clause rather than being overridden by it.
    """
    from agent.models import Agent

    findings = []
    if not story.is_comic:
        return findings

    agents = Agent.objects.filter(output_type=Agent.OUTPUT_TYPE_IMAGE).prefetch_related("instructions")
    if not agents.exists():
        findings.append(Finding(
            WARN, "no-image-agent",
            "No agent with output type 'image' is configured, so nothing can draw a panel."))
        return findings

    for agent in agents:
        for prompt in agent.instructions.all():
            hits = _matched_directives(prompt.prompt)
            if hits:
                findings.append(Finding(
                    FAIL, "image-agent-letters",
                    f"Image agent {agent.name!r} carries instruction {prompt.name!r}, which tells "
                    f"it to draw text: {', '.join(repr(h) for h in hits)}. Guillermo appends this "
                    f"AFTER the panel prompt, so it overrides any 'no text' clause and the words "
                    f"are baked into the art. Move it to the text agent or delete it.",
                    obj=prompt))
    return findings


def check_style_does_not_letter(story):
    """The global Style reaches every single panel, so it is the widest-blast-radius prompt there is."""
    findings = []
    if not story.is_comic or not story.style:
        return findings
    hits = _matched_directives(story.style.prompt)
    if hits:
        findings.append(Finding(
            FAIL, "style-letters",
            f"Style {story.style.name!r} tells the renderer to draw text: "
            f"{', '.join(repr(h) for h in hits)}. The Style is attached to every panel in the "
            f"story, so this bakes words into all of them.",
            obj=story.style))
    return findings


def check_referenced_entities_are_armed(story):
    """A referenced entity with no reference image injects only its NAME into the panel call.

    There is no error and no warning: the panel generates, it just quietly stops looking like
    itself, and consistency collapses across the book. Only entities actually referenced by a
    panel are checked - an unused row costs nothing.
    """
    from .models import Action

    findings = []
    referenced = {}

    for action in (Action.objects.filter(scene__story=story)
                   .exclude(name__startswith=PARKED_PREFIX)
                   .select_related("background", "actor")
                   .prefetch_related("cast", "props")):
        for entity in [action.background, action.actor]:
            if entity is not None:
                referenced.setdefault((type(entity).__name__, entity.pk), [entity, 0])[1] += 1
        for entity in list(action.cast.all()) + list(action.props.all()):
            referenced.setdefault((type(entity).__name__, entity.pk), [entity, 0])[1] += 1

    for (kind, _pk), (entity, uses) in sorted(referenced.items(), key=lambda kv: str(kv[1][0])):
        if not entity.image_id:
            findings.append(Finding(
                FAIL, "unarmed-reference",
                f"{kind} {str(entity)!r} is referenced by {uses} panel(s) but has no reference "
                f"image. Only its name is sent to the renderer, so it will look different every "
                f"time it appears. Generate its reference image before rendering those panels.",
                obj=entity))
    return findings


def check_no_duplicate_names(story):
    """The Writer agent creates entities with update_or_create BY NAME.

    Two rows with the same name means half the panels bind to one and half to the other, each with
    its own reference image, so a character silently becomes two people.
    """
    from .models import Action, Background, Character, Prop

    findings = []
    for model, label, qs in (
            (Character, "Character", Character.objects.filter(story=story)),
            (Background, "Background", Background.objects.filter(story=story)),
            (Prop, "Prop", Prop.objects.filter(story=story)),
            (Action, "Action", Action.objects.filter(scene__story=story)
                                     .exclude(name__startswith=PARKED_PREFIX)),
    ):
        dupes = (qs.values("name").annotate(n=Count("id")).filter(n__gt=1).order_by("name"))
        for row in dupes:
            if not row["name"]:
                continue
            findings.append(Finding(
                FAIL, "duplicate-name",
                f"{label} {row['name']!r} exists {row['n']} times in this story. References bind "
                f"by name, so panels will split between the copies and pick up whichever "
                f"reference image each one carries. Merge them.",
                obj=None))
    return findings


def check_panels_can_be_rendered(story):
    """Panels that would spend money and return something unusable."""
    from .models import Action

    findings = []
    # `cast` and `props` anchor a panel just as well as `actor` and `background` do. Counting only
    # the two FK fields would report a crowd panel built entirely from `cast` as having no visual
    # reference, which is both wrong and the kind of wrong that gets a validator ignored.
    actions = (Action.objects.filter(scene__story=story)
               .exclude(name__startswith=PARKED_PREFIX)
               .select_related("scene")
               .annotate(n_cast=Count("cast", distinct=True),
                         n_props=Count("props", distinct=True)))
    for action in actions:
        prompt = (action.prompt or "").strip()
        if not prompt:
            findings.append(Finding(
                WARN, "empty-prompt",
                f"Action {str(action)!r} has no prompt. Generating it would spend money on an "
                f"image with no art direction.",
                obj=action))
        elif len(prompt) < MIN_USEFUL_PROMPT:
            findings.append(Finding(
                WARN, "stub-prompt",
                f"Action {str(action)!r} has a {len(prompt)}-character prompt, below the "
                f"{MIN_USEFUL_PROMPT} needed to direct an image. A render would likely be wasted.",
                obj=action))
        if (action.background_id is None and action.actor_id is None
                and not action.n_cast and not action.n_props):
            findings.append(Finding(
                WARN, "no-anchor",
                f"Action {str(action)!r} references no background, actor, cast or prop, so the "
                f"renderer has no visual anchor and will invent one. It will not match the rest "
                f"of the story.",
                obj=action))
    return findings


def _onpage_elements(action):
    """[(type, text)] for the words that will actually be drawn on this panel."""
    lettering = action.lettering
    if isinstance(lettering, dict):
        lettering = lettering.get("elements", [])
    if not isinstance(lettering, list):
        return []
    out = []
    for el in lettering:
        if isinstance(el, dict) and el.get("text"):
            out.append((el.get("type") or "?", str(el["text"])))
    return out


def check_onpage_text_house_rules(story):
    """Enforce the deployment's rules for on-page words, if it declared any.

    Checked against the LETTERING, not `Action.text`: the lettering elements are what the
    compositor draws, so they are what a reader sees. A rule enforced anywhere else is advisory.
    """
    from django.conf import settings

    from .models import Action

    banned = getattr(settings, "LETTERING_BANNED_CHARACTERS", DEFAULT_BANNED_CHARACTERS) or ""
    caps = getattr(settings, "LETTERING_MAX_CHARS", DEFAULT_MAX_CHARS) or {}
    if not banned and not caps:
        return []

    findings = []
    for action in (Action.objects.filter(scene__story=story)
                   .exclude(name__startswith=PARKED_PREFIX)
                   .exclude(lettering__isnull=True)):
        for kind, text in _onpage_elements(action):
            hits = sorted({ch for ch in banned if ch in text})
            if hits:
                findings.append(Finding(
                    FAIL, "banned-character",
                    f"Action {str(action)!r} has {', '.join(repr(h) for h in hits)} in its on-page "
                    f"{kind} text. This deployment does not allow it on the page: {text[:70]!r}",
                    obj=action))
            cap = caps.get(kind)
            if cap and len(text) > cap:
                findings.append(Finding(
                    FAIL, "onpage-too-long",
                    f"Action {str(action)!r} has a {len(text)}-character {kind}, over this "
                    f"deployment's limit of {cap}. It will crowd the art: {text[:70]!r}",
                    obj=action))
    return findings


CHECKS = (
    check_image_agents_do_not_letter,
    check_style_does_not_letter,
    check_referenced_entities_are_armed,
    check_no_duplicate_names,
    check_panels_can_be_rendered,
    check_onpage_text_house_rules,
)


def check_story(story):
    """Run every check. Returns a list of Finding, most severe first."""
    findings = []
    for check in CHECKS:
        findings.extend(check(story))
    return sorted(findings, key=lambda f: (f.severity != FAIL, f.code))


def summarise(findings):
    """(n_fail, n_warn, one-line verdict)."""
    fails = sum(1 for f in findings if f.severity == FAIL)
    warns = len(findings) - fails
    if fails:
        verdict = f"NOT SAFE TO RENDER: {fails} failure(s), {warns} warning(s)."
    elif warns:
        verdict = f"Safe to render, with {warns} warning(s)."
    else:
        verdict = "Clean. Safe to render."
    return fails, warns, verdict
