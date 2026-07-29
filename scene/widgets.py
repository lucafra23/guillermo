"""Admin widgets for lettering.

The lettering spec is fractional geometry: `{"type", "text", "box": [x, y, w, h], "tail": [x, y]}`
with every coordinate a fraction of the image. That is the right storage format — it re-letters at
any resolution — and a terrible editing format. Nobody can look at `[0.28, 0.14, 0.66, 0.17]` and
say whether the balloon covers a character's face.

Editing it as raw JSON was tried in this project and produced exactly the failure you would expect:
positions guessed without looking at the plate moved tails off mouths and parked boxes on top of
heads. So the widget puts the plate behind the boxes and lets them be dragged, and the preview
button composites through the real letterer rather than approximating it in the browser — what you
approve is what gets written.
"""
import json

from django import forms
from django.utils.safestring import mark_safe


class LetteringWidget(forms.Widget):
    """Visual editor for an Action.lettering spec, drawn over the panel's own plate."""

    template_name = "scene/lettering_widget.html"

    class Media:
        css = {"all": ("css/lettering_editor.css",)}
        js = ("js/lettering_editor.js",)

    def __init__(self, attrs=None, plate_url=None, preview_url=None, action_id=None):
        super().__init__(attrs)
        self.plate_url = plate_url
        self.preview_url = preview_url
        self.action_id = action_id

    def format_value(self, value):
        if value in (None, ""):
            return "[]"
        if isinstance(value, str):
            return value
        return json.dumps(value)

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        context["widget"].update({
            "value": self.format_value(value),
            "plate_url": self.plate_url or "",
            "preview_url": self.preview_url or "",
            "action_id": self.action_id or "",
        })
        return context

    # NOTE: deliberately NO value_from_datadict override.
    #
    # An earlier version parsed the JSON here and returned a Python object. That breaks the admin
    # in a way no unit test of the method could show: forms.JSONField.bound_data does json.loads()
    # on whatever this returns, so a list raised TypeError, and BoundField.value() calls bound_data
    # on EVERY render of a bound form — i.e. every time the admin re-displays the page because
    # something failed validation. The result was a 500 on any validation error on this form,
    # including the lettering errors this widget exists to report, with the author's dragged
    # balloons discarded. A typo in an unrelated field cost the whole edit too.
    #
    # Django's own JSONField already does the right thing: it parses in to_python, and on failure
    # keeps the raw text as InvalidJSONInput so the form re-renders with what the user typed rather
    # than resetting the panel to empty. The override added nothing.


class LetteringFormField(forms.JSONField):
    """JSONField that validates against the compositor's own rules, not just 'is it JSON'."""

    def clean(self, value):
        value = super().clean(value)
        from .lettering import LetteringError, normalise_elements

        if value in (None, "", [], {}):
            return value
        try:
            normalise_elements(value)
        except LetteringError as e:
            # Surface the compositor's own message: it names the element index and the reason,
            # which is what the author needs to fix it.
            raise forms.ValidationError(str(e))
        return value
