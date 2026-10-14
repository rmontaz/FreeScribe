"""
Tests for reading scanned and photographed forms (OCR plus boxes and lines from pixels).

The test pages are the computer-made test forms turned into pictures the way a
scanner would: grey, slightly tilted, blurred, speckled and JPEG-compressed. The
fields found in the picture are compared with the fields read from the original PDF.

This software is released under the AGPL-3.0 license.
"""
import base64
import io
import os
import random

import numpy as np
import pytest

pymupdf = pytest.importorskip("pymupdf")

from services import eform_mapping as em  # noqa: E402
from services import scan_form  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures")
pytestmark = pytest.mark.skipif(not scan_form.available(), reason="OCR data not installed")


def scanned(name, tilt=0.5, dpi=200, noise=10, quality=70):
    """The first page of a test form as a scan-like JPEG."""
    from PIL import Image, ImageFilter
    pix = pymupdf.open(os.path.join(FIXTURES, name))[0].get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    image = Image.frombytes("L", (pix.width, pix.height), pix.samples)
    image = image.rotate(tilt, resample=Image.BICUBIC, fillcolor=255).filter(ImageFilter.GaussianBlur(0.7))
    rng = np.random.default_rng(1)
    pixels = np.asarray(image, dtype=np.float32) * 0.92 + 12 + rng.normal(0, noise, (pix.height, pix.width))
    buffer = io.BytesIO()
    Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8)).save(buffer, "JPEG", quality=quality)
    return buffer.getvalue(), pix.width, pix.height


def from_pdf(name, width, height):
    with open(os.path.join(FIXTURES, name), "rb") as handle:
        data = base64.b64encode(handle.read()).decode()
    return em.detect({"pdf": data, "pages": [{"page": 1, "pdfPage": 1, "naturalW": width, "naturalH": height}]})


def from_image(data):
    return em.detect({"images": [{"page": 1, "data": "data:image/jpeg;base64," + base64.b64encode(data).decode()}]})


def overlap(a, b):
    x0, y0 = max(a["x"], b["x"]), max(a["y"], b["y"])
    x1, y1 = min(a["x"] + a["width"], b["x"] + b["width"]), min(a["y"] + a["height"], b["y"] + b["height"])
    inter = max(0, x1 - x0) * max(0, y1 - y0)
    return inter / (a["width"] * a["height"] + b["width"] * b["height"] - inter)


def matches(truth, found):
    """Fields of ``truth`` that have a field of the same kind at the same place in ``found``."""
    kind = lambda f: f["type"] == "checkbox"  # noqa: E731
    hits = []
    for t in truth:
        best = max((f for f in found if kind(f) == kind(t)), key=lambda f: overlap(t, f), default=None)
        if best is not None and overlap(t, best) >= 0.3:
            hits.append((t, best))
    return hits


@pytest.mark.parametrize("name", ["work_absence_form.pdf", "flat_intake_form.pdf", "flat_checkbox_form.pdf",
                                  "flat_word_table_form.pdf"])
def test_scan_finds_the_same_fields_as_the_pdf(name):
    data, width, height = scanned(name)
    truth = from_pdf(name, width, height)["fields"]
    result = from_image(data)
    found = result["fields"]
    hits = matches(truth, found)
    assert len(hits) >= 0.85 * len(truth)
    assert len(found) - len(hits) <= 2                      # hardly anything that is not a field
    assert result["summary"]["pagesReadWithOcr"] == 1
    # Labels are read from the picture
    same = sum(1 for t, f in hits if t["label"].split(" (")[0].lower() == f["label"].split(" (")[0].lower())
    assert same >= 0.8 * len(hits)


def test_scan_gets_tags_from_its_labels():
    data, _w, _h = scanned("work_absence_form.pdf", tilt=-0.8)
    tags = {f["oscarTag"] for f in from_image(data)["fields"]}
    assert {"patient_name", "dobc2", "hin", "phone", "current_user_signature_image"} <= tags


def test_tilted_scan_lands_on_the_tilted_boxes():
    import math
    data, width, height = scanned("work_absence_form.pdf", tilt=1.0)
    level = {f["label"]: f for f in from_pdf("work_absence_form.pdf", width, height)["fields"]}["Patient name"]
    found = {f["label"]: f for f in from_image(data)["fields"]}["Patient name"]
    # Where the centre of the box went when the page was turned 1 degree anticlockwise
    a = math.radians(1.0)
    dx = level["x"] + level["width"] / 2 - width / 2
    dy = level["y"] + level["height"] / 2 - height / 2
    tx = width / 2 + dx * math.cos(a) + dy * math.sin(a)
    ty = height / 2 - dx * math.sin(a) + dy * math.cos(a)
    assert abs(found["x"] + found["width"] / 2 - tx) < 8 and abs(found["y"] + found["height"] / 2 - ty) < 8


def test_png_page_with_transparency():
    from PIL import Image
    data, _w, _h = scanned("flat_intake_form.pdf", tilt=0, noise=0, quality=95)
    image = Image.open(io.BytesIO(data)).convert("LA")
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    result = em.detect({"images": [{"page": 3, "data": base64.b64encode(buffer.getvalue()).decode()}]})
    assert {f["page"] for f in result["fields"]} == {3}
    assert "Patient name" in {f["label"] for f in result["fields"]}


def test_unreadable_image():
    with pytest.raises(em.MappingError, match="could not be read"):
        em.detect({"images": [{"page": 1, "data": base64.b64encode(b"not an image").decode()}]})


@pytest.mark.parametrize("word, fixed", [
    ("Patent", "Patient"), ("Chnician", "Clinician"), ("Heaith", "Health"), ("SIGNATURF", "SIGNATURE"),
    ("Glucese,", "Glucose,"), ("Smith", "Smith"), ("Montreal", "Montreal"), ("cec", "cec"), ("name", "name"),
])
def test_respell(word, fixed):
    assert scan_form.respell(word) == fixed


def test_box_readings_are_not_text():
    for reading in ("CJ", "LJ", "CL]", "O", "[]", "0"):
        assert not scan_form._is_text(reading)
    for word in ("No", "Yes", "CBC", "TSH", "Other"):
        assert scan_form._is_text(word)


def test_tilt_is_measured():
    from PIL import Image
    data, _w, _h = scanned("work_absence_form.pdf", tilt=1.5, noise=0)
    gray = np.asarray(Image.open(io.BytesIO(data)).convert("L"))
    assert abs(scan_form._tilt(scan_form._ink(gray)) + 1.5) <= 0.15     # the turn that levels it
