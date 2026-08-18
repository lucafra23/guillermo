"""A cumulative ceiling on what an instance may spend on image generation.

#9 confirms one batch at a time, and a per-batch confirmation cannot see a habit. Ten
confirmed batches of ten are a hundred images and nobody was ever asked about the hundred.
This reads the ledger the app already keeps and refuses once the total passes a figure the
deployment set in advance, which is the only moment the question can be answered calmly.

It is OFF unless IMAGE_SPEND_CAP is set, because a cap is a budget and this project cannot
know anyone's. When it IS set, every path below fails CLOSED: an unreadable ledger, an
unpriceable generation, or a cap that cannot be evaluated all refuse the spend rather than
allowing it. A cap that cannot be read is not a cap that passed.
"""
import logging

from django.conf import settings

logger = logging.getLogger(__name__)


def _conf(name, default):
    return getattr(settings, name, default)


def images_generated():
    """Paid image generations recorded in the ledger, or None if that cannot be read.

    TokenUsage is written by Agent.save_usage on every returned generation, so counting its
    image rows counts generations that actually completed -- which is what was billed. A
    refusal that never produced a response writes no row and is not counted, correctly.
    """
    try:
        from agent.models import Agent, TokenUsage
        return TokenUsage.objects.filter(agent__output_type=Agent.OUTPUT_TYPE_IMAGE).count()
    except Exception:
        # Never re-raised: this runs on the path that spends money, and an exception here
        # must not become the reason a generation proceeds unchecked.
        logger.warning("Could not read the image ledger for the spend cap", exc_info=True)
        return None


def new_spend():
    """Money spent on images since IMAGE_SPEND_BASELINE, or None if it cannot be derived.

    The baseline exists so a cap set today measures spending from today. Without it, an
    instance with history behind it is over its first cap the moment the cap is written,
    and the only way to use the feature would be to set a number larger than the past --
    i.e. to think about the wrong quantity.
    """
    count = images_generated()
    if count is None:
        return None
    rate = float(_conf("IMAGE_GENERATION_COST", 0) or 0)
    if rate <= 0:
        return None
    baseline = int(_conf("IMAGE_SPEND_BASELINE", 0) or 0)
    return max(0, count - baseline) * rate


def cap_block_reason(n_pending=0):
    """Why this generation must not happen, or None to allow it.

    `n_pending` is how many images the caller is about to buy, so a batch that would cross
    the cap is stopped BEFORE it spends rather than reported after the fact.
    """
    cap = float(_conf("IMAGE_SPEND_CAP", 0) or 0)
    if cap <= 0:
        return None                       # no budget declared: this feature has no opinion

    rate = float(_conf("IMAGE_GENERATION_COST", 0) or 0)
    if rate <= 0:
        return ("IMAGE_SPEND_CAP is set but IMAGE_GENERATION_COST is not, so the spend "
                "cannot be priced and the cap cannot be enforced. Set the rate, or clear "
                "the cap.")

    spent = new_spend()
    if spent is None:
        return ("The image ledger could not be read, so the spend against the "
                f"${cap:,.2f} cap is unknown. Refusing rather than guessing.")

    if spent >= cap:
        return (f"The ${cap:,.2f} image budget is spent (${spent:,.2f} so far). Raise "
                f"IMAGE_SPEND_CAP, or move IMAGE_SPEND_BASELINE forward to start a new one.")

    projected = spent + max(0, int(n_pending or 0)) * rate
    if projected > cap:
        affordable = int((cap - spent) / rate)
        return (f"This would take the total to ${projected:,.2f}, past the ${cap:,.2f} "
                f"budget (${spent:,.2f} spent). Room for about {affordable} more image(s).")
    return None
