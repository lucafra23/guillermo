"""What call_with_retry must and must not do.

The safety property is not "retries work" -- it is that a retry is only ever attempted when the
request provably did not run, because everything else is already paid for.
"""
from unittest import mock

from django.test import SimpleTestCase, override_settings

from agent.retry import call_with_retry, is_retryable, suggested_delay


class FakeAPIError(Exception):
    """Shaped like google-genai's APIError: a `.status`, a `.code`, and a verbatim body."""

    def __init__(self, status=None, code=None, details=None):
        super().__init__(status or code or "error")
        if status is not None:
            self.status = status
        if code is not None:
            self.code = code
        if details is not None:
            self.details = details


def _no_sleep():
    """Run the backoff without spending the wall clock, recording what it asked for."""
    slept = []
    return slept, mock.patch("agent.retry.time.sleep", side_effect=slept.append)


@override_settings(GENAI_MAX_RETRIES=5, GENAI_RETRY_BASE_DELAY=30, GENAI_RETRY_MAX_DELAY=300,
                   GENAI_RETRY_MAX_ELAPSED=600, GENAI_MIN_CALL_INTERVAL=0)
class CallWithRetryTests(SimpleTestCase):

    def test_a_rate_limit_is_retried_and_the_panel_survives(self):
        calls = []

        def flaky():
            calls.append(1)
            if len(calls) < 3:
                raise FakeAPIError(status="RESOURCE_EXHAUSTED")
            return "plate"

        slept, patched = _no_sleep()
        with patched:
            self.assertEqual(call_with_retry(flaky), "plate")
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(slept), 2)

    def test_a_paid_failure_is_not_retried(self):
        """A response that arrived and was then rejected has been billed. Retrying buys it twice."""
        calls = []

        def refused():
            calls.append(1)
            raise ValueError("finish_reason: SAFETY")   # our own handling, post-response

        with self.assertRaises(ValueError):
            call_with_retry(refused)
        self.assertEqual(len(calls), 1)

    def test_deadline_exceeded_is_not_retried(self):
        """504 may mean the image was generated and billed but not delivered."""
        self.assertFalse(is_retryable(FakeAPIError(status="DEADLINE_EXCEEDED", code=504)))
        self.assertTrue(is_retryable(FakeAPIError(status="RESOURCE_EXHAUSTED")))
        self.assertTrue(is_retryable(FakeAPIError(status="UNAVAILABLE")))
        self.assertTrue(is_retryable(FakeAPIError(code=429)))

    @override_settings(GENAI_MAX_RETRIES=0)
    def test_zero_retries_means_one_attempt_not_zero(self):
        calls = []

        def always_limited():
            calls.append(1)
            raise FakeAPIError(status="RESOURCE_EXHAUSTED")

        with self.assertRaises(FakeAPIError):
            call_with_retry(always_limited)
        self.assertEqual(len(calls), 1)

    def test_a_server_delay_beyond_the_cap_gives_up_immediately(self):
        """A daily quota says 3600s. Truncating to the cap burns every attempt on a hopeless wait."""
        calls = []

        def daily_quota():
            calls.append(1)
            raise FakeAPIError(status="RESOURCE_EXHAUSTED",
                               details={"error": {"details": [{"retryDelay": "3600s"}]}})

        slept, patched = _no_sleep()
        with patched, self.assertRaises(FakeAPIError):
            call_with_retry(daily_quota)
        self.assertEqual(len(calls), 1)
        self.assertEqual(slept, [])

    def test_the_servers_own_delay_is_honoured_when_it_fits(self):
        calls = []

        def limited_once():
            calls.append(1)
            if len(calls) == 1:
                raise FakeAPIError(status="RESOURCE_EXHAUSTED",
                                   details={"error": {"details": [{"retryDelay": "12s"}]}})
            return "plate"

        slept, patched = _no_sleep()
        with patched:
            self.assertEqual(call_with_retry(limited_once), "plate")
        self.assertEqual(len(slept), 1)
        self.assertGreaterEqual(slept[0], 12.0)      # jittered upward, never below the server's ask
        self.assertLessEqual(slept[0], 12.0 * 1.15)


class SuggestedDelayTests(SimpleTestCase):
    """A malformed error body must never turn a free retry into an AttributeError.

    That failure mode kills both retry layers: the raised error has no `.status`, so the task
    runner's `getattr(e, 'status', None) in TASK_RETRY_EXCEPTIONS` check misses it too.
    """

    def test_hostile_bodies_yield_no_hint_and_never_raise(self):
        for details in (
            None, "quota exceeded", ["quota exceeded"], 42,
            {"error": "quota exceeded"},                          # proxy-shaped, not Gemini-shaped
            {"error": {"details": "not a list"}},
            {"error": {"details": [None, 7, "x"]}},
            {"error": {"details": [{"retryDelay": "abcs"}]}},
            {"error": {"details": [{"retryDelay": True}]}},       # bool is an int in Python
            {"error": {"details": [{"retryDelay": float("nan")}]}},
            {"error": {"details": [{"retryDelay": float("inf")}]}},
            {"error": {"details": [{"retryDelay": -5}]}},         # reaches time.sleep as ValueError
        ):
            with self.subTest(details=details):
                self.assertIsNone(suggested_delay(FakeAPIError(status="RESOURCE_EXHAUSTED",
                                                               details=details)))

    def test_both_spellings_of_the_field_are_read(self):
        for key in ("retryDelay", "retry_delay"):
            with self.subTest(key=key):
                exc = FakeAPIError(status="RESOURCE_EXHAUSTED",
                                   details={"error": {"details": [{key: "7s"}]}})
                self.assertEqual(suggested_delay(exc), 7.0)
