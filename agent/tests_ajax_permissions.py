"""The AJAX row endpoints, from the point of view of an account that should not be able to use them.

The admin's spend confirmations, cumulative caps and per-story scoping all sit on the changelist
actions. This endpoint sits beside them on the same models, so anything it fails to check is a
way around all of them at once.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase

from scene.models import Action, Scene, Story, StoryProfile

U = get_user_model()


class AjaxUpdatePermissionTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.story = Story.objects.create(name="Book")
        cls.scene = Scene.objects.create(name="M00", story=cls.story, order=0)
        cls.panel = Action.objects.create(name="panel", scene=cls.scene, order=0,
                                          prompt="the original prompt")

    def _staff(self, username, perms=()):
        user = U.objects.create_user(username, f"{username}@x.test", "pw12345!", is_staff=True)
        StoryProfile.objects.get_or_create(user=user)
        for codename in perms:
            user.user_permissions.add(Permission.objects.get(codename=codename))
        return U.objects.get(pk=user.pk)

    def _url(self, pk=None):
        return f"/admin/scene/action/ajax-update/{pk or self.panel.pk}/"

    def test_a_staff_account_with_no_permissions_cannot_write(self):
        """The exploit: 403 on the changelist, 200 here, and the row changed anyway."""
        self.client.force_login(self._staff("nobody"))
        self.assertEqual(self.client.get("/admin/scene/action/").status_code, 403)
        response = self.client.post(self._url(), {"prompt": "OVERWRITTEN"})
        self.panel.refresh_from_db()
        self.assertIn(response.status_code, (403, 302))
        self.assertEqual(self.panel.prompt, "the original prompt")

    def test_view_permission_alone_does_not_permit_writing(self):
        self.client.force_login(self._staff("looker", ["view_action"]))
        response = self.client.post(self._url(), {"prompt": "OVERWRITTEN"})
        self.panel.refresh_from_db()
        self.assertIn(response.status_code, (403, 302))
        self.assertEqual(self.panel.prompt, "the original prompt")

    def test_change_permission_still_works(self):
        """The endpoint must keep doing its job for the people it is for."""
        self.client.force_login(self._staff("editor", ["view_action", "change_action"]))
        response = self.client.post(self._url(), {"prompt": "a legitimate edit"})
        self.panel.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.panel.prompt, "a legitimate edit")

    def test_it_only_writes_fields_the_admin_declares_editable(self):
        """It used to save ANY posted key matching a model field."""
        self.client.force_login(self._staff("editor2", ["view_action", "change_action"]))
        before_name = self.panel.name
        self.client.post(self._url(), {"prompt": "fine", "name": "renamed behind the UI"})
        self.panel.refresh_from_db()
        self.assertEqual(self.panel.prompt, "fine")
        self.assertEqual(self.panel.name, before_name, "an undeclared field was written")

    def test_get_is_not_a_write(self):
        self.client.force_login(self._staff("editor3", ["view_action", "change_action"]))
        response = self.client.get(self._url(), {"prompt": "via GET"})
        self.panel.refresh_from_db()
        self.assertEqual(response.status_code, 405)
        self.assertEqual(self.panel.prompt, "the original prompt")

    def test_polling_a_row_needs_at_least_view_permission(self):
        self.client.force_login(self._staff("nobody2"))
        response = self.client.get(f"/admin/scene/action/ajax-last-tasks/{self.panel.pk}/")
        self.assertIn(response.status_code, (403, 302))

    def test_polling_works_with_view_permission(self):
        self.client.force_login(self._staff("looker2", ["view_action"]))
        response = self.client.get(f"/admin/scene/action/ajax-last-tasks/{self.panel.pk}/")
        self.assertEqual(response.status_code, 200)


class AjaxScopingTests(TestCase):
    """Story scoping applies here too, or it is not scoping."""

    @classmethod
    def setUpTestData(cls):
        cls.mine = Story.objects.create(name="Mine")
        cls.theirs = Story.objects.create(name="Theirs")
        cls.my_scene = Scene.objects.create(name="M", story=cls.mine, order=0)
        cls.their_scene = Scene.objects.create(name="T", story=cls.theirs, order=0)
        cls.their_panel = Action.objects.create(name="theirs", scene=cls.their_scene, order=0,
                                                prompt="not yours")

    def test_a_scoped_user_cannot_write_outside_their_story(self):
        user = U.objects.create_user("scoped", "s@x.test", "pw12345!", is_staff=True)
        for codename in ("view_action", "change_action"):
            user.user_permissions.add(Permission.objects.get(codename=codename))
        profile, _ = StoryProfile.objects.get_or_create(user=user)
        profile.story = self.mine
        profile.save()
        self.client.force_login(U.objects.get(pk=user.pk))
        response = self.client.post(
            f"/admin/scene/action/ajax-update/{self.their_panel.pk}/", {"prompt": "reached in"})
        self.their_panel.refresh_from_db()
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.their_panel.prompt, "not yours")
