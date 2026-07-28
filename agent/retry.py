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
spaces calls within a process. It is deliberately per-process and not a distributed limiter:
that matches how batches actually run here (a worker walking a scene), and a cluster-wide limiter
would need shared state this app does not have. With several workers, divide the interval by the
worker count, or leave it at 0 and rely on the retry.
"""
import logging
import random
import threading
import time

from django.conf import settings

logger = logging.getLogger(__name__)

# Statuses that mean "the request never ran". Anything else is either a real error or something
# that already cost money, and must not be retried.
RATE_LIMIT_STATUSES = {"RESOURCE_EXHAUSTED"}
TRANSIENT_STATUSES = {"UNAVAILABLE", "INTERNAL", "DEADLINE_EXCEEDED", "ABORTED"}
RETRYABLE_CODES = {429, 500, 502, 503, 504}

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
    """Honour the server's own retryDelay when it sends one, in preference to guessing."""
    details = getattr(exc, "details", None)
    if not isinstance(details, dict):
        return None
    error = details.get("error", details)
    for entry in (error.get("details") or []):
        if not isinstance(entry, dict):
            continue
        delay = entry.get("retryDelay") or entry.get("retry_delay")
        if isinstance(delay, str) and delay.endswith("s"):
            try:
                return float(delay[:-1])
            except ValueError:
                continue
        if isinstance(delay, (int, float)):
            return float(delay)
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


def call_with_retry(func, *, description="generation call", on_retry=None):
    """Run `func()`, retrying only failures that prove no work was done.

    `on_retry(attempt, delay, exc)` is called before each sleep so a caller with somewhere to
    report progress (a Task log) can say what is happening instead of appearing to hang.
    """
    max_attempts = int(_conf("GENAI_MAX_RETRIES", 5))
    base = float(_conf("GENAI_RETRY_BASE_DELAY", 30))
    cap = float(_conf("GENAI_RETRY_MAX_DELAY", 300))

    last_exc = None
    for attempt in range(1, max_attempts + 1):
        pace()
        try:
            return func()
        except Exception as exc:  # noqa: BLE001 — re-raised below unless provably free to retry
            last_exc = exc
            if not is_retryable(exc) or attempt == max_attempts:
                raise
            # Exponential with full jitter, so a batch that trips the limit together does not
            # come back in lockstep and trip it again.
            delay = suggested_delay(exc)
            if delay is None:
                delay = min(cap, base * (2 ** (attempt - 1)))
                delay = delay / 2 + random.uniform(0, delay / 2)
            else:
                delay = min(cap, delay)
            kind = "rate limited" if is_rate_limited(exc) else "transient error"
            message = (
                f"{description}: {kind} ({_status_of(exc) or _code_of(exc)}), "
                f"attempt {attempt}/{max_attempts}; retrying in {delay:.0f}s"
            )
            logger.warning(message)
            if on_retry:
                try:
                    on_retry(attempt, delay, exc)
                except Exception:  # reporting must never break the retry
                    logger.exception("on_retry callback failed")
            time.sleep(delay)
    raise last_exc
