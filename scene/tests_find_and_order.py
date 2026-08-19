"""Finding a panel, and getting the same order twice.

Both come from the same daily reality: a book is 604 panels long, the author's questions are
"which panels mention X" and "what order do these read in", and the admin could answer neither.
"""
from django.contrib import admin as dj_admin
from django.contrib.auth import get_user_model
from django.test import TestCase

from scene.models import Action, ComicAction, Scene, Story, StoryProfile

U = get_user_model()


class SearchTests(TestCase):
    """A panel is found by what is IN it far more often than by its name."""

    @classmethod
    def setUpTestData(cls):
        cls.story = Story.objects.create(name="Book")
        cls.scene = Scene.objects.create(name="M00", story=cls.story, order=0)
        cls.magenta = Action.objects.create(
            name="m0_stack", scene=cls.scene, order=0,
            prompt="a stack of coins lit in magenta, camera plumb")
        cls.lanyard = ComicAction.objects.create(
            name="m0_support", scene=cls.scene, order=1,
            prompt_comic="three identical agents in lanyards", text="we are here to help")
        cls.other = Action.objects.create(name="m0_quiet", scene=cls.scene, order=2,
                                          prompt="an empty room")

    def setUp(self):
        user = U.objects.create_superuser("boss", "b@x.test", "pw12345!")
        StoryProfile.objects.get_or_create(user=user)
        self.client.force_login(user)

    def _found(self, url, term):
        response = self.client.get(url, {"q": term})
        self.assertEqual(response.status_code, 200)
        return {obj.pk for obj in response.context["cl"].queryset}

    def test_a_panel_is_findable_by_a_word_in_its_prompt(self):
        """The 'which panels mention the banned colour' question, asked daily."""
        self.assertEqual(self._found("/admin/scene/action/", "magenta"), {self.magenta.pk})

    def test_a_comic_panel_is_findable_by_its_visual_description(self):
        self.assertIn(self.lanyard.pk, self._found("/admin/scene/comicaction/", "lanyards"))

    def test_a_comic_panel_is_still_findable_by_its_dialogue(self):
        self.assertIn(self.lanyard.pk, self._found("/admin/scene/comicaction/", "here to help"))

    def test_searching_by_name_still_works(self):
        self.assertEqual(self._found("/admin/scene/action/", "m0_quiet"), {self.other.pk})

    def test_a_word_in_no_panel_finds_nothing(self):
        self.assertEqual(self._found("/admin/scene/action/", "zeppelin"), set())


class StableOrderTests(TestCase):
    """`order` is not unique and nothing tie-breaks it.

    Measured on a real book: 604 panels, 530 distinct (scene, order) pairs, 61 groups covering
    135 panels. Ties have no defined order, so two requests can disagree about the sequence.
    """

    @classmethod
    def setUpTestData(cls):
        cls.story = Story.objects.create(name="Book")
        cls.scene = Scene.objects.create(name="M00", story=cls.story, order=0)
        cls.tied = [Action.objects.create(name=f"tied-{i}", scene=cls.scene, order=0)
                    for i in range(5)]

    def test_the_admin_orders_tied_panels_the_same_way_every_time(self):
        ma = dj_admin.site._registry[Action]
        self.assertEqual(tuple(ma.ordering), ("order", "id"))
        first = list(Action.objects.order_by(*ma.ordering).values_list("pk", flat=True))
        second = list(Action.objects.order_by(*ma.ordering).values_list("pk", flat=True))
        self.assertEqual(first, second)
        self.assertEqual(first, sorted(p.pk for p in self.tied))

    def test_every_shot_admin_shares_the_tie_break(self):
        for model in (Action, ComicAction):
            ma = dj_admin.site._registry[model]
            self.assertEqual(tuple(ma.ordering), ("order", "id"), f"{model.__name__} differs")

    def test_the_model_default_is_still_the_weaker_one(self):
        """Honest about scope: Meta.ordering keeps its gap, and fixing it needs a migration."""
        self.assertEqual(list(Action._meta.ordering), ["order"])
