"""Tests for the generate-image spend guard.

Three things are under test, and all three are ways to spend money you did not mean to:

  1. the confirmation fires on batch SIZE as well as on overwrite,
  2. a confirmation is one-shot -- a refresh or a resent payload cannot re-fire it,
  3. the money figure never raises, whatever the deployment configured.

SimpleTestCase throughout: none of this needs a database, and a guard on the path that
spends money should be provable without one.

Note on (2): the one-shot property is the whole point, and it is easy to write a test
that passes without it. Asserting only that a fresh token validates would stay green
against a plain signature, which verifies every time it is presented. So the replay
test asserts the SECOND use is rejected, which a stateless signature cannot do.
"""
from django.contrib.sessions.backends.cache import SessionStore
from django.test import SimpleTestCase, RequestFactory, override_settings

from scene.mixins import AdminActionsMixin


class FakeUser:
    def __init__(self, pk):
        self.pk = pk


class ConfirmationTokenTests(SimpleTestCase):
    """The token must be single-use, and bound to both the batch and the user."""

    PKS = [3, 1, 2]

    def setUp(self):
        self.mixin = AdminActionsMixin()
        self.factory = RequestFactory()
        self.session = SessionStore()

    def request(self, token=None, user_pk=7):
        post = {} if token is None else {"confirm_token": token}
        request = self.factory.post("/admin/", post)
        request.session = self.session          # same session unless a test says otherwise
        request.user = FakeUser(user_pk)
        return request

    def issue(self, pks=None, user_pk=7):
        return self.mixin._confirmation_token(self.request(user_pk=user_pk), pks or self.PKS)

    def test_a_fresh_token_validates(self):
        token = self.issue()
        self.assertTrue(self.mixin._confirmation_is_valid(self.request(token), self.PKS))

    def test_a_token_cannot_be_replayed(self):
        """The second presentation must be rejected. A signature alone cannot do this."""
        token = self.issue()
        self.assertTrue(self.mixin._confirmation_is_valid(self.request(token), self.PKS))
        self.assertFalse(
            self.mixin._confirmation_is_valid(self.request(token), self.PKS),
            "a confirmation was accepted twice -- a refresh would re-fire the batch")

    def test_pk_order_does_not_matter(self):
        """The batch is a set, not a sequence: a differently-ordered queryset is the
        same batch and must not be treated as tampering."""
        token = self.issue(pks=[1, 2, 3])
        self.assertTrue(self.mixin._confirmation_is_valid(self.request(token), [3, 2, 1]))

    def test_token_is_bound_to_the_selection(self):
        token = self.issue()
        self.assertFalse(self.mixin._confirmation_is_valid(self.request(token), self.PKS + [4]))

    def test_token_is_bound_to_the_user(self):
        token = self.issue()
        self.assertFalse(self.mixin._confirmation_is_valid(self.request(token, user_pk=99), self.PKS))

    def test_unspent_token_survives_a_rejected_attempt(self):
        """A rejected attempt must not burn the nonce, or a wrong click would force the
        operator to re-select the whole batch."""
        token = self.issue()
        self.mixin._confirmation_is_valid(self.request(token), self.PKS + [4])   # rejected
        self.assertTrue(self.mixin._confirmation_is_valid(self.request(token), self.PKS))

    def test_absent_empty_garbage_and_tampered_tokens_are_rejected(self):
        token = self.issue()
        for label, candidate in [
            ("absent", None),
            ("empty", ""),
            ("garbage", "not-a-token"),
            ("tampered", token[:-3] + "aaa"),
        ]:
            with self.subTest(token=label):
                self.assertFalse(
                    self.mixin._confirmation_is_valid(self.request(candidate), self.PKS))

    def test_a_token_from_another_session_is_rejected(self):
        """The nonce lives in the issuing session, so a signed payload lifted into a
        different session has nothing to spend."""
        token = self.issue()
        self.session = SessionStore()           # a different browser
        self.assertFalse(self.mixin._confirmation_is_valid(self.request(token), self.PKS))

    def test_pending_nonces_are_bounded(self):
        """A session must not grow without limit just because someone opened the
        confirmation screen repeatedly."""
        request = self.request()
        for _ in range(50):
            self.mixin._confirmation_token(request, self.PKS)
        self.assertEqual(len(request.session[AdminActionsMixin.CONFIRM_NONCE_KEY]), 20)


class ConfirmationThresholdTests(SimpleTestCase):
    """Confirm on overwrite OR on size. The original asked only about overwrite, which
    made it an overwrite guard wearing a spend guard's name: 500 image-less rows went
    through with no prompt at all."""

    # The production predicate itself, not a restatement of it. Restating the condition
    # here would test the restatement and stay green through any change to the guard.
    needs_confirmation = AdminActionsMixin._needs_generate_confirmation

    def test_small_clean_batch_is_not_nagged(self):
        self.assertFalse(self.needs_confirmation(n_overwrite=0, total=3))

    def test_batch_at_the_threshold_is_not_nagged(self):
        self.assertFalse(
            self.needs_confirmation(n_overwrite=0, total=AdminActionsMixin.CONFIRM_GENERATE_OVER))

    def test_large_clean_batch_confirms(self):
        """The case the first version of this guard missed entirely."""
        self.assertTrue(
            self.needs_confirmation(
                n_overwrite=0, total=AdminActionsMixin.CONFIRM_GENERATE_OVER + 1))

    def test_any_overwrite_confirms_however_small(self):
        self.assertTrue(self.needs_confirmation(n_overwrite=1, total=2))


class SpendEstimateTests(SimpleTestCase):
    """The figure is shown on a screen people act on, so a wrong number is worse than
    none -- and it must never be the line that raises, because it used to raise AFTER
    the batch was queued."""

    def estimate(self, count=40):
        return AdminActionsMixin._spend_estimate(count)

    @override_settings(IMAGE_GENERATION_COST=0.15)
    def test_a_configured_rate_produces_a_figure(self):
        self.assertEqual(self.estimate(40), " (about $6.00)")

    @override_settings(IMAGE_GENERATION_COST=0.039)
    def test_the_figure_is_rounded_to_cents(self):
        self.assertEqual(self.estimate(40), " (about $1.56)")

    @override_settings(IMAGE_GENERATION_COST=0)
    def test_zero_rate_shows_no_figure(self):
        self.assertEqual(self.estimate(), "")

    @override_settings(IMAGE_GENERATION_COST=None)
    def test_unset_rate_shows_no_figure(self):
        self.assertEqual(self.estimate(), "")

    @override_settings(IMAGE_GENERATION_COST="0.15")
    def test_a_string_rate_does_not_multiply_as_a_string(self):
        """os.getenv returns str. Uncoerced, rate * count repeats the string
        ('0.150.150.15') and then dies inside the :.2f format spec -- historically
        after the batch had been queued."""
        self.assertEqual(self.estimate(40), " (about $6.00)")

    @override_settings(IMAGE_GENERATION_COST="not-a-number")
    def test_an_unusable_rate_shows_no_figure_instead_of_raising(self):
        self.assertEqual(self.estimate(), "")

    @override_settings(IMAGE_GENERATION_COST=-1)
    def test_a_negative_rate_shows_no_figure(self):
        self.assertEqual(self.estimate(), "")
