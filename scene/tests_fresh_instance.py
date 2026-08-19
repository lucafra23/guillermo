"""What breaks on an instance that is not the one the app was written on.

Every case here was reproduced against a copy of a real deployment before being fixed. They
share a shape: something that is always true for the original developer's database -- a group
that exists, a style that is set, a profile row that lines up with a user id -- and is not true
for anyone else's.
"""
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings

from agent.models import Agent, AgentModel, AgentProfile
from scene.models import Author, Character, Scene, Story, Style

U = get_user_model()


class CoAuthorInviteTests(TestCase):
    """Adding an Author with an email is how a maintainer invites a collaborator."""

    def setUp(self):
        self.story = Story.objects.create(name="Book")

    def test_inviting_someone_works_on_an_instance_with_no_groups(self):
        """It used to raise Group.DoesNotExist: nothing in the project creates 'faf'."""
        self.assertEqual(Group.objects.count(), 0)
        with mock.patch("scene.mixins.EmailSenderMixin.send_email", create=True), \
                mock.patch("django.core.mail.send_mail"):
            Author.objects.create(story=self.story, email="new@example.test")
        self.assertTrue(U.objects.filter(email="new@example.test").exists())

    def test_the_invited_user_is_staff_and_in_the_group(self):
        with mock.patch("django.core.mail.send_mail"):
            Author.objects.create(story=self.story, email="second@example.test")
        user = U.objects.get(email="second@example.test")
        self.assertTrue(user.is_staff)
        self.assertTrue(user.groups.exists())

    @override_settings(INVITED_AUTHOR_GROUP="Authors")
    def test_the_group_can_be_pointed_at_one_that_carries_permissions(self):
        with mock.patch("django.core.mail.send_mail"):
            Author.objects.create(story=self.story, email="third@example.test")
        user = U.objects.get(email="third@example.test")
        self.assertIn("Authors", list(user.groups.values_list("name", flat=True)))

    def test_an_existing_group_is_reused_not_duplicated(self):
        Group.objects.create(name="faf")
        with mock.patch("django.core.mail.send_mail"):
            Author.objects.create(story=self.story, email="fourth@example.test")
        self.assertEqual(Group.objects.filter(name="faf").count(), 1)


class StoryWithoutAStyleTests(TestCase):
    """A story exists before anyone picks its style."""

    def test_scene_contents_do_not_crash_without_a_style(self):
        story = Story.objects.create(name="Fresh")          # no style
        scene = Scene.objects.create(name="M00", story=story, order=0)
        self.assertIsInstance(scene.get_contents(generate_self=False), list)

    def test_a_style_is_still_included_when_there_is_one(self):
        story = Story.objects.create(name="Styled", style=Style.objects.create(name="ink"))
        scene = Scene.objects.create(name="M00", story=story, order=0)
        self.assertTrue(scene.get_contents(generate_self=False))


class UsageRecordingTests(TestCase):
    """save_usage runs after the generation returned, i.e. after the money is gone."""

    def _agent(self):
        return Agent.objects.create(name="artist", agent_model=AgentModel.objects.create(name="m"),
                                    output_type=Agent.OUTPUT_TYPE_IMAGE)

    def _response(self):
        response = mock.Mock()
        response.usage_metadata.prompt_token_count = 10
        response.usage_metadata.candidates_token_count = 5
        response.usage_metadata.total_token_count = 15
        return response

    def test_a_user_without_an_agent_profile_still_gets_a_usage_row(self):
        user = U.objects.create_user("noprofile", "n@x.test", "pw12345!")
        AgentProfile.objects.filter(user=user).delete()
        agent = self._agent()
        agent.save_usage(U.objects.get(pk=user.pk), self._response(), obj=None, preset="x")
        self.assertEqual(agent.token_usages.count(), 1,
                         "the spend happened and nothing recorded it")

    def test_the_api_key_is_recorded_when_there_is_one(self):
        user = U.objects.create_user("withkey", "w@x.test", "pw12345!")
        agent = self._agent()
        agent.save_usage(U.objects.get(pk=user.pk), self._response(), obj=None, preset="x")
        row = agent.token_usages.first()
        self.assertIn("api_key_id", row.json_report)


class ProfileScopingTests(TestCase):
    """The per-user admin querysets compared the wrong two columns."""

    def test_a_user_sees_their_own_agent_profile_when_ids_diverge(self):
        from django.contrib import admin as dj_admin

        first = U.objects.create_user("first", "f@x.test", "pw12345!", is_staff=True)
        second = U.objects.create_user("second", "s@x.test", "pw12345!", is_staff=True)
        # Force the ids apart, which is what a restore or a deletion does in practice.
        AgentProfile.objects.filter(user=second).delete()
        mine = AgentProfile.objects.create(user=second)
        self.assertNotEqual(mine.pk, second.pk)

        request = mock.Mock()
        request.user = second
        rows = dj_admin.site._registry[AgentProfile].get_queryset(request)
        self.assertIn(mine, rows)
        self.assertTrue(all(r.user_id == second.pk for r in rows),
                        "someone else's profile is visible")
