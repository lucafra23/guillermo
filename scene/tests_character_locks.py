"""Character consistency across a long book, and the guarantee that it changes nothing by default.

The second half matters as much as the first: this feature alters what is sent to a paid image
model, so "off means byte-identical" is the property that makes it safe to merge.
"""
from django.test import TestCase, override_settings

from scene.character_locks import OFF, TEXT, TEXT_AND_IMAGE, contents_for, mode
from scene.models import Action, Character, ComicAction, Scene, Story


class ModeTests(TestCase):

    def test_it_is_off_unless_configured(self):
        with override_settings(COMIC_CHARACTER_CONTEXT=None):
            self.assertEqual(mode(), OFF)

    def test_nonsense_is_treated_as_off_not_as_on(self):
        """A typo in a deployment setting must not start spending differently."""
        with override_settings(COMIC_CHARACTER_CONTEXT="yes please"):
            self.assertEqual(mode(), OFF)

    def test_case_and_padding_do_not_matter(self):
        with override_settings(COMIC_CHARACTER_CONTEXT="  TEXT  "):
            self.assertEqual(mode(), TEXT)


class ContentsTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.story = Story.objects.create(name="Book")
        cls.scene = Scene.objects.create(name="M00", story=cls.story, order=0)
        cls.maxi = Character.objects.create(
            name="Maxi", story=cls.story,
            prompt="a tall woman with cropped silver hair and a canvas jacket")
        cls.founder = Character.objects.create(
            name="Founder", story=cls.story,
            prompt="a wiry man in a rumpled grey hoodie")
        cls.extra = Character.objects.create(name="Nameless extra", story=cls.story, prompt="")

    def _panel(self, actor=None, cast=()):
        panel = ComicAction.objects.create(name="p", scene=self.scene, order=0,
                                           prompt_comic="two people argue over a bag")
        if actor:
            panel.actor = actor
            panel.save()
        for c in cast:
            panel.cast.add(c)
        return panel

    @override_settings(COMIC_CHARACTER_CONTEXT="off")
    def test_off_adds_nothing(self):
        panel = self._panel(actor=self.maxi, cast=[self.founder])
        self.assertEqual(contents_for(panel), [])

    @override_settings(COMIC_CHARACTER_CONTEXT="text")
    def test_text_mode_sends_the_canonical_description(self):
        panel = self._panel(actor=self.maxi, cast=[self.founder])
        parts = contents_for(panel)
        joined = " ".join(str(p) for p in parts)
        self.assertIn("cropped silver hair", joined)
        self.assertIn("rumpled grey hoodie", joined)
        self.assertIn("Maxi", joined)
        self.assertTrue(all(isinstance(p, str) for p in parts), "text mode must not send images")

    @override_settings(COMIC_CHARACTER_CONTEXT="text")
    def test_the_actor_comes_first_and_is_not_repeated(self):
        panel = self._panel(actor=self.maxi, cast=[self.maxi, self.founder])
        described = [p for p in contents_for(panel) if str(p).startswith("Character ")]
        self.assertEqual(len(described), 2, described)
        self.assertIn("Maxi", str(described[0]))

    @override_settings(COMIC_CHARACTER_CONTEXT="text")
    def test_a_character_with_no_description_contributes_nothing(self):
        """Padding a paid prompt with blank lines is worse than sending less."""
        panel = self._panel(cast=[self.extra])
        self.assertEqual(contents_for(panel), [])

    @override_settings(COMIC_CHARACTER_CONTEXT="text")
    def test_the_order_is_stable_across_calls(self):
        """An identical panel must send an identical prompt, or reruns look like model noise."""
        panel = self._panel(actor=self.maxi, cast=[self.founder, self.extra])
        self.assertEqual([str(p) for p in contents_for(panel)],
                         [str(p) for p in contents_for(panel)])

    @override_settings(COMIC_CHARACTER_CONTEXT="text+image")
    def test_image_mode_attaches_the_reference_plate(self):
        from filer.models.imagemodels import Image as FilerImage
        plate = FilerImage.objects.create(original_filename="maxi.png", name="maxi")
        self.maxi.image = plate
        self.maxi.save()
        panel = self._panel(actor=self.maxi)
        parts = contents_for(panel)
        self.assertIn(plate, parts)

    @override_settings(COMIC_CHARACTER_CONTEXT="text")
    def test_a_panel_with_nobody_in_it_is_unchanged(self):
        self.assertEqual(contents_for(self._panel()), [])


class GetContentsIntegrationTests(TestCase):
    """The wiring: what Action.get_contents actually hands the image agent."""

    @classmethod
    def setUpTestData(cls):
        from scene.models import Style
        # A style, because Scene.get_contents dereferences story.style unguarded on the
        # non-comic presets. That is a separate upstream bug; it is not this branch's to fix,
        # and these tests are about the comic path.
        cls.story = Story.objects.create(name="Book", style=Style.objects.create(name="ink"))
        cls.scene = Scene.objects.create(name="M00", story=cls.story, order=0)
        cls.maxi = Character.objects.create(name="Maxi", story=cls.story,
                                            prompt="cropped silver hair, canvas jacket")

    def _panel(self):
        panel = ComicAction.objects.create(name="p", scene=self.scene, order=0,
                                           prompt_comic="she opens the bag")
        panel.actor = self.maxi
        panel.save()
        return panel

    @override_settings(COMIC_CHARACTER_CONTEXT="off")
    def test_the_comic_prompt_is_unchanged_when_off(self):
        panel = self._panel()
        contents = panel.get_contents(generate_self=True, preset=Action.PRESET_COMIC)
        self.assertFalse(any("cropped silver hair" in str(p) for p in contents))

    @override_settings(COMIC_CHARACTER_CONTEXT="text")
    def test_the_description_reaches_the_comic_prompt_when_on(self):
        panel = self._panel()
        contents = panel.get_contents(generate_self=True, preset=Action.PRESET_COMIC)
        self.assertTrue(any("cropped silver hair" in str(p) for p in contents),
                        f"description missing from {contents}")

    @override_settings(COMIC_CHARACTER_CONTEXT="text")
    def test_other_presets_are_untouched(self):
        """Only the comic lane was missing this; the others already send their cast."""
        panel = self._panel()
        before = panel.get_contents(generate_self=True, preset=Action.PRESET_IMAGE)
        with override_settings(COMIC_CHARACTER_CONTEXT="off"):
            after = panel.get_contents(generate_self=True, preset=Action.PRESET_IMAGE)
        self.assertEqual([str(p) for p in before], [str(p) for p in after])

    @override_settings(COMIC_CHARACTER_CONTEXT="text")
    def test_the_panels_own_words_come_last_and_therefore_win(self):
        """A standing description must not outrank the panel in front of it.

        Later parts weigh more (Character.get_contents: "last so it is more important"). If the
        cast description trails the panel's prompt, a panel rewritten to ask for something the
        description contradicts keeps returning the description's version -- measured in
        production as a panel asking for three men and receiving a lizard, because the standing
        character text still said lizard and outranked it.
        """
        panel = self._panel()
        contents = [str(p) for p in panel.get_contents(generate_self=True,
                                                       preset=Action.PRESET_COMIC)]
        described = next(i for i, p in enumerate(contents) if "cropped silver hair" in p)
        own_words = next(i for i, p in enumerate(contents) if "she opens the bag" in p)
        self.assertLess(described, own_words,
                        "the cast description outranks the panel's own instruction")
