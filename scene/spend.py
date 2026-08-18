"""A cumulative ceiling on what an instance may spend on image generation.

#9 confirms one batch at a time, and a per-batch confirmation cannot see a habit. Ten
confirmed batches of ten are a hundred images and nobody was ever asked about the hundred.
This reads the ledger the app already keeps and refuses once the total passes a figure the
deployment set in advance, which is the only moment the question can be answered calmly.

It is OFF unless IMAGE_SPEND_CAP is set, because a cap is a budget and this project cannot
know anyone's. When it IS set, every path below fails CLOSED: an unreadable ledger, an
unpriceable generation, or a cap that cannot be evaluated all refuse the spend rather than
allowing it. A cap that cannot be read is not a cap that passed.

What counts as spent is deliberately wider than what has been billed. Generation is queued by
default, and the ledger only learns about a call when it returns, so a cap reading completed
rows alone approves an unbounded burst before the first row lands. Queued paid work counts too.
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


def images_in_flight():
    """Paid image work that has been APPROVED but not yet billed, or None if unreadable.

    The ledger only learns about a generation when it comes back: save_usage writes its row
    from the response. But USE_TASK_QUEUE is on by default, so the admin hands work to Celery
    and returns immediately, and a burst of approvals is invisible to a cap that reads only
    completed rows. Measured before this existed: with a $1 cap and a $0.10 rate, 200
    consecutive checks all passed while 200 generations sat queued -- $20 approved against a
    $1 budget, the cap never firing once.

    Counting queued work makes the cap bound what has been COMMITTED rather than what has
    already been paid, which is the only version of the number that can still prevent anything.
    """
    try:
        from django.conf import settings as dj_settings
        from task.models import Task

        paid = [t for t in (getattr(dj_settings, "TASK_TYPE_GENERATE_IMAGE", None),
                            getattr(dj_settings, "TASK_TYPE_REFINE_IMAGE", None),
                            getattr(dj_settings, "TASK_TYPE_GENERATE_COMIC", None)) if t]
        if not paid:
            return 0
        unfinished = [Task.TASK_STATUS_STARTED, Task.TASK_STATUS_PENDING,
                      Task.TASK_STATUS_HOLDING, Task.TASK_STATUS_SCHEDULED,
                      Task.TASK_STATUS_RETRY]
        return Task.objects.filter(task_type__in=paid, status__in=unfinished).count()
    except Exception:
        logger.warning("Could not read queued generation tasks for the spend cap", exc_info=True)
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
    queued = images_in_flight()
    if queued is None:
        return None
    rate = float(_conf("IMAGE_GENERATION_COST", 0) or 0)
    if rate <= 0:
        return None
    baseline = int(_conf("IMAGE_SPEND_BASELINE", 0) or 0)
    # Billed work plus approved-and-queued work. The baseline only offsets history, so it is
    # subtracted from the billed count alone.
    return (max(0, count - baseline) + max(0, queued)) * rate


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
