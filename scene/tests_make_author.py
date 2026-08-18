"""The handover: an account a maintainer creates, and whether it can actually do the work.

The bug these exist for is invisible to the person who issues the account, because they are a
superuser and every page works for them. So the tests are written from the other side: log in
as the account that was just created and open the pages an author opens.
"""
from io import StringIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from scene.models import Scene, Story, StoryProfile

U = get_user_model()

AUTHOR_PAGES = [
    "/admin/scene/story/", "/admin/scene/scene/", "/admin/scene/action/",
    "/admin/scene/comicaction/", "/admin/scene/render/", "/admin/scene/character/",
]


class BareStaffAccountTests(TestCase):
    """What Elio's handover produces WITHOUT this command. This is the bug, pinned."""

    def test_a_staff_account_with_no_permissions_can_see_nothing(self):
        user = U.objects.create_user("bare", "b@x.test", "pw12345!", is_staff=True)
        self.client.force_login(user)
        self.assertEqual(self.client.get("/admin/").status_code, 200)
        for url in AUTHOR_PAGES:
            self.assertEqual(self.client.get(url).status_code, 403, f"{url} unexpectedly allowed")


class MakeAuthorTests(TestCase):

    def _run(self, *args, **kwargs):
        out = StringIO()
        call_command("make_author", *args, stdout=out, stderr=StringIO(), **kwargs)
        return out.getvalue()

    def test_it_creates_an_account_that_can_open_every_authoring_page(self):
        self._run("luca", password="pw12345!")
        user = U.objects.get(username="luca")
        self.assertTrue(user.is_staff)
        self.assertFalse(user.is_superuser, "an author should not need superuser")
        self.client.force_login(user)
        for url in AUTHOR_PAGES:
            self.assertEqual(self.client.get(url).status_code, 200, f"{url} is not reachable")

    def test_the_account_can_actually_change_things_not_just_look(self):
        self._run("luca", password="pw12345!")
        user = U.objects.get(username="luca")
        for codename in ("add_action", "change_action", "delete_action", "view_action",
                         "change_story", "change_comicaction", "change_render"):
            self.assertTrue(user.has_perm(f"scene.{codename}"), f"missing scene.{codename}")

    def test_it_does_not_hand_out_more_than_authoring_needs(self):
        """An author writes a book; they do not administer the instance."""
        self._run("luca", password="pw12345!")
        user = U.objects.get(username="luca")
        for codename in ("auth.add_user", "auth.change_user", "auth.delete_user",
                         "agent.change_googleapikey", "agent.add_googleapikey",
                         "scene.delete_contactrequest"):
            self.assertFalse(user.has_perm(codename), f"unexpectedly granted {codename}")

    def test_agent_rows_are_readable_but_not_editable(self):
        self._run("luca", password="pw12345!")
        user = U.objects.get(username="luca")
        self.assertTrue(user.has_perm("agent.view_agent"))
        self.assertFalse(user.has_perm("agent.change_agent"))

    def test_running_it_twice_changes_nothing_the_second_time(self):
        self._run("luca", password="pw12345!")
        first = set(Group.objects.get(name="Authors").permissions.values_list("id", flat=True))
        self._run("luca")
        second = set(Group.objects.get(name="Authors").permissions.values_list("id", flat=True))
        self.assertEqual(first, second)
        self.assertEqual(U.objects.filter(username="luca").count(), 1)

    def test_it_promotes_an_account_that_already_exists(self):
        U.objects.create_user("existing", "e@x.test", "pw12345!")
        self._run("existing")
        user = U.objects.get(username="existing")
        self.assertTrue(user.is_staff)
        self.client.force_login(user)
        self.assertEqual(self.client.get("/admin/scene/action/").status_code, 200)

    def test_an_existing_password_survives_a_rerun(self):
        self._run("luca", password="first-one")
        before = U.objects.get(username="luca").password
        self._run("luca")
        self.assertEqual(U.objects.get(username="luca").password, before)

    def test_a_new_account_without_a_password_cannot_log_in(self):
        self._run("nopass")
        self.assertFalse(U.objects.get(username="nopass").has_usable_password())

    def test_it_selects_the_story_so_the_admin_opens_on_the_right_book(self):
        story = Story.objects.create(name="The Book of Bags")
        Scene.objects.create(name="M00", story=story, order=0)
        self._run("luca", story="The Book of Bags")
        self.assertEqual(StoryProfile.objects.get(user__username="luca").story, story)

    def test_an_unknown_story_is_refused_by_name(self):
        Story.objects.create(name="The Book of Bags")
        with self.assertRaises(CommandError) as ctx:
            self._run("luca", story="Some Other Book")
        self.assertIn("Some Other Book", str(ctx.exception))
        self.assertIn("The Book of Bags", str(ctx.exception))   # says what DOES exist

    def test_a_missing_story_profile_is_repaired(self):
        user = U.objects.create_user("old", "o@x.test", "pw12345!")
        StoryProfile.objects.filter(user=user).delete()
        self._run("old")
        self.assertTrue(StoryProfile.objects.filter(user=user).exists())

    def test_dry_run_leaves_nothing_behind(self):
        self._run("ghost", dry_run=True)
        self.assertFalse(U.objects.filter(username="ghost").exists())

    def test_superuser_is_available_but_not_the_default(self):
        self._run("boss", superuser=True)
        self.assertTrue(U.objects.get(username="boss").is_superuser)

    def test_both_profiles_exist_afterwards(self):
        """Every changelist reads StoryProfile; every generation reads AgentProfile."""
        from agent.models import AgentProfile
        user = U.objects.create_user("old2", "o2@x.test", "pw12345!")
        StoryProfile.objects.filter(user=user).delete()
        AgentProfile.objects.filter(user=user).delete()
        self._run("old2")
        self.assertTrue(StoryProfile.objects.filter(user=user).exists())
        self.assertTrue(AgentProfile.objects.filter(user=user).exists())
