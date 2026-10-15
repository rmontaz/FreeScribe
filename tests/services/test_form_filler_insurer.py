"""
Tests for filling insurer and government disability forms, using
tests/fixtures/insurer_form.pdf (see make_insurer_form.py), which copies the
layouts that real forms (CPP-D ISP-2519, Canada Life and Sun Life physician
statements) use.
"""

import os

import pytest
from pypdf import PdfReader

from services import form_filler as ff

FORM = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures", "insurer_form.pdf")


@pytest.fixture(scope="module")
def fields():
    return {f.name: f for f in ff.read_form(FORM)}


class TestReading:
    def test_yes_no_named_by_their_values_not_nearby_marks(self, fields):
        assert fields["treated_before"].labels() == ["Yes", "No"]

    def test_numbered_options_named_by_words_inside_a_drawing(self, fields):
        # Stored as 0, 1, 2; the words are drawn inside a Form XObject placed with a transform
        f = fields["prognosis"]
        assert f.options == ["0", "1", "2"]
        assert f.labels() == ["improve", "deteriorate", "remain the same"]
        assert f.option_value("deteriorate") == "1"

    def test_grid_row_option_stored_under_the_row_name(self, fields):
        f = fields["grid_memory"]
        assert f.labels() == ["No impact", "Mild", "Severe"]
        assert f.option_value("No impact") == "Memory"

    def test_tick_boxes_say_what_they_mean(self, fields):
        assert fields["factor_0"].label.endswith(": Workplace Issues")
        assert fields["factor_1"].label.endswith(": Alcohol / Drug Abuse")
        assert "::" not in fields["factor_0"].label

    def test_two_tick_boxes_that_are_one_question(self, fields):
        f = fields["sex"]
        assert f.kind == ff.RADIO
        assert f.labels() == ["Male", "Female"]

    def test_choices_are_written_to_the_right_box(self, fields, tmp_path):
        out = tmp_path / "filled.pdf"
        values = {"sex": "Female", "prognosis": "1", "grid_memory": "Memory", "factor_0": "yes"}
        ff.fill_pdf(FORM, str(out), list(fields.values()), values)
        states = {}
        for page in PdfReader(str(out)).pages:
            for annotation in page.get("/Annots") or []:
                annotation = annotation.get_object()
                name = ff._qualified_name(annotation)
                if name in values:
                    states.setdefault(name, []).append(str(annotation.get("/AS")))
        assert states["sex"] == ["/Off", "/Female"]
        assert states["prognosis"] == ["/Off", "/1", "/Off"]
        assert states["grid_memory"][0] == "/Memory"
        assert states["factor_0"] == ["/Workplace Issues"]


class TestRoles:
    def test_identifiers_and_signature_date_are_never_asked(self, fields):
        assert fields["group_no"].role == ff.PRIVATE
        assert fields["date_signed"].role == ff.PRIVATE
        assert not fields["group_no"].needs_answer

    def test_clinician_block(self, fields):
        for name in ("physician_name", "physician_address", "physician_phone", "physician_fax"):
            assert fields[name].role == ff.CLINICIAN, name
            assert not fields[name].needs_answer
        assert fields["patient_name"].role == ""
        assert fields["patient_name"].needs_answer

    def test_clinician_values_from_profile(self, fields):
        profile = {"name": "Dr. Jane Smith", "street": "12 Main St", "city": "Guelph", "province": "ON",
                   "postal_code": "N1H 1A1", "phone": "519-555-0100", "fax": "519-555-0101"}
        values = ff.clinician_values(list(fields.values()), profile)
        assert values == {
            "physician_name": "Dr. Jane Smith",
            "physician_address": "12 Main St, Guelph, ON N1H 1A1",
            "physician_phone": "519-555-0100",
            "physician_fax": "519-555-0101",
        }

    def test_empty_profile_fills_nothing(self, fields):
        assert ff.clinician_values(list(fields.values()), {}) == {}

    def test_roles_on_labels(self):
        def field(label, kind=ff.TEXT):
            return ff.FormField(name=label, kind=kind, label=label)

        form = [field("Social Insurance Number"), field("Patient name"), field("Home phone"),
                field("I am a:", ff.RADIO), field("Name"), field("Address and telephone number (or use a stamp)"),
                field("Name of physician1"), field("Name of Specialist (1)"), field("First name of witness"),
                field("Last name(s)")]
        ff.assign_roles(form)
        roles = {f.label: f.role for f in form}
        assert roles["Social Insurance Number"] == ff.PRIVATE
        assert roles["Patient name"] == ""
        assert roles["Home phone"] == ff.PRIVATE
        assert roles["Name"] == ff.CLINICIAN                      # after "I am a:"
        assert roles["Address and telephone number (or use a stamp)"] == ff.CLINICIAN
        assert roles["Name of physician1"] == ""                  # another doctor, not the clinician
        assert roles["Name of Specialist (1)"] == ""
        assert roles["Last name(s)"] == ff.PRIVATE                # the witness's


class TestDates:
    @pytest.mark.parametrize("label, max_length, expected", [
        ("Date of birth (dd/mm/yyyy)", None, "14/03/1984"),
        ("First date of work absence (dd-mm-yyy)", None, "14-03-1984"),
        ("Date of last office visit - Enter 4 digit year 2 digit month and 2 digit day", None, "1984-03-14"),
        ("Date of symptom onset - Enter 4 digit year and 2 digit month", None, "1984-03"),
        ("Date of birth year month day", 8, "19840314"),
        ("Date (mm/dd/yyyy)", None, "03/14/1984"),
        ("Date of birth", None, "1984-03-14"),
    ])
    def test_iso_dates_take_the_form_layout(self, label, max_length, expected):
        f = ff.FormField(name="d", kind=ff.TEXT, label=label, max_length=max_length)
        assert ff.normalize(f, "1984-03-14") == expected

    def test_dates_inside_text(self):
        f = ff.FormField(name="d", kind=ff.TEXT, label="Initial dosage and date started (dd/mm/yyyy) (1)")
        assert ff.normalize(f, "50 mg, 2026-09-10") == "50 mg, 10/09/2026"
        assert ff.normalize(f, "50 mg since September 10, 2026") == "50 mg since 10/09/2026"

    def test_month_only_answer_kept_when_a_day_is_wanted(self):
        assert ff.format_dates("2019-04", "%d/%m/%Y") == "2019-04"

    def test_impossible_dates_left_alone(self):
        assert ff.format_dates("2026-13-45", "%d/%m/%Y") == "2026-13-45"


class TestRows:
    def test_rows_are_numbered_and_kept_together(self, fields):
        meds = [f for f in fields.values() if f.name.startswith("med_")]
        assert ff.row_of(fields["med_start_2"]) == ("Initial dosage and date started (dd/mm/yyyy)", 2)
        batches = ff.plan_batches(meds, per_prompt=3)
        assert [[f.name for f in group] for group in batches] == [
            ["med_name_1", "med_start_1"], ["med_name_2", "med_start_2"], ["med_name_3", "med_start_3"]]

    def test_repeated_labels_are_numbered(self):
        form = [ff.FormField(name=str(i), kind=ff.TEXT, label=label)
                for i, label in enumerate(["Medical condition:", "Impairment(s):", "Medical condition:",
                                           "Impairment(s):", "Type of therapy (1)", "Type of therapy (1)"])]
        ff._number_repeats(form)
        assert [f.label for f in form] == ["Medical condition: (1)", "Impairment(s): (1)",
                                           "Medical condition: (2)", "Impairment(s): (2)",
                                           "Type of therapy (1)", "Type of therapy (1), table 2"]
        assert ff.row_of(form[5]) == ("Type of therapy [table 2]", 1)

    def test_earlier_rows_are_shown_to_the_model(self, fields):
        meds = [f for f in fields.values() if f.name.startswith("med_")]
        values = {"med_name_1": "Sertraline", "med_start_1": "50 mg, 10/09/2026"}
        earlier = ff.earlier_answers(meds[2:4], meds, values)
        assert earlier == [("Medication Name (1)", "Sertraline"),
                           ("Initial dosage and date started (dd/mm/yyyy) (1)", "50 mg, 10/09/2026")]
        prompt = ff.build_prompt(meds[2:4], "Date of visit: 2026-10-15", "Form", earlier)
        assert "Already filled in earlier rows" in prompt and "- Medication Name (1): Sertraline" in prompt
        assert "YYYY-MM-DD" in prompt

    def test_repeated_rows_are_dropped(self, fields):
        meds = [f for f in fields.values() if f.name.startswith("med_")]
        values = {"med_name_1": "Sertraline", "med_start_1": "50 mg",
                  "med_name_2": "Trazodone", "med_start_2": "50 mg at bedtime",
                  "med_name_3": "sertraline", "med_start_3": "100 mg"}
        cleaned = ff.drop_repeats(meds, values)
        assert cleaned["med_name_3"] == "" and cleaned["med_start_3"] == ""
        assert cleaned["med_name_2"] == "Trazodone" and cleaned["med_start_2"] == "50 mg at bedtime"


def test_month_first_dates_fixed_when_certain():
    f = ff.FormField(name="d", kind=ff.TEXT, label="Date of birth (dd/mm/yyyy)")
    assert ff.normalize(f, "03/14/1984") == "14/03/1984"
    assert ff.normalize(f, "09/08/2026") == "09/08/2026"      # could be either: left as written
    us = ff.FormField(name="d", kind=ff.TEXT, label="Date (mm/dd/yyyy)")
    assert ff.normalize(us, "14/03/1984") == "03/14/1984"


def test_a_repeated_section_is_cleared_as_a_whole():
    labels = ["Middle name", "Medical condition:", "Impairment(s):", "Medication", "Medication",
              "Medical condition:", "Impairment(s):", "Medication", "Medication", "Middle name",
              "Patient height"]
    form = [ff.FormField(name=str(i), kind=ff.TEXT, label=label) for i, label in enumerate(labels)]
    # as on the CPP-D report: an ordinary field sits between the patient's details and the conditions
    form.insert(1, form.pop())
    ff._number_repeats(form)
    values = {"0": "Ann", "1": "COPD", "2": "FEV1 32%", "3": "Trelegy", "4": "Salbutamol",
              "5": "COPD", "6": "FEV1 32% again", "7": "Duloxetine", "8": "Trelegy", "9": "", "10": "165"}
    cleaned = ff.drop_repeats(form, values)
    assert cleaned["5"] == "" and cleaned["6"] == ""          # condition 2 repeats condition 1
    assert cleaned["7"] == "Duloxetine"                        # a new medication stays
    assert cleaned["8"] == ""                                  # Trelegy again
    assert cleaned["1"] == "COPD" and cleaned["3"] == "Trelegy" and cleaned["4"] == "Salbutamol"
