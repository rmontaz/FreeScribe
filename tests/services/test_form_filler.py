"""Tests for filling PDF forms from a session.

This software is released under the AGPL-3.0 license.
"""
import datetime as dt
import re
import os

import pytest
from pypdf import PdfReader

from services import form_filler as ff

FIXTURES = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures")
FORM = os.path.join(FIXTURES, "work_absence_form.pdf")
FLAT = os.path.join(FIXTURES, "flat_form.pdf")


@pytest.fixture(scope="module")
def fields():
    return ff.read_form(FORM)


def by_name(fields):
    return {f.name: f for f in fields}


class TestReadForm:
    def test_labels_come_from_tooltip_or_page_text(self, fields):
        labels = {f.name: f.label for f in fields}
        assert labels["Text1"] == "Patient name"                         # text to the left
        assert labels["hcn"] == "Ontario health card number (10 digits)"  # tooltip wins
        assert labels["topmostSubform[0].Page1[0].f1_06[0]"] == "Diagnosis / reason for absence"  # above
        assert labels["absence_to"] == "Unable to work from … to"        # short label gets the line start
        assert labels["fit_to_return"] == "Fit to return to full duties"  # checkbox label on the right
        assert labels["followup"] == "Follow-up required"

    def test_kinds_and_options(self, fields):
        f = by_name(fields)
        assert f["fit_to_return"].kind == ff.CHECKBOX and f["fit_to_return"].on_state == "/Yes"
        assert f["followup"].kind == ff.RADIO and f["followup"].options == ["Yes", "No"]
        assert f["restrictions"].kind == ff.CHOICE
        assert "No lifting over 10 kg" in f["restrictions"].options
        assert f["comments"].multiline and not f["Text1"].multiline

    def test_reading_order(self, fields):
        names = [f.name for f in fields]
        assert names[0] == "Text1"
        assert names.index("absence_from") < names.index("absence_to") < names.index("fit_to_return")
        assert names[-1] == "signature"

    def test_flat_pdf_is_explained(self):
        with pytest.raises(ff.FormError, match="no fillable fields"):
            ff.read_form(FLAT)

    def test_not_a_pdf(self, tmp_path):
        bogus = tmp_path / "note.pdf"
        bogus.write_text("hello")
        with pytest.raises(ff.FormError, match="could not be read"):
            ff.read_form(str(bogus))


class TestPseudonymize:
    def test_round_trip(self):
        text = "Call 416-555-0123 or maria@example.com. Card 1234567890. Again 416-555-0123."
        hidden, mapping = ff.pseudonymize(text)
        assert "416-555-0123" not in hidden and "1234567890" not in hidden and "maria@" not in hidden
        assert hidden.count("{{PHONE-1}}") == 2          # same value, same placeholder
        assert "{{OHIP-1}}" in hidden and "{{EMAIL-1}}" in hidden
        assert ff.restore(hidden, mapping) == text

    def test_restore_tolerates_mangled_braces(self):
        _, mapping = ff.pseudonymize("Phone 416-555-0123")
        assert ff.restore("{PHONE-1}", mapping) == "416-555-0123"
        assert ff.restore("{{ PHONE-1 }}", mapping) == "416-555-0123"

    def test_nothing_to_hide(self):
        assert ff.pseudonymize("No identifiers here.") == ("No identifiers here.", {})


class TestPrompts:
    def test_batches_and_numbering(self, fields):
        batches = ff.build_prompts(fields, "Date of visit: 2026-10-13", per_prompt=5)
        assert [len(group) for group, _ in batches] == [5, 5, 3]
        group, prompt = batches[0]
        assert "1. Patient name (text)" in prompt
        assert "Date of visit: 2026-10-13" in prompt
        assert "JSON" in prompt and "Never guess" in prompt

    def test_choice_and_checkbox_descriptions(self, fields):
        f = by_name(fields)
        assert f["followup"].describe() == "Follow-up required (choose one: Yes | No)"
        assert f["fit_to_return"].describe() == "Fit to return to full duties (checkbox: yes or no)"

    def test_source_shortens_transcript_first(self):
        source = ff.compose_source(title="Maria Lopez", note="S: back pain", context="Works in a warehouse",
                                   transcript="word " * 5000, visit_date=dt.date(2026, 10, 13), max_chars=1500)
        assert len(source) <= 1500
        assert "Maria Lopez" in source and "S: back pain" in source and "warehouse" in source
        assert source.endswith("[transcript shortened]")

    def test_source_without_transcript(self):
        source = ff.compose_source(note="Plan: rest", visit_date=dt.date(2026, 1, 2))
        assert source.startswith("Date of visit: 2026-01-02") and "Plan: rest" in source


class TestParseAnswers:
    @pytest.mark.parametrize("reply", [
        '{"1": "Maria", "2": ""}',
        'Here you go:\n```json\n{"1": "Maria", "2": "",}\n```',
        "{'1': 'Maria', '2': ''}",
        '{"field 1": "Maria", "Field 2": null}',
        '["Maria", ""]',
        'Answers: "1": "Maria", "2": ""',
    ])
    def test_tolerant(self, reply):
        assert ff.parse_answers(reply, 2) == {1: "Maria", 2: ""}

    def test_out_of_range_and_garbage(self):
        assert ff.parse_answers('{"1": "a", "9": "b"}', 2) == {1: "a"}
        assert ff.parse_answers("I cannot help with that.", 2) == {}
        assert ff.parse_answers("", 2) == {}

    def test_non_string_values(self):
        assert ff.parse_answers('{"1": true, "2": ["a", "b"], "3": 7}', 3) == {1: "yes", 2: "a, b", 3: "7"}


class TestNormalize:
    def test_text(self, fields):
        f = by_name(fields)
        assert ff.normalize(f["Text1"], "  Maria   Lopez ") == "Maria Lopez"
        assert ff.normalize(f["Text1"], "Not mentioned") == ""
        assert ff.normalize(f["Text1"], "N/A") == ""
        assert ff.normalize(f["comments"], "Line one\nLine two") == "Line one\nLine two"

    def test_max_length(self):
        field = ff.FormField(name="x", kind=ff.TEXT, label="x", max_length=4)
        assert ff.normalize(field, "123456") == "1234"

    def test_checkbox(self, fields):
        box = by_name(fields)["fit_to_return"]
        assert ff.normalize(box, "Yes") == "yes"
        assert ff.normalize(box, "true") == "yes"
        assert ff.normalize(box, "no") == ""
        assert ff.normalize(box, "maybe") == ""

    def test_options_with_opaque_values(self):
        sex = ff.FormField(name="sex", kind=ff.RADIO, label="Sex", options=["1", "2"],
                           option_labels=["Female", "Male"])
        assert sex.describe() == "Sex (choose one: Female | Male)"
        assert ff.normalize(sex, "male") == "2"
        assert ff.normalize(sex, "Female") == "1"
        assert sex.option_label("2") == "Male"

    def test_choices(self, fields):
        f = by_name(fields)
        assert ff.normalize(f["followup"], "yes") == "Yes"
        assert ff.normalize(f["restrictions"], "light duties only") == "Light duties"
        assert ff.normalize(f["restrictions"], "no lifting") == "No lifting over 10 kg"
        assert ff.normalize(f["restrictions"], "Wrok from home") == "Work from home"
        assert ff.normalize(f["restrictions"], "bed rest") == ""


class TestFillPdf:
    def test_values_written_and_form_stays_editable(self, fields, tmp_path):
        out = tmp_path / "filled.pdf"
        values = {
            "Text1": "Maria Lopez",
            "topmostSubform[0].Page1[0].f1_06[0]": "Acute lumbar strain",
            "fit_to_return": "yes",
            "followup": "No",
            "restrictions": "Light duties",
            "comments": "Rest.\nReassess in 1 week.",
        }
        assert ff.fill_pdf(FORM, str(out), fields, values) == len(values)

        result = PdfReader(str(out)).get_fields()
        assert result["Text1"]["/V"] == "Maria Lopez"
        assert result["topmostSubform[0].Page1[0].f1_06[0]"]["/V"] == "Acute lumbar strain"
        assert result["fit_to_return"]["/V"] == "/Yes"
        assert result["followup"]["/V"] == "/No"
        assert result["restrictions"]["/V"] == "Light duties"
        assert result["comments"]["/V"] == "Rest.\nReassess in 1 week."
        assert result["Text2"].get("/V", "") == ""        # untouched fields stay empty

    def test_text_starting_with_slash_stays_text(self, fields, tmp_path):
        out = tmp_path / "filled.pdf"
        ff.fill_pdf(FORM, str(out), fields, {"comments": "/7 days off"})
        assert PdfReader(str(out)).get_fields()["comments"]["/V"] == "/7 days off"

    def test_unticking_and_unknown_names(self, fields, tmp_path):
        out = tmp_path / "filled.pdf"
        assert ff.fill_pdf(FORM, str(out), fields, {"fit_to_return": "", "nope": "x"}) == 1
        assert PdfReader(str(out)).get_fields()["fit_to_return"]["/V"] == "/Off"

    def test_existing_values_are_read_back(self, fields, tmp_path):
        out = tmp_path / "filled.pdf"
        ff.fill_pdf(FORM, str(out), fields, {"Text1": "Maria", "fit_to_return": "yes", "followup": "Yes"})
        again = by_name(ff.read_form(str(out)))
        assert again["Text1"].existing == "Maria"
        assert again["fit_to_return"].existing == "Yes"
        assert again["followup"].existing == "Yes"
        assert again["Text2"].existing == ""


def test_default_output_name():
    assert ff.default_output_name("/x/Work form.pdf", "Maria: Lopez") == "Work form - Maria Lopez - filled.pdf"
    assert ff.default_output_name("/x/Work form.pdf") == "Work form - filled.pdf"


class TestSharedLine:
    def test_fields_on_one_line_are_described_against_the_line(self, fields):
        f = by_name(fields)
        assert f["absence_from"].line == f["absence_to"].line == "Unable to work from … to …"
        assert 'only the part after "to"' in f["absence_to"].describe()
        assert 'only the part after "Unable to work from"' in f["absence_from"].describe()
        assert not f["Text1"].line

    def test_whole_line_answers_are_split(self, fields):
        f = by_name(fields)
        whole = "Unable to work from 2026-10-13 to 2026-10-20"
        assert ff.normalize(f["absence_from"], whole) == "2026-10-13"
        assert ff.normalize(f["absence_to"], whole) == "2026-10-20"
        assert ff.normalize(f["absence_to"], "to 2026-10-20") == "2026-10-20"
        assert ff.normalize(f["absence_from"], "2026-10-13") == "2026-10-13"


def test_capacity(fields):
    f = by_name(fields)
    assert 50 <= ff.capacity(f["Text1"]) <= 70           # 300 pt wide, one line
    assert ff.capacity(f["comments"]) > ff.capacity(f["Text1"]) * 3
    assert ff.capacity(ff.FormField(name="x", kind=ff.TEXT, label="x")) is None


def test_invented_placeholders_are_dropped(fields):
    text = by_name(fields)["Text1"]
    assert ff.normalize(text, "{{REFERING-CLINICIAN}}") == ""
    assert ff.normalize(text, "[Clinician name]") == ""
    assert ff.normalize(text, "Dr. {{NAME}}") == "Dr."
    assert ff.normalize(text, "Maria Lopez [age 56]") == "Maria Lopez [age 56]"   # ordinary brackets stay


class TestWrapping:
    def test_wrap_text(self):
        wrapped = ff.wrap_text("one two three four five six seven", width=60, size=10)
        lines = wrapped.split("\n")
        assert len(lines) > 1 and " ".join(lines) == "one two three four five six seven"
        assert all(ff._text_width(line, 10) <= 60 for line in lines)
        assert ff.wrap_text("Line one\nLine two", width=500, size=10) == "Line one\nLine two"
        assert all(len(part) < 30 for part in ff.wrap_text("x" * 100, width=80, size=10).split("\n"))

    def test_long_answers_wrap_inside_multiline_boxes(self, fields, tmp_path):
        out = tmp_path / "filled.pdf"
        long_text = "Lower back pain since Friday after lifting boxes at the warehouse, worse with bending. " * 2
        ff.fill_pdf(FORM, str(out), fields, {"comments": long_text, "Text1": long_text})
        result = PdfReader(str(out)).get_fields()
        comment_lines = result["comments"]["/V"].split("\n")
        assert len(comment_lines) > 2
        assert all(ff._text_width(line, 12) <= 428 for line in comment_lines)
        assert "\n" not in result["Text1"]["/V"]         # single-line fields are left alone


def test_third_party_notices_cover_the_pdf_libraries():
    """Every library form filling needs is credited in the notices shipped with the app."""
    notices_path = os.path.join(os.path.dirname(ff.__file__), "..", "markdown", "help", "third_party_notices.md")
    notices = open(notices_path, encoding="utf-8").read()
    requirements = open(os.path.join(FIXTURES, "..", "..", "client_requirements_mac.txt")).read()
    for package, version in re.findall(r"^(pypdf|pymupdf)==([\d.]+)$", requirements, flags=re.MULTILINE):
        assert version in notices, f"{package} {version} is not in the third-party notices"
    assert "Mathieu Fenniak" in notices and "Artifex Software" in notices
    assert "Redistributions in binary form must reproduce" in notices
