"""Moving panels by naming a neighbour, and the renumbering that follows.

The friction: reading order is an integer typed by hand, and a revision pass is full of "move
this before that" -- fourteen in one recorded sweep, in scenes of thirty to forty-seven panels.
The correctness problem underneath: nothing keeps those integers distinct. On the book this
serves, 604 panels hold 530 distinct (scene, order) pairs, so 135 of them share a number with a
sibling and their relative order is whatever the database returns first.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase

from scene.models import Action, Scene, Story, StoryProfile
from scene.reorder import AFTER, BEFORE, ReorderError, move, renumber, ties

U = get_user_model()


class MoveTests(TestCase):

    def setUp(self):
        self.story = Story.objects.create(name="Book")
        self.scene = Scene.objects.create(name="M00", story=self.story, order=0)
        self.panels = [Action.objects.create(name=f"p{i}", scene=self.scene, order=i)
                       for i in range(5)]

    def _order(self, scene=None):
        return [a.name for a in Action.objects.filter(scene=scene or self.scene)
                .order_by("order", "id")]

    def test_moving_a_panel_before_another(self):
        move([self.panels[4]], self.panels[1], BEFORE)
        self.assertEqual(self._order(), ["p0", "p4", "p1", "p2", "p3"])

    def test_moving_a_panel_after_another(self):
        move([self.panels[0]], self.panels[3], AFTER)
        self.assertEqual(self._order(), ["p1", "p2", "p3", "p0", "p4"])

    def test_a_run_of_panels_keeps_its_shape(self):
        move([self.panels[3], self.panels[4]], self.panels[0], BEFORE)
        self.assertEqual(self._order(), ["p3", "p4", "p0", "p1", "p2"])

    def test_the_scene_is_left_numbered_zero_to_n_with_no_gaps(self):
        move([self.panels[4]], self.panels[0], BEFORE)
        orders = list(Action.objects.filter(scene=self.scene).order_by("order")
                      .values_list("order", flat=True))
        self.assertEqual(orders, list(range(5)))

    def test_moving_a_panel_next_to_itself_is_refused_with_a_reason(self):
        with self.assertRaises(ReorderError) as ctx:
            move([self.panels[2]], self.panels[2], BEFORE)
        self.assertIn("being moved", str(ctx.exception))

    def test_an_empty_selection_is_refused(self):
        with self.assertRaises(ReorderError):
            move([], self.panels[0], BEFORE)

    def test_a_missing_target_is_refused(self):
        with self.assertRaises(ReorderError) as ctx:
            move([self.panels[0]], None, BEFORE)
        self.assertIn("Choose the panel", str(ctx.exception))

    def test_an_unknown_position_is_refused(self):
        with self.assertRaises(ReorderError):
            move([self.panels[0]], self.panels[1], "sideways")


class TiedOrderTests(TestCase):
    """The measured defect: panels sharing a number."""

    def setUp(self):
        self.story = Story.objects.create(name="Book")
        self.scene = Scene.objects.create(name="M00", story=self.story, order=0)
        self.a = Action.objects.create(name="a", scene=self.scene, order=0)
        self.b = Action.objects.create(name="b", scene=self.scene, order=0)
        self.c = Action.objects.create(name="c", scene=self.scene, order=0)

    def test_ties_are_reported(self):
        self.assertEqual({p.name for p in ties(self.scene)}, {"a", "b", "c"})

    def test_renumbering_breaks_every_tie_and_keeps_creation_order(self):
        changed = renumber(self.scene)
        self.assertEqual(changed, 2)                      # a already sat at 0
        self.assertEqual([p.name for p in Action.objects.filter(scene=self.scene)
                          .order_by("order")], ["a", "b", "c"])
        self.assertEqual(ties(self.scene), [])

    def test_a_move_repairs_the_ties_it_finds(self):
        move([self.c], self.a, BEFORE)
        self.assertEqual(ties(self.scene), [])
        self.assertEqual([p.name for p in Action.objects.filter(scene=self.scene)
                          .order_by("order")], ["c", "a", "b"])


class CrossSceneTests(TestCase):

    def setUp(self):
        self.story = Story.objects.create(name="Book")
        self.one = Scene.objects.create(name="M00", story=self.story, order=0)
        self.two = Scene.objects.create(name="M01", story=self.story, order=1)
        self.a1 = Action.objects.create(name="a1", scene=self.one, order=0)
        self.a2 = Action.objects.create(name="a2", scene=self.one, order=1)
        self.b1 = Action.objects.create(name="b1", scene=self.two, order=0)
        self.b2 = Action.objects.create(name="b2", scene=self.two, order=1)

    def test_a_panel_can_move_into_another_scene(self):
        move([self.a2], self.b1, AFTER)
        self.a2.refresh_from_db()
        self.assertEqual(self.a2.scene, self.two)
        self.assertEqual([p.name for p in Action.objects.filter(scene=self.two)
                          .order_by("order")], ["b1", "a2", "b2"])

    def test_the_scene_it_left_is_closed_up(self):
        move([self.a1], self.b2, AFTER)
        orders = list(Action.objects.filter(scene=self.one).order_by("order")
                      .values_list("order", flat=True))
        self.assertEqual(orders, [0])                     # a2 moved down to 0, no hole at 0


class AdminActionTests(TestCase):
    """Through the admin, the way an author reaches it."""

    def setUp(self):
        self.story = Story.objects.create(name="Book")
        self.scene = Scene.objects.create(name="M00", story=self.story, order=0)
        self.panels = [Action.objects.create(name=f"p{i}", scene=self.scene, order=i)
                       for i in range(4)]
        user = U.objects.create_superuser("boss", "b@x.test", "pw12345!")
        StoryProfile.objects.get_or_create(user=user)
        self.client.force_login(user)

    def test_the_action_offers_a_form_listing_the_neighbours(self):
        response = self.client.post("/admin/scene/action/", {
            "action": "move_panels", "_selected_action": [str(self.panels[3].pk)]})
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("p0", html)
        self.assertNotIn('value="%d"' % self.panels[3].pk, html.split("<select")[-1])

    def test_submitting_the_form_moves_and_renumbers(self):
        response = self.client.post("/admin/scene/action/", {
            "action": "move_panels", "_selected_action": [str(self.panels[3].pk)],
            "reorder_submit": "1", "target": str(self.panels[0].pk), "where": "before"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual([p.name for p in Action.objects.filter(scene=self.scene)
                          .order_by("order")], ["p3", "p0", "p1", "p2"])

    def test_the_renumber_action_repairs_ties(self):
        for panel in self.panels:
            panel.order = 0
            panel.save()
        response = self.client.post("/admin/scene/action/", {
            "action": "renumber_panels",
            "_selected_action": [str(p.pk) for p in self.panels]})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ties(self.scene), [])
