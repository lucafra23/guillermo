"""Rate-limit pacing and retry for generation calls.

WHY THIS EXISTS

A rate-limited request produces nothing and costs nothing, so retrying it is free; the only
price of getting it wrong is wall-clock. Dropping it, by contrast, loses a panel silently in
the middle of a batch, and the loss is invisible until someone counts the results.

The task runner already retries on RESOURCE_EXHAUSTED (`TASK_RETRY_EXCEPTIONS`), but only for
work that goes through the queue, only three times, and only after the whole task has failed
and been re-dispatched. That is the wrong altitude for a rate limit: the call itself should
wait a few seconds and go again, rather than failing a task and rescheduling it. This module
retries at the call site, so the inline path is covered too.

WHAT IS AND IS NOT RETRIED

Only the transport call is wrapped, never the handling of a successful response. That boundary
is the whole safety property: a 429 or a 503 means no content was produced and no money was
spent, so another attempt is free. A response that arrives and is then rejected (a content
filter, a finish_reason that is not STOP, a schema that will not validate) HAS been paid for,
and retrying it would pay again for the same refusal. Those propagate on the first failure.

PACING

Retrying after the fact is second best; not tripping the limit is better. GENAI_MIN_CALL_INTERVAL
spaces calls within a process, and is off (0) by default.

Be precise about what "per process" buys you: Celery runs prefork, so the effective rate is
`concurrency x 1/interval`, not one call per interval for the whole deployment — and gunicorn's
threads keep their own counter again. So this is a blunt instrument for the common case (one
worker walking a scene) and NOT a cluster-wide limiter, which would need shared state this app
does not have. Size it against concurrency, not against the number of machines.
"""
import logging
import math
import random
import threading
import time

from django.conf import settings

logger = logging.getLogger(__name__)

# Statuses that mean the request PROVABLY never ran, so another attempt cannot be billed twice.
# The list is deliberately short, and shorter than the obvious "retry all 5xx" instinct:
#
#   RESOURCE_EXHAUSTED / 429 — rejected at the quota gate, before any model work.
#   UNAVAILABLE / 503        — the server declined to accept the request at all.
#
# Everything tempting that is NOT here, and why:
#   DEADLINE_EXCEEDED / 504  — the SERVER gave up waiting, which does not mean the model gave up
#                              working. With a 300s client timeout against a frontend that
#                              deadlines earlier, an image can be generated and BILLED and simply
#                              not delivered. Retrying that five times buys five images and shows
#                              none of them, and because save_usage only runs on a returned
#                              response, the spend does not even appear in TokenUsage.
#   INTERNAL / 500, 502      — same argument, weaker but not disprovable from the outside.
#   ABORTED                  — implies contention on work that may have started.
# If one of these needs retrying later, it needs a cost argument first, not a sympathetic feeling.
RATE_LIMIT_STATUSES = {"RESOURCE_EXHAUSTED"}
TRANSIENT_STATUSES = {"UNAVAILABLE"}
RETRYABLE_CODES = {429, 503}

_pace_lock = threading.Lock()
_last_call_at = 0.0


def _conf(name, default):
    return getattr(settings, name, default)


def _status_of(exc):
    """The API status string ('RESOURCE_EXHAUSTED'), or None for non-API exceptions.

    Always via getattr: google-genai's APIError carries `.status`, but a socket error, a
    ValueError from our own response handling, or anything else does not, and reaching for the
    attribute directly is how an error handler turns one failure into a different, confusing one.
    """
    status = getattr(exc, "status", None)
    return status if isinstance(status, str) else None


def _code_of(exc):
    code = getattr(exc, "code", None)
    return code if isinstance(code, int) else None


def is_rate_limited(exc):
    return _status_of(exc) in RATE_LIMIT_STATUSES or _code_of(exc) == 429


def is_retryable(exc):
    """True when the request demonstrably did not run, so another attempt costs nothing."""
    status = _status_of(exc)
    if status in RATE_LIMIT_STATUSES or status in TRANSIENT_STATUSES:
        return True
    return _code_of(exc) in RETRYABLE_CODES


def suggested_delay(exc):
    """The server's own retryDelay in seconds, or None. Never raises.

    `APIError.details` is the response body verbatim, so it is whatever the far end sent: a
    Gemini-shaped dict, but equally `{"error": "quota exceeded"}` from a proxy, a list, or a
    bare string from a load balancer that rewrote the 429.

    Being total matters far more than being clever here. An exception raised while working out
    *how long to wait* replaces a free, retryable rate limit with an AttributeError that has no
    `.status` — which then fails the task runner's `getattr(e, 'status', None) in
    TASK_RETRY_EXCEPTIONS` check too. One malformed error body would therefore kill both retry
    layers and lose the panel for good. Anything unparseable simply means "no hint, back off on
    our own schedule".
    """
    try:
        details = getattr(exc, "details", None)
        if not isinstance(details, dict):
            return None
        error = details.get("error", details)
        if not isinstance(error, dict):
            return None
        entries = error.get("details")
        if not isinstance(entries, (list, tuple)):
            return None
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            delay = entry.get("retryDelay") or entry.get("retry_delay")
            if isinstance(delay, str) and delay.endswith("s"):
                try:
                    delay = float(delay[:-1])
                except ValueError:
                    continue
            if isinstance(delay, bool) or not isinstance(delay, (int, float)):
                continue
            delay = float(delay)
            # A negative delay reaches time.sleep() as a ValueError; NaN compares False against
            # every bound and slips through. Both are nonsense rather than instructions.
            if not math.isfinite(delay) or delay < 0:
                continue
            return delay
    except Exception:  # a hostile or simply unexpected body must never break the retry
        logger.debug("Could not read retryDelay from the error body", exc_info=True)
    return None


def pace():
    """Block until at least GENAI_MIN_CALL_INTERVAL has passed since the last call."""
    global _last_call_at
    interval = float(_conf("GENAI_MIN_CALL_INTERVAL", 0) or 0)
    if interval <= 0:
        return
    with _pace_lock:
        wait = _last_call_at + interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_call_at = time.monotonic()


def call_with_retry(func, *, description="generation call"):
    """Run `func()`, retrying only failures that prove no work was done.

    Bounded twice over: by attempt count and, more importantly, by GENAI_RETRY_MAX_ELAPSED. The
    elapsed budget is what keeps this safe to call from a request thread — the admin's
    generate-image action runs inline, gunicorn is configured with `--timeout 0`, and without a
    total bound a handful of rate-limited clicks would park every thread in time.sleep and take
    the admin down for as long as the backoff lasted.
    """
    # GENAI_MAX_RETRIES counts RETRIES, so attempts is one more than that, and 0 means "try once,
    # never retry" rather than "never call anything". Reading it the other way meant a config of 0
    # ran the loop body zero times and raised TypeError from a None exception.
    retries = max(0, int(_conf("GENAI_MAX_RETRIES", 5)))
    max_attempts = retries + 1
    base = float(_conf("GENAI_RETRY_BASE_DELAY", 30))
    cap = float(_conf("GENAI_RETRY_MAX_DELAY", 300))
    budget = float(_conf("GENAI_RETRY_MAX_ELAPSED", 600))

    started = time.monotonic()
    for attempt in range(1, max_attempts + 1):
        pace()
        try:
            return func()
        except Exception as exc:  # noqa: BLE001 — re-raised below unless provably free to retry
            if not is_retryable(exc) or attempt == max_attempts:
                raise

            server_delay = suggested_delay(exc)
            if server_delay is None:
                delay = min(cap, base * (2 ** (attempt - 1)))
                # Full jitter: a batch that trips the limit together must not come back in lockstep.
                delay = delay / 2 + random.uniform(0, delay / 2)
            else:
                if server_delay > cap:
                    # The server is telling us this will not clear for far longer than we are
                    # willing to wait (a daily quota says 3600s). Truncating to the cap would
                    # burn every remaining attempt on a wait that cannot possibly succeed; stop
                    # now and let the caller surface a real quota error.
                    logger.warning(
                        "%s: server asked for %.0fs, beyond the %.0fs cap — giving up rather than "
                        "spending the remaining attempts on a wait that cannot succeed",
                        description, server_delay, cap,
                    )
                    raise
                # Jitter the server's figure too, for the same herd reason: RetryInfo is identical
                # for every caller in the batch, so honouring it exactly is the lockstep case.
                delay = server_delay * (1.0 + random.uniform(0, 0.15))

            elapsed = time.monotonic() - started
            if elapsed + delay > budget:
                logger.warning(
                    "%s: %.0fs spent and the next wait is %.0fs, over the %.0fs budget — giving up",
                    description, elapsed, delay, budget,
                )
                raise

            kind = "rate limited" if is_rate_limited(exc) else "unavailable"
            logger.warning(
                "%s: %s (%s), attempt %d/%d; retrying in %.0fs",
                description, kind, _status_of(exc) or _code_of(exc), attempt, max_attempts, delay,
            )
            time.sleep(delay)
