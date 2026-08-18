"""The cumulative cap, which is the guard a per-batch confirmation cannot be.

Every test states the money consequence it protects, because that is the only reason any of
this exists. The negative cases matter most: a cap that fails OPEN is worse than no cap,
since it reads as protection while allowing the spend.
"""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from agent.models import Agent, AgentModel, TokenUsage
from scene.spend import cap_block_reason, images_generated, new_spend


def _record(n, output_type=Agent.OUTPUT_TYPE_IMAGE):
    """n rows in the ledger, as save_usage would write them."""
    model = AgentModel.objects.create(name="m")
    agent = Agent.objects.create(name=f"a-{output_type}", agent_model=model, output_type=output_type)
    TokenUsage.objects.bulk_create([TokenUsage(agent=agent, tokens=1) for _ in range(n)])


class LedgerTests(TestCase):

    def test_it_counts_image_generations_only(self):
        _record(3, Agent.OUTPUT_TYPE_IMAGE)
        _record(5, Agent.OUTPUT_TYPE_TEXT)
        self.assertEqual(images_generated(), 3)

    @override_settings(IMAGE_GENERATION_COST=0.039, IMAGE_SPEND_BASELINE=0)
    def test_spend_is_the_count_times_the_rate(self):
        _record(10)
        self.assertAlmostEqual(new_spend(), 0.39, places=4)

    @override_settings(IMAGE_GENERATION_COST=0.039, IMAGE_SPEND_BASELINE=8)
    def test_the_baseline_makes_the_cap_measure_from_today(self):
        """An instance with history behind it must not be over its first budget on day one."""
        _record(10)
        self.assertAlmostEqual(new_spend(), 2 * 0.039, places=4)

    @override_settings(IMAGE_GENERATION_COST=0.039, IMAGE_SPEND_BASELINE=99)
    def test_a_baseline_past_the_count_is_zero_not_negative(self):
        _record(10)
        self.assertEqual(new_spend(), 0)


@override_settings(IMAGE_GENERATION_COST=0.10)
class CapTests(TestCase):

    def test_no_cap_configured_means_no_opinion(self):
        """Off by default: this project cannot know anyone's budget."""
        _record(10_000)
        with override_settings(IMAGE_SPEND_CAP=0):
            self.assertIsNone(cap_block_reason(500))

    @override_settings(IMAGE_SPEND_CAP=10.0)
    def test_under_the_cap_it_says_nothing(self):
        _record(10)                       # $1.00 of $10.00
        self.assertIsNone(cap_block_reason(5))

    @override_settings(IMAGE_SPEND_CAP=10.0)
    def test_at_the_cap_it_refuses(self):
        _record(100)                      # exactly $10.00
        reason = cap_block_reason(1)
        self.assertIn("budget is spent", reason)

    @override_settings(IMAGE_SPEND_CAP=10.0)
    def test_a_batch_that_would_cross_the_cap_is_stopped_before_it_spends(self):
        """The point of taking n_pending: after the fact is too late for money."""
        _record(95)                       # $9.50 spent, $0.50 of room
        reason = cap_block_reason(20)     # would add $2.00
        self.assertIn("past the", reason)
        self.assertIn("Room for about 5", reason)

    @override_settings(IMAGE_SPEND_CAP=10.0)
    def test_a_batch_that_exactly_reaches_the_cap_is_allowed(self):
        _record(95)
        self.assertIsNone(cap_block_reason(5))       # $9.50 + $0.50 == $10.00

    @override_settings(IMAGE_SPEND_CAP=10.0, IMAGE_GENERATION_COST=0)
    def test_a_cap_that_cannot_be_priced_refuses(self):
        """Fails CLOSED. The alternative is a cap that reads as protection and is not."""
        _record(10)
        reason = cap_block_reason(1)
        self.assertIn("cannot be priced", reason)

    @override_settings(IMAGE_SPEND_CAP=10.0)
    def test_an_unreadable_ledger_refuses(self):
        with mock.patch("scene.spend.images_generated", return_value=None):
            reason = cap_block_reason(1)
        self.assertIn("could not be read", reason)

    def test_a_broken_ledger_query_is_reported_not_raised(self):
        """This runs on the path that spends money; an exception must not become permission."""
        with mock.patch("agent.models.TokenUsage.objects.filter", side_effect=RuntimeError("boom")):
            self.assertIsNone(images_generated())
        with override_settings(IMAGE_SPEND_CAP=10.0), \
                mock.patch("agent.models.TokenUsage.objects.filter", side_effect=RuntimeError("boom")):
            self.assertIn("could not be read", cap_block_reason(1))


@override_settings(IMAGE_GENERATION_COST=0.10, IMAGE_SPEND_CAP=1.0)
class AdminActionTests(TestCase):
    """The cap has to stop the action, not merely disapprove of it."""

    def setUp(self):
        from django.contrib import admin as dj_admin
        from scene.models import ComicAction, Scene, Story, StoryProfile

        U = get_user_model()
        self.user = U.objects.create_superuser("boss", "b@x.test", "pw12345!")
        StoryProfile.objects.get_or_create(user=self.user)
        story = Story.objects.create(name="Book")
        scene = Scene.objects.create(name="S", story=story, order=0)
        self.panels = [ComicAction.objects.create(name=f"p{i}", scene=scene, order=i) for i in range(3)]
        self.ma = dj_admin.site._registry[ComicAction]
        self.qs = ComicAction.objects.filter(pk__in=[p.pk for p in self.panels])

    def _request(self):
        req = mock.Mock()
        req.user = self.user
        req.POST = {}
        return req

    def test_generate_does_not_queue_a_single_task_over_the_cap(self):
        _record(20)                              # $2.00 spent against a $1.00 cap
        req = self._request()
        with mock.patch("task.models.Task.createTaskIfQueueEnabled") as queued, \
                mock.patch.object(self.ma, "message_user") as told:
            self.ma._queue_generate_image(req, self.qs)
        queued.assert_not_called()
        self.assertIn("budget is spent", str(told.call_args))

    def test_refine_is_covered_too(self):
        """Refine calls the same paid agent; a cap that skipped it would have a door in it."""
        _record(20)
        req = self._request()
        with mock.patch("task.models.Task.createTaskIfQueueEnabled") as queued, \
                mock.patch.object(self.ma, "message_user"):
            self.ma.default_refine_image(req, self.qs)
        queued.assert_not_called()

    def test_under_the_cap_the_action_proceeds(self):
        _record(1)                               # $0.10 of $1.00
        req = self._request()
        with mock.patch("task.models.Task.createTaskIfQueueEnabled") as queued, \
                mock.patch.object(self.ma, "message_user"):
            self.ma._queue_generate_image(req, self.qs)
        self.assertEqual(queued.call_count, 3)
