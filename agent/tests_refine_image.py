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

from django.test import SimpleTestCase

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
