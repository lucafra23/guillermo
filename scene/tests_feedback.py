"""The feedback ledger, and the seven ways the prose version failed.

Each test names the failure it prevents. They are not hypothetical: every one is a recorded
incident on the book this app serves, where feedback lived in markdown tables outside the app.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase

from scene.models import (Action, Feedback, FeedbackBatch, Scene, Story, StoryProfile)

U = get_user_model()


class ScopeTests(TestCase):
    """Filing against a panel must be enough; the row still has to be findable from above."""

    @classmethod
    def setUpTestData(cls):
        cls.story = Story.objects.create(name="Book")
        cls.scene = Scene.objects.create(name="M14", story=cls.story, order=14)
        cls.panel = Action.objects.create(name="1_benches", scene=cls.scene, order=0)

    def test_filing_against_a_panel_fills_in_the_scene_and_story(self):
        item = Feedback.objects.create(action=self.panel, ask="the bench is on the wrong side")
        item.refresh_from_db()
        self.assertEqual(item.scene, self.scene)
        self.assertEqual(item.story, self.story)

    def test_a_story_wide_rule_needs_no_panel(self):
        """AW-G5: a standing ruling had nowhere to live and was re-learned each time."""
        rule = Feedback.objects.create(story=self.story, kind=Feedback.KIND_RULE,
                                       ask="never bake words into art")
        self.assertIsNone(rule.action)
        self.assertEqual(rule.kind, Feedback.KIND_RULE)

    def test_it_is_reachable_from_the_panel(self):
        Feedback.objects.create(action=self.panel, ask="x")
        self.assertEqual(self.panel.feedback.count(), 1)


class StatusTests(TestCase):
    """DONE and VERIFIED are different claims."""

    def setUp(self):
        self.story = Story.objects.create(name="Book")

    def test_done_is_not_verified(self):
        """AW-G2: rows were ticked done with the defect still on the page."""
        item = Feedback.objects.create(story=self.story, ask="fix the white box",
                                       status=Feedback.STATUS_DONE)
        self.assertTrue(item.is_open, "DONE must still count as outstanding until looked at")
        item.status = Feedback.STATUS_VERIFIED
        item.save()
        self.assertFalse(item.is_open)

    def test_partly_done_is_a_state_you_can_record(self):
        """AW-G3: one sentence carrying two asks got half-applied, and nothing said so."""
        item = Feedback.objects.create(story=self.story, status=Feedback.STATUS_PARTIAL,
                                       ask="a caption AND a candlestick chart on the phone",
                                       note="caption applied; the chart is not drawn")
        self.assertTrue(item.is_open)
        self.assertIn("chart", item.note)

    def test_moot_and_superseded_are_closed_without_claiming_work(self):
        a = Feedback.objects.create(story=self.story, ask="old ask",
                                    status=Feedback.STATUS_SUPERSEDED)
        b = Feedback.objects.create(story=self.story, ask="the ask that replaced it")
        a.superseded_by = b
        a.save()
        self.assertFalse(a.is_open)
        self.assertEqual(b.supersedes.first(), a)


class ApprovalTests(TestCase):
    """AW-G4: approvals are not artefacts; only complaints are."""

    def test_an_approval_is_a_row_like_any_other(self):
        story = Story.objects.create(name="Book")
        scene = Scene.objects.create(name="M11", story=story, order=11)
        panel = Action.objects.create(name="15_refactor", scene=scene, order=0)
        approval = Feedback.objects.create(action=panel, kind=Feedback.KIND_APPROVAL,
                                           ask="this one is right, keep it",
                                           status=Feedback.STATUS_VERIFIED)
        self.assertEqual(panel.feedback.filter(kind=Feedback.KIND_APPROVAL).count(), 1)
        self.assertFalse(approval.is_open)


class BatchTests(TestCase):
    """AW-G1 and AW-G6: unfiled feedback is lost, and quoted words are not a record."""

    def test_a_batch_keeps_the_message_verbatim_and_its_items_link_back(self):
        story = Story.objects.create(name="Book")
        batch = FeedbackBatch.objects.create(
            story=story, label="batch AW",
            verbatim="But the mod looks different visually!!\nand the founder earring")
        one = Feedback.objects.create(batch=batch, story=story, ask="the mod looks different")
        two = Feedback.objects.create(batch=batch, story=story, ask="the founder earring")
        self.assertEqual(batch.items.count(), 2)
        self.assertIn("looks different visually", batch.verbatim)
        self.assertEqual(one.batch, two.batch)

    def test_the_verbatim_survives_even_if_every_item_is_deleted(self):
        batch = FeedbackBatch.objects.create(label="AO", verbatim="his exact words")
        Feedback.objects.create(batch=batch, ask="something").delete()
        batch.refresh_from_db()
        self.assertEqual(batch.verbatim, "his exact words")

    def test_an_agents_finding_is_marked_as_such(self):
        """AW-G7: an agent's finding is not the author's instruction."""
        item = Feedback.objects.create(ask="3 of 5 panels here are already cut",
                                       source=FeedbackBatch.SOURCE_AGENT)
        self.assertEqual(item.source, FeedbackBatch.SOURCE_AGENT)


class AdminTests(TestCase):
    """It has to be usable where the work happens, or it is a slower way of losing things."""

    def setUp(self):
        self.story = Story.objects.create(name="Book")
        self.scene = Scene.objects.create(name="M14", story=self.story, order=0)
        self.panel = Action.objects.create(name="1_benches", scene=self.scene, order=0)
        user = U.objects.create_superuser("boss", "b@x.test", "pw12345!")
        StoryProfile.objects.get_or_create(user=user)
        self.client.force_login(user)

    def test_the_changelists_open(self):
        for url in ("/admin/scene/feedback/", "/admin/scene/feedbackbatch/"):
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_feedback_appears_on_the_panel_page(self):
        Feedback.objects.create(action=self.panel, ask="the bench is on the wrong side")
        html = self.client.get(f"/admin/scene/action/{self.panel.pk}/change/").content.decode()
        self.assertIn("feedback on this panel", html.lower())
        self.assertIn("wrong side", html)

    def test_open_items_can_be_found_by_status(self):
        Feedback.objects.create(story=self.story, ask="open one")
        Feedback.objects.create(story=self.story, ask="closed one",
                                status=Feedback.STATUS_VERIFIED)
        response = self.client.get("/admin/scene/feedback/", {"status__exact": "open"})
        self.assertEqual(response.context["cl"].result_count, 1)

    def test_searching_finds_an_item_by_the_words_that_were_said(self):
        Feedback.objects.create(story=self.story, ask="i see still the lizard for tech support")
        response = self.client.get("/admin/scene/feedback/", {"q": "lizard"})
        self.assertEqual(response.context["cl"].result_count, 1)

    def test_the_verified_action_closes_items(self):
        item = Feedback.objects.create(story=self.story, ask="x", status=Feedback.STATUS_DONE)
        self.client.post("/admin/scene/feedback/", {
            "action": "mark_verified", "_selected_action": [str(item.pk)]})
        item.refresh_from_db()
        self.assertEqual(item.status, Feedback.STATUS_VERIFIED)
