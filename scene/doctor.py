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

# IMPERATIVE phrases: wording that only makes sense as an order to draw text. These are matched
# without any negation analysis, because they do not occur in a "do not do this" clause naturally.
#
# Bare NOUNS ("speech bubble", "caption box") are deliberately NOT here. They appear in the cure as
# often as the disease - the 2026-07-24 fix reads "Draw no speech bubbles, thought clouds, caption
# boxes or lettering of any kind" - and the clause-scoped negation check that used to compensate
# was wrong in both directions: it suppressed "Do not omit the caption box" (a real draw order,
# since the prompts are comma-joined lists of "NO ..." clauses) and it fired on "Speech bubbles:
# never draw them". Grepping for a disease that shares its vocabulary with the cure cannot be made
# reliable, so that whole mechanism is gone. `require_text_free_assertion` below carries the weight
# instead: it demands the cure rather than hunting every spelling of the disease.
LETTERING_DIRECTIVES = [
    "letter it into",
    "render legible text",
    "legible and well-placed",
    "exact text match",
    "render bubbles and captions",
    "gilded plaque",
    "monospace helpdesk card",
    "write the dialogue",
    "bake the chapter title",
    "hand-drawn lettering",
    "readable signage",
    "crisp typography",
]

# Wording that asserts the panel is delivered without words. A comic story whose image instructions
# contain NONE of these is not configured for text-free art, whatever else they say.
TEXT_FREE_ASSERTIONS = [
    "text-free",
    "text free",
    "no text",
    "never draw words",
    "draw no words",
    "without any text",
    "no lettering",
    "draw no speech bubbles",
]

_WS = re.compile(r"[\s\-]+")


def _normalised(text):
    """Lowercased with runs of whitespace and hyphens flattened to one space.

    Without this, "speech-bubble", "speech  bubble" and "speech\\nbubble" all slip a phrase match -
    and the Writer prompt in this very database says "speech-bubble placement".
    """
    return _WS.sub(" ", (text or "").lower())

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
    """Imperative lettering directives present in this text."""
    lowered = _normalised(text)
    return [d for d in LETTERING_DIRECTIVES if _normalised(d) in lowered]


def image_instruction_sources(story):
    """[(label, prompt_object)] for EVERY prompt that reaches an image generation.

    `agent.instructions` is only one of four channels. `Agent.get_instructions` returns
    `self.instructions.all() + Prompt.instructions(preset, obj)`, and that second call adds every
    global Prompt matching the preset, every global Prompt matching the object's ContentType, and
    the Scene's own `instructions` - all appended in the same last position that caused the
    2026-07-24 incident.

    Reading only the M2M made this validator blind to the channel most likely to carry the next
    one. The prompt that CAUSED that incident is still in this database, detached from every agent
    with `category='comic'`; setting `is_global=True` on it - one checkbox - re-arms it, and the
    old check reported the story safe.
    """
    from django.contrib.contenttypes.models import ContentType

    from agent.models import Agent, Prompt

    from .models import Action

    out = []
    for agent in Agent.objects.filter(output_type=Agent.OUTPUT_TYPE_IMAGE).prefetch_related("instructions"):
        for prompt in agent.instructions.all():
            out.append((f"image agent {agent.name!r}", prompt))

    for prompt in Prompt.objects.filter(is_global=True):
        out.append(("global prompt", prompt))

    try:
        ct = ContentType.objects.get_for_model(Action)
        for prompt in Prompt.objects.filter(content_types=ct):
            out.append(("prompt bound to Action", prompt))
    except Exception:
        pass

    for scene in story.scenes.all().prefetch_related("instructions"):
        for prompt in scene.instructions.all():
            out.append((f"scene {scene} instructions", prompt))

    seen, unique = set(), []
    for label, prompt in out:
        if prompt.pk in seen:
            continue
        seen.add(prompt.pk)
        unique.append((label, prompt))
    return unique


def check_image_agents_do_not_letter(story):
    """The 2026-07-24 failure mode itself, across every channel that reaches an image call.

    Only meaningful for a comic story: on a film, an image agent drawing a title card is fine.
    Guillermo appends instructions AFTER the panel prompt, so anything here overrides a per-panel
    "no text" clause rather than being overridden by it.
    """
    from agent.models import Agent

    findings = []
    if not story.is_comic:
        return findings

    if not Agent.objects.filter(output_type=Agent.OUTPUT_TYPE_IMAGE).exists():
        findings.append(Finding(
            WARN, "no-image-agent",
            "No agent with output type 'image' is configured, so nothing can draw a panel."))
        return findings

    for label, prompt in image_instruction_sources(story):
        hits = _matched_directives(prompt.prompt)
        if hits:
            findings.append(Finding(
                FAIL, "image-agent-letters",
                f"{label} carries instruction {prompt.name!r}, which tells the renderer to draw "
                f"text: {', '.join(repr(h) for h in hits)}. This is appended AFTER the panel "
                f"prompt, so it overrides any 'no text' clause and the words are baked into the "
                f"art. Move it to the text agent or delete it.",
                obj=prompt))
    return findings


def check_text_free_is_asserted(story):
    """A comic story must SAY somewhere that panels are delivered without words.

    This is the check that does the real work, and it is deliberately the opposite shape to the one
    above. Hunting for the disease can never be complete: the vocabulary is unbounded, it overlaps
    the cure, and a reviewer measured a dozen realistic wordings that a phrase list misses. Demand
    the cure instead - its absence is a single, decidable fact.
    """
    findings = []
    if not story.is_comic:
        return findings

    combined = " ".join(_normalised(p.prompt) for _label, p in image_instruction_sources(story))
    style = _normalised(story.style.prompt) if story.style else ""
    if not any(_normalised(a) in combined or _normalised(a) in style for a in TEXT_FREE_ASSERTIONS):
        findings.append(Finding(
            FAIL, "no-text-free-assertion",
            "This is a comic story, but nothing in the image agent's instructions or the Style "
            "says the panels are delivered text-free. Without that the model letters whatever the "
            "panel describes, and a baked balloon cannot be removed - it is part of the picture. "
            "Add an explicit text-free instruction before generating anything."))
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
    # There are TWO paid paths to a panel and they read different fields: `generate_image` uses
    # `prompt`, `generate_comic` uses `prompt_comic`. A comic story may use either - this book
    # generates text-free art through `generate_image` and composites the words afterwards, so its
    # `prompt_comic` is empty on 504 of 512 rows BY DESIGN.
    #
    # So the question is not "is the comic field filled in" but "is there art direction on ANY
    # path". Demanding `prompt_comic` on a book that does not use it produced 491 warnings on a
    # healthy story, which is how a validator gets ignored.
    # `cast` and `props` anchor a panel just as well as `actor` and `background` do. Counting only
    # the two FK fields would report a crowd panel built entirely from `cast` as having no visual
    # reference, which is both wrong and the kind of wrong that gets a validator ignored.
    actions = (Action.objects.filter(scene__story=story)
               .exclude(name__startswith=PARKED_PREFIX)
               .select_related("scene")
               .annotate(n_cast=Count("cast", distinct=True),
                         n_props=Count("props", distinct=True)))
    for action in actions:
        candidates = [(f, (getattr(action, f, None) or "").strip())
                      for f in ("prompt", "prompt_comic")]
        best = max(candidates, key=lambda kv: len(kv[1]))
        field, prompt = best
        if not prompt:
            findings.append(Finding(
                WARN, "empty-prompt",
                f"Action {str(action)!r} has neither a prompt nor a prompt_comic. Generating it "
                f"would spend money on an "
                f"image with no art direction.",
                obj=action))
        elif len(prompt) < MIN_USEFUL_PROMPT:
            findings.append(Finding(
                WARN, "stub-prompt",
                f"Action {str(action)!r} has a {len(prompt)}-character {field}, below the "
                f"{MIN_USEFUL_PROMPT} needed to direct an image. A render would likely be wasted.",
                obj=action))
        # A panel that already HAS art is not about to be generated, so "the renderer will invent
        # an anchor" is not a thing that can happen to it. Firing on 47 already-drawn panels is how
        # a validator earns 55 warnings on a healthy book and gets skipped - and it made --strict
        # exit non-zero on a clean story, so the CI gate this advertises was dead on arrival.
        if (action.image_id is None and action.image_comic_id is None
                and action.background_id is None and action.actor_id is None
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
    extra = []
    if isinstance(lettering, dict):
        # Sibling string values are drawn too - three rows in this book keep their caption in a
        # `chronicle` key - and reading only "elements" let those escape every house rule.
        extra = [(k, v) for k, v in lettering.items() if k != "elements" and isinstance(v, str) and v]
        lettering = lettering.get("elements", [])
    if not isinstance(lettering, list):
        return extra
    out = list(extra)
    for el in lettering:
        # A non-string `text` measured via str() counts its repr: ["a","b"] scores 24 characters.
        if isinstance(el, dict) and isinstance(el.get("text"), str) and el["text"]:
            out.append((el.get("type") or "?", el["text"]))
    return out


def check_onpage_text_house_rules(story):
    """Enforce the deployment's rules for on-page words, if it declared any.

    Checked against the LETTERING, not `Action.text`: the lettering elements are what the
    compositor draws, so they are what a reader sees. A rule enforced anywhere else is advisory.
    """
    from django.conf import settings

    from .models import Action

    # Coerced, not trusted. `LETTERING_MAX_CHARS = 110` (the obvious mistake) used to raise
    # AttributeError straight out of the admin action; a validator must not be the thing that
    # breaks the page.
    raw_banned = getattr(settings, "LETTERING_BANNED_CHARACTERS", DEFAULT_BANNED_CHARACTERS)
    banned = "".join(raw_banned) if isinstance(raw_banned, (str, list, tuple)) else ""
    raw_caps = getattr(settings, "LETTERING_MAX_CHARS", DEFAULT_MAX_CHARS)
    caps = raw_caps if isinstance(raw_caps, dict) else {}
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
    check_text_free_is_asserted,
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
