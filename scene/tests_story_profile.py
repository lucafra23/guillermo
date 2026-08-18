"""A user without a StoryProfile must still be able to use the admin.

scene/signals.py creates a profile on user creation, but a profile can be absent anyway --
deleted by hand, or created before that signal was connected. Every dereference of
`request.user.story_profile` in this module used to be unguarded, so the first changelist such a
user opened returned a 500. The filtering these mixins do is a stated preference; not having
stated one is not an error.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase

from scene.models import Action, Scene, Story, StoryProfile

U = get_user_model()


class ProfilelessAdminTests(TestCase):
    """The regression: no profile at all."""

    @classmethod
    def setUpTestData(cls):
        cls.story = Story.objects.create(name="Book")
        cls.scene = Scene.objects.create(name="Opening", story=cls.story, order=0)
        cls.action = Action.objects.create(name="Panel", scene=cls.scene, order=0)

    def setUp(self):
        self.user = U.objects.create_superuser("nobody", "n@x.test", "pw12345!")
        StoryProfile.objects.filter(user=self.user).delete()   # the state this guards against
        self.client.force_login(self.user)

    def test_the_story_changelist_opens(self):
        self.assertEqual(self.client.get("/admin/scene/story/").status_code, 200)

    def test_the_action_changelist_opens(self):
        self.assertEqual(self.client.get("/admin/scene/action/").status_code, 200)

    def test_the_scene_changelist_opens(self):
        self.assertEqual(self.client.get("/admin/scene/scene/").status_code, 200)

    def test_nothing_is_filtered_away(self):
        """No preference stated means no filtering, not an empty admin."""
        response = self.client.get("/admin/scene/action/")
        self.assertContains(response, "Panel")

    def test_the_admin_index_opens(self):
        self.assertEqual(self.client.get("/admin/").status_code, 200)


class ProfiledFilteringStillWorksTests(TestCase):
    """The guard must not quietly turn the feature off for users who DO have a profile."""

    @classmethod
    def setUpTestData(cls):
        cls.story = Story.objects.create(name="Book")
        cls.other_story = Story.objects.create(name="Other book")
        cls.scene = Scene.objects.create(name="Opening", story=cls.story, order=0)
        cls.other_scene = Scene.objects.create(name="Elsewhere", story=cls.other_story, order=0)
        Action.objects.create(name="Mine", scene=cls.scene, order=0)
        Action.objects.create(name="Theirs", scene=cls.other_scene, order=0)

    def setUp(self):
        self.user = U.objects.create_superuser("author", "a@x.test", "pw12345!")
        self.profile, _ = StoryProfile.objects.get_or_create(user=self.user)
        self.client.force_login(self.user)

    def test_a_current_story_still_filters_the_changelist(self):
        self.profile.story = self.story
        self.profile.save()
        response = self.client.get("/admin/scene/action/")
        self.assertContains(response, "Mine")
        self.assertNotContains(response, "Theirs")

    def test_no_current_story_shows_everything(self):
        response = self.client.get("/admin/scene/action/")
        self.assertContains(response, "Mine")
        self.assertContains(response, "Theirs")
