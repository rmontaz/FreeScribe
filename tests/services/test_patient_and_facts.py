"""
Tests for patient details from the EMR chart (services/patient_details.py, the
extension's /api/patient), section headings and the visit-facts step of the form filler.
"""

import os

import pytest

from services import browser_control as bc
from services import form_filler as ff
from services import patient_details as pd
from services.sessions import Session

FORM = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures", "patient_form.pdf")

PRIYA = {"first_name": "Priya", "last_name": "Raman", "dob": "1984-03-14", "sex": "F", "hin": "1234567890",
         "hin_version": "AB", "street": "12 Elm St", "city": "Toronto", "province": "ON",
         "postal_code": "M4C 1A1", "phone": "416-555-0101", "sin": "046 454 286"}


@pytest.fixture(scope="module")
def fields():
    return ff.read_form(FORM)


def by_name(fields):
    return {f.name: f for f in fields}


class TestPatientValues:
    def test_patient_boxes_come_from_the_chart(self, fields):
        values = pd.patient_values(fields, PRIYA)
        assert values["name"] == "Raman, Priya"                 # the order the label asks for
        assert values["dob"] == "14/03/1984"                    # the box's date layout
        assert values["hin"] == "1234567890 AB"
        assert values["address"] == "12 Elm St, Toronto, ON M4C 1A1"
        assert values["phone"] == "416-555-0101"
        assert values["sex"] == "F"                             # a choice, by its option

    def test_other_peoples_boxes_are_left_alone(self, fields):
        values = pd.patient_values(fields, PRIYA)
        for name in ("employer", "witness_first", "witness_phone", "physician", "physician_phone", "med"):
            assert name not in values

    def test_nothing_without_a_patient(self, fields):
        assert pd.patient_values(fields, {}) == {}

    def test_first_date_is_not_a_first_name(self):
        f = ff.FormField(name="x", kind=ff.TEXT, label="First date of work absence (dd/mm/yyyy)")
        assert pd.patient_values([f], PRIYA) == {}

    def test_clean(self):
        details = pd.clean({"first_name": " Priya ", "dob": "1984-3-14", "sex": "female", "sin": "000",
                            "unknown": "x", "phone": 4165550101})
        assert details == {"first_name": "Priya", "dob": "1984-03-14", "sex": "F", "phone": "4165550101"}
        assert pd.clean({"dob": "14/03/1984"}) == {}

    def test_names(self):
        assert pd.display_name(PRIYA) == "Raman, Priya"
        assert pd.summary(PRIYA) == "Priya Raman, date of birth 1984-03-14, female"


class TestLinkingFromTheExtension:
    def control(self, state, linked):
        return bc.VisitControl(state=lambda: state, start=None, stop=None, pause=None, resume=None,
                               add_context=None, set_patient=linked.update)

    def test_links_the_patient_and_reports_it(self):
        state, linked = bc.AppState(recording=False, paused=False, session_id="s1"), {}
        control = self.control(state, linked)
        assert control.patient({"patient": PRIYA}) == {"ok": True, "patient": "Raman, Priya"}
        assert linked["hin"] == "1234567890"
        state.patient = dict(linked)
        assert control.status()["session"]["patient"] == "Raman, Priya"

    def test_another_patients_session_is_refused(self):
        state = bc.AppState(recording=False, paused=False, session_id="s1", patient=dict(PRIYA))
        control = self.control(state, {})
        with pytest.raises(bc.ControlError, match="This FreeScribe session is for Raman, Priya"):
            control.patient({"patient": {"first_name": "John", "last_name": "Smith"}})
        # The same patient again (as OSCAR spells the health card) is fine
        assert control.patient({"patient": {**PRIYA, "hin": "1234-567-890"}})["ok"]

    def test_needs_a_name_and_a_session(self):
        control = self.control(bc.AppState(recording=False, paused=False), {})
        with pytest.raises(bc.ControlError):
            control.patient({"patient": {"hin": "1"}})
        with pytest.raises(bc.ControlError, match="Start or open a session"):
            control.patient({"patient": PRIYA})


def test_the_session_keeps_the_patient():
    session = Session(patient=pd.clean(PRIYA))
    again = Session.from_dict(session.to_dict())
    assert again.patient == session.patient
    assert not Session(patient={"last_name": "Raman"}).is_empty
    assert Session.from_dict({"title": "x", "patient": "not a dict"}).patient == {}


class TestSections:
    def test_fields_know_their_section(self, fields):
        f = by_name(fields)
        assert f["name"].section == "Section 1 - Patient information"
        # The bracketed note is dropped; a numbered question is not a heading
        assert f["med"].section == "2. Medications"
        assert f["return_when"].section == "2. Medications"
        assert f["physician"].section == "Section 5 - Attending physician"

    def test_the_prompt_shows_the_section(self, fields):
        prompt = ff.build_prompt([by_name(fields)["med"]], "Date of visit: 2026-10-15")
        assert "1. [2. Medications] Medication name (text)" in prompt


class TestVisitFacts:
    def test_fact_prompt(self):
        prompt = ff.build_fact_prompt("Medications", "each medication", "Date of visit: 2026-10-15\n\nhello")
        assert prompt.startswith("Read the visit information below and list only each medication.")
        assert ff.NONE_STATED in prompt and prompt.rstrip().endswith("Medications:")

    def test_parse_facts(self):
        reply = ("Here are the medications:\n**Medications:**\n* Sertraline 50 mg, started 2026-09-10\n"
                 "2. Trazodone 50 mg at bedtime\n- Sertraline 50 mg, started 2026-09-10\n")
        # Headings and lead-ins are dropped, bullets and numbers become "- ", repeats go
        assert ff.parse_facts(reply) == "- Sertraline 50 mg, started 2026-09-10\n- Trazodone 50 mg at bedtime"
        assert ff.parse_facts("None stated.") == ""
        assert ff.parse_facts("") == ""
        assert len(ff.parse_facts("\n".join(f"- fact number {i}" for i in range(500)), limit=200)) <= 200

    def test_fact_sheet(self):
        source = "Date of visit: 2026-10-15\nSession title (often the patient's name): Priya\n\nVisit transcript:\n..."
        sheet = ff.fact_sheet(source, [("Medications", "- Sertraline 50 mg"), ("Work", "")])
        assert sheet == ("Date of visit: 2026-10-15\nSession title (often the patient's name): Priya\n\n"
                         "Medications:\n- Sertraline 50 mg\n\nWork:\nNone stated.")

    def test_patient_line_in_the_visit_information(self):
        source = ff.compose_source(title="x", transcript="hello", patient=pd.summary(PRIYA))
        assert "Patient (from the chart): Priya Raman, date of birth 1984-03-14, female" in source


class TestReadVisitFacts:
    SOURCE = "Date of visit: 2026-10-15\n\nVisit transcript:\nsertraline 50 mg since September 10th"

    def test_one_question_per_topic_then_the_sheet(self):
        asked, shown = [], []

        def ask(prompt):
            asked.append(prompt)
            return "- Sertraline 50 mg, started 2026-09-10" if prompt.rstrip().endswith("Medications:") else "None stated."

        sheet = ff.read_visit_facts(self.SOURCE, ask, progress=shown.append)
        assert len(asked) == len(ff.FACT_TOPICS)
        assert shown[0] == f"Reading the visit: diagnoses (1 of {len(ff.FACT_TOPICS)})…"
        assert "Medications:\n- Sertraline 50 mg, started 2026-09-10" in sheet
        assert "Diagnoses:\nNone stated." in sheet
        assert "Visit transcript" not in sheet          # the fields are filled from the facts only

    def test_nothing_read_keeps_the_visit_information(self):
        assert ff.read_visit_facts(self.SOURCE, lambda prompt: "") == self.SOURCE

    def test_cancelling_stops(self):
        calls = []
        result = ff.read_visit_facts(self.SOURCE, lambda p: calls.append(p) or "- x", cancelled=lambda: len(calls) >= 2)
        assert result == self.SOURCE and len(calls) == 2
