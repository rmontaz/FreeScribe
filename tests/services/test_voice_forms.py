"""Talking through a web form: boxes from the page, labels from the form's picture, values from chart/profile/AI."""
import base64
import os
import sys

import pymupdf
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src", "FreeScribe.client"))

from services import form_filler as ff  # noqa: E402
from services import scan_form, voice_forms  # noqa: E402


@pytest.mark.parametrize("name,label", [
    ("patient_dob", "Patient dob"), ("referralReason", "Referral reason"), ("Text12", ""), ("input_3", ""),
    ("cb2", ""), ("t", ""), ("", ""), ("RX", ""), ("work_restrictions", "Work restrictions"),
])
def test_name_label(name, label):
    assert voice_forms.name_label(name) == label


def test_fields_from_page():
    fields = voice_forms.fields_from_page([
        {"id": "0", "kind": "text", "label": "Diagnosis", "multiline": True},
        {"id": "1", "kind": "text", "name": "Text7"},
        {"id": "2", "kind": "radio", "label": "Able to work", "options": ["Y", "N"], "option_labels": ["Yes", "No"]},
        {"id": "3", "kind": "choice", "label": "Empty choice", "options": []},
        {"id": "4", "kind": "button"},
    ])
    assert [f.name for f in fields] == ["0", "1", "2"]
    assert fields[1].label == "" and fields[2].labels() == ["Yes", "No"]
    with pytest.raises(voice_forms.VoiceFormError):
        voice_forms.fields_from_page([])


def _picture():
    """A paper form as a picture (what many eForms show), and where its boxes are, in pixels."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 120), "Patient name:", fontsize=12)
    page.draw_line((160, 122), (500, 122))
    page.insert_text((72, 180), "Diagnosis:", fontsize=12)
    page.draw_line((140, 182), (500, 182))
    page.insert_text((72, 240), "Fit for work:", fontsize=12)
    page.draw_rect(pymupdf.Rect(220, 230, 230, 240))
    page.insert_text((235, 240), "Yes", fontsize=12)
    page.draw_rect(pymupdf.Rect(270, 230, 280, 240))
    page.insert_text((285, 240), "No", fontsize=12)
    zoom = 2.0
    png = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).tobytes("png")
    px = lambda r: [v * zoom for v in r]   # noqa: E731
    items = [
        {"id": "a", "kind": "text", "name": "Text1", "picture": 0, "rect": px((162, 108, 498, 121))},
        {"id": "b", "kind": "text", "name": "Text2", "picture": 0, "rect": px((142, 168, 498, 181))},
        {"id": "c", "kind": "radio", "name": "r1", "options": ["1", "2"], "picture": 0,
         "option_rects": [px((220, 230, 230, 240)), px((270, 230, 280, 240))]},
    ]
    return items, [{"data": base64.b64encode(png).decode(), "width": 612 * zoom}]


@pytest.mark.skipif(not scan_form.available(), reason="text recognition (Tesseract data) not available")
def test_labels_from_the_picture():
    items, pictures = _picture()
    fields = voice_forms.fields_from_page(items)
    found = voice_forms.labels_from_pictures(fields, items, pictures)
    labels = {f.name: f.label for f in fields}
    assert found >= 2
    assert "patient name" in labels["a"].lower()
    assert "diagnosis" in labels["b"].lower()
    assert [o.lower() for o in fields[2].labels()] == ["yes", "no"]
    assert "work" in fields[2].label.lower()


def test_fill_takes_chart_and_profile_before_the_ai():
    fields = voice_forms.fields_from_page([
        {"id": "n", "kind": "text", "label": "Patient name"},
        {"id": "d", "kind": "text", "label": "Diagnosis"},
        {"id": "doc", "kind": "text", "label": "Physician name"},
        {"id": "u", "kind": "text", "name": "Text9"},
        {"id": "w", "kind": "checkbox", "label": "Off work"},
        {"id": "p", "kind": "text", "label": "Physician:"},
        {"id": "r", "kind": "text", "label": "Referring physician"},
    ])
    asked = []

    def ask(group):
        asked.extend(f.name for f in group)
        return {"d": "Lumbar strain", "w": "yes", "n": "Wrong Name"}, []

    values, problems = voice_forms.fill(fields, ask, patient={"first_name": "Ann", "last_name": "Lee"},
                                        profile={"name": "Dr. M"})
    assert values["n"] == "Ann Lee" and values["doc"] == "Dr. M" and values["p"] == "Dr. M"
    assert "p" not in asked and "r" in asked      # another doctor's box is not the clinician's
    assert values["d"] == "Lumbar strain" and values["w"] == "yes"
    assert "u" not in asked and "n" not in asked and "doc" not in asked
    assert problems == []
