"""Tests for refine_image keeping the plate it replaces.

refine_image is a PAID generation that writes to `image`, and it sits directly next to
"Revert to previous plate" in the same admin action list. That adjacency is a promise:
a reader takes the presence of revert to mean refine is covered by it. It was not --
refine assigned self.image directly and the approved plate was gone.

SimpleTestCase and a stub: none of this needs the database. The mixin's contract here
is entirely about which method it delegates to, and a stub records that far more
plainly than a real model with a filer row behind it would.
"""
import inspect

from django.test import SimpleTestCase, TestCase

from agent.models import GetContentsMixin


class Plate:
    """Stands in for a filer Image; identity is all these tests compare."""

    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return f"<Plate {self.name}>"


class RefineProbe(GetContentsMixin):
    """A minimal host for the mixin that records what refine_image did."""

    PRESET_REFINE = "refine"

    def __init__(self, generates=None):
        self.image = Plate("approved")
        self.previous_image = None
        self.saved = False
        self._generates = generates

    # -- the parts of the host the mixin reaches for --------------------------------
    def get_agent(self, output_type):
        return self

    def generate(self, *args, **kwargs):
        return self._generates

    def save(self, *args, **kwargs):
        self.saved = True

    def set_image_keeping_previous(self, new_image, save=True):
        self.previous_image = self.image
        self.image = new_image
        self.saved = save
        return new_image


class RefineImageTests(SimpleTestCase):

    def test_only_one_refine_image_is_defined(self):
        """A second, identical refine_image used to sit further down the same class.

        Python keeps only the later definition, so the earlier one was dead -- and it
        is the one a reader finds first, so a fix applied there did nothing at all.
        This test fails if the duplicate ever comes back.
        """
        source = inspect.getsource(GetContentsMixin)
        self.assertEqual(
            source.count("def refine_image("), 1,
            "more than one refine_image is defined; only the last one runs")

    def test_the_replaced_plate_is_kept(self):
        """The whole point: revert must have something to revert to."""
        probe = RefineProbe(generates=Plate("refined"))
        approved = probe.image

        probe.refine_image()

        self.assertEqual(probe.image.name, "refined")
        self.assertIs(probe.previous_image, approved,
                      "refine destroyed the approved plate with no way back")

    def test_it_delegates_rather_than_assigning(self):
        """Guards the mechanism, not just the outcome: assigning self.image directly
        would leave previous_image untouched and this test red."""
        source = inspect.getsource(GetContentsMixin.refine_image)
        self.assertIn("set_image_keeping_previous", source)
        self.assertNotIn("self.image = out", source)

    def test_a_failed_generation_changes_nothing(self):
        """A refusal or a rate limit must not blank the existing plate."""
        probe = RefineProbe(generates=None)
        approved = probe.image

        probe.refine_image()

        self.assertIs(probe.image, approved)
        self.assertIsNone(probe.previous_image)
        self.assertFalse(probe.saved)

    def test_save_false_does_not_persist(self):
        probe = RefineProbe(generates=Plate("refined"))
        approved = probe.image

        probe.refine_image(save=False)

        self.assertIs(probe.image, approved, "save=False must not swap the plate")
        self.assertFalse(probe.saved)


class PreviousPlateIsVisibleTests(TestCase):
    """The plate a generation replaced has to be lookable-at, not only revertible-to.

    `previous_image` backs "Revert to previous plate", and was rendered nowhere: the only way to
    see what a re-roll replaced was to revert, look, and revert back -- writing twice to answer a
    question about a picture. The standing rule in the book this serves is to diff a new face
    against one already approved BEFORE arming it; that rule needs the old face on screen.
    """

    def _plate(self, name):
        """A filer row with a real file behind it: filer's .url is '' without one."""
        import os

        from django.conf import settings
        from filer.models.imagemodels import Image as FilerImage
        from PIL import Image as PILImage

        rel = f"plates/{name}.png"
        absolute = os.path.join(settings.MEDIA_ROOT, rel)
        os.makedirs(os.path.dirname(absolute), exist_ok=True)
        PILImage.new("RGB", (8, 8), (1, 2, 3)).save(absolute)
        return FilerImage.objects.create(original_filename=f"{name}.png", file=rel, name=name)

    def _panel(self):
        from scene.models import Action, Scene, Story
        story = Story.objects.create(name="Book")
        scene = Scene.objects.create(name="M00", story=story, order=0)
        panel = Action.objects.create(name="p", scene=scene, order=0)
        return panel, self._plate("old"), self._plate("new")

    def test_a_panel_with_no_history_says_so_plainly(self):
        panel, _old, _new = self._panel()
        self.assertIn("No previous plate", str(panel.pic_previous()))

    def test_the_replaced_plate_is_rendered_after_a_generation(self):
        panel, old, new = self._panel()
        panel.image = old
        panel.save()
        panel.set_image_keeping_previous(new)
        panel.refresh_from_db()
        markup = str(panel.pic_previous())
        self.assertIn("<img", markup)
        self.assertIn(old.url, markup)

    def test_it_carries_no_write_affordance(self):
        """previous_image is editable=False; the image menu must not bind to it."""
        panel, old, new = self._panel()
        panel.image = old
        panel.save()
        panel.set_image_keeping_previous(new)
        markup = str(panel.refresh_from_db() or panel.pic_previous())
        self.assertNotIn("image-menu-container", markup)
        self.assertNotIn('data-field="previous_image"', markup)

    def test_it_is_on_the_panel_change_form(self):
        from scene.mixins import ACTION_FIELDSETS
        fields = [f for _label, opts in ACTION_FIELDSETS for f in opts["fields"]]
        self.assertIn("pic_previous", fields)

    def test_it_is_on_the_character_and_prop_change_form(self):
        from scene.mixins import ELEMENT_FIELDSETS
        fields = [f for _label, opts in ELEMENT_FIELDSETS for f in opts["fields"]]
        self.assertIn("pic_previous", fields)
