"""Tests for filling flat PDF forms (no form fields) by finding their blanks.

This software is released under the AGPL-3.0 license.
"""
import os

import pytest

pytest.importorskip("pymupdf")
from pypdf import PdfReader  # noqa: E402

from services import flat_form  # noqa: E402
from services import form_filler as ff  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures")
INTAKE = os.path.join(FIXTURES, "flat_intake_form.pdf")
SCANNED = os.path.join(FIXTURES, "scanned_form.pdf")
FILLABLE = os.path.join(FIXTURES, "work_absence_form.pdf")


@pytest.fixture(scope="module")
def blanks():
    return flat_form.find_blanks(INTAKE)


class TestFindBlanks:
    def test_every_blank_is_found_in_reading_order(self, blanks):
        assert [b.label for b in blanks] == [
            "Patient name",                 # underscores after a label
            "Date of birth", "Phone",       # two blanks on one line
            "Health card number",           # a drawn line
            "Allergies: None known", "Allergies: Penicillin", "Allergies: Other",   # tick boxes
            "Reason for visit",             # label above an empty box
            "Current medications",          # label inside the box
            "Off work from", "Off work from … to",   # a range on one line
            "Clinician signature",          # label under a signature line
            "Date",
        ]

    def test_kinds_and_sizes(self, blanks):
        by_label = {b.label: b for b in blanks}
        assert by_label["Allergies: Penicillin"].kind == flat_form.CHECKBOX
        assert by_label["Reason for visit"].multiline and by_label["Current medications"].multiline
        assert not by_label["Patient name"].multiline
        # The field sits on the line, not over the label
        line = by_label["Health card number"].rect
        assert line[0] == pytest.approx(190, abs=1) and line[2] == pytest.approx(500, abs=1)
        # The box's own label is left above the field inside it
        meds = by_label["Current medications"].rect
        assert meds[1] > 338

    def test_scanned_detection(self):
        assert flat_form.is_scanned(SCANNED)
        assert not flat_form.is_scanned(INTAKE)

    def test_duplicate_labels_are_numbered(self):
        blanks = [flat_form.Blank(0, (0, 0, 1, 1), label="Date"), flat_form.Blank(0, (0, 2, 1, 3), label="Date")]
        flat_form._make_labels_unique(blanks)
        assert [b.label for b in blanks] == ["Date", "Date (2)"]


class TestPrepareForm:
    def test_fillable_forms_are_used_as_they_are(self):
        prepared = ff.prepare_form(FILLABLE)
        assert not prepared.converted and prepared.fill_path == FILLABLE
        assert len(prepared.fields) == 13

    def test_flat_forms_get_fields(self, tmp_path):
        prepared = ff.prepare_form(INTAKE, work_dir=str(tmp_path))
        assert prepared.converted and prepared.original_path == INTAKE
        assert prepared.fill_path.startswith(str(tmp_path))
        labels = {f.label: f for f in prepared.fields}
        assert len(prepared.fields) == 13
        assert labels["Allergies: Other"].kind == ff.CHECKBOX
        assert labels["Reason for visit"].multiline
        assert labels["Off work from … to"].line == "Off work from … to …"

    def test_original_is_untouched(self, tmp_path):
        before = open(INTAKE, "rb").read()
        ff.prepare_form(INTAKE, work_dir=str(tmp_path))
        assert open(INTAKE, "rb").read() == before
        assert not PdfReader(INTAKE).get_fields()

    def test_filled_flat_form(self, tmp_path):
        prepared = ff.prepare_form(INTAKE, work_dir=str(tmp_path))
        wanted = {"Patient name": "Maria Lopez", "Allergies: Penicillin": "yes",
                  "Reason for visit": "Lower back pain.\nStarted Friday.", "Off work from … to": "2026-10-20"}
        values = {f.name: wanted.get(f.label, "") for f in prepared.fields}
        out = tmp_path / "filled.pdf"
        ff.fill_pdf(prepared.fill_path, str(out), prepared.fields, values)

        result = {f.label: f.existing for f in ff.read_form(str(out))}
        assert result["Patient name"] == "Maria Lopez"
        assert result["Allergies: Penicillin"] == "Yes"
        assert result["Allergies: Other"] == ""
        assert result["Reason for visit"] == "Lower back pain.\nStarted Friday."
        assert result["Off work from … to"] == "2026-10-20"
        fonts = PdfReader(str(out)).trailer["/Root"]["/AcroForm"]["/DR"]["/Font"]
        assert "/Helv" in fonts and "/ZaDb" in fonts

    def test_scanned_form_is_explained(self):
        with pytest.raises(ff.FormError, match="scanned image"):
            ff.prepare_form(SCANNED)

    def test_page_without_blanks(self, tmp_path):
        import pymupdf
        path = tmp_path / "letter.pdf"
        doc = pymupdf.open()
        doc.new_page().insert_text((72, 72), "Dear colleague, thank you for seeing this patient.")
        doc.save(str(path))
        with pytest.raises(ff.FormError, match="could not find any blanks"):
            ff.prepare_form(str(path))


def test_word_processor_form_with_tables():
    """A form typed in a word processor: a ruled table, a one-cell box, ☐ characters."""
    blanks = flat_form.find_blanks(os.path.join(FIXTURES, "flat_word_table_form.pdf"))
    assert [(b.label, b.kind, b.multiline) for b in blanks] == [
        ("Patient name", "text", False),           # empty cell right of its label cell
        ("Date of birth", "text", False),
        ("Health card number", "text", False),
        ("Phone", "text", False),
        ("Referring clinician", "text", False),
        ("Date of referral", "text", False),
        ("Diagnosis", "text", False),
        ("Reason for referral / goals of treatment", "text", True),   # one-cell table under its question
        ("Urgency: Routine", "checkbox", False),
        ("Urgency: Urgent (within 1 week)", "checkbox", False),
        ("Precautions: None", "checkbox", False),
        ("Precautions: Weight bearing restrictions", "checkbox", False),
        ("Precautions: Other", "checkbox", False),
        ("Signature", "text", False),
        ("Date", "text", False),
    ]


def test_tick_boxes_of_every_kind_and_rows_of_character_boxes():
    """Drawn squares, radio circles, symbol-font characters, four-line boxes; character-box rows merge."""
    blanks = flat_form.find_blanks(os.path.join(FIXTURES, "flat_checkbox_form.pdf"), keep_unlabelled=True)
    by_label = {b.label: b for b in blanks}
    assert len(blanks) == 27
    assert [b.kind for b in blanks].count(flat_form.CHECKBOX) == 21
    for label in ("Sex: Male", "Sex: Female",        # circles
                  "Urgent: Yes", "Urgent: No",        # ZapfDingbats characters
                  "Fasting: Yes", "Fasting: No",      # four separate lines
                  "Glucose", "Urinalysis"):           # drawn squares
        assert by_label[label].kind == flat_form.CHECKBOX, label
    card = by_label["Health card number"]
    assert card.kind == flat_form.TEXT and card.comb == 10
    assert by_label["Version"].comb == 2
    assert [by_label[k].comb for k in ("Date of birth", "Date of birth (2)", "Date of birth (3)")] == [4, 2, 2]


def test_unlabelled_blanks_are_dropped_for_form_filling(tmp_path):
    import pymupdf
    path = tmp_path / "lines.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.draw_line((72, 200), (300, 200))          # a line with no words anywhere near it
    page.insert_text((72, 100), "Patient name: ______________")
    doc.save(str(path))
    assert [b.label for b in flat_form.find_blanks(str(path))] == ["Patient name"]
    assert len(flat_form.find_blanks(str(path), keep_unlabelled=True)) == 2
