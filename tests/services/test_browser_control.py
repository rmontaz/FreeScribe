"""
Tests for the browser extension's control of FreeScribe: the logic and the local server.

This software is released under the AGPL-3.0 license.
"""
import json
import socket
import urllib.request

import pytest

from services import browser_control as bc
from services.extension_server import ExtensionServer, new_code, normalize_code, CODE_HEADER


class FakeApp:
    def __init__(self):
        self.state = bc.AppState(recording=False, paused=False, session_id="s1", title="Visit")
        self.calls = []
        self.context = ""

    def control(self):
        return bc.VisitControl(
            state=lambda: self.state,
            start=lambda: (self.calls.append("start"), setattr(self.state, "recording", True)),
            stop=lambda: (self.calls.append("stop"), setattr(self.state, "recording", False)),
            pause=lambda: (self.calls.append("pause"), setattr(self.state, "paused", True)),
            resume=lambda: (self.calls.append("resume"), setattr(self.state, "paused", False)),
            add_context=lambda text: setattr(self, "context", self.context + text),
            version="v-test")


def test_record_flow_and_phases():
    app = FakeApp()
    control = app.control()
    assert control.status()["phase"] == "idle"
    control.record({"action": "start"})
    assert control.status()["phase"] == "recording"
    with pytest.raises(bc.ControlError):
        control.record({"action": "start"})
    control.record({"action": "pause"})
    assert control.status()["phase"] == "paused"
    control.record({"action": "resume"})
    control.record({"action": "stop"})
    app.state.busy = "Creating note"
    assert control.status()["phase"] == "working"
    app.state.busy, app.state.note = "", "**Subjective:** chest pain"
    assert control.status()["phase"] == "note_ready"
    assert app.calls == ["start", "pause", "resume", "stop"]


def test_cannot_start_while_busy_or_stop_when_idle():
    app = FakeApp()
    control = app.control()
    with pytest.raises(bc.ControlError):
        control.record({"action": "stop"})
    app.state.busy = "Creating note"
    with pytest.raises(bc.ControlError, match="busy"):
        control.record({"action": "start"})
    with pytest.raises(bc.ControlError):
        control.record({"action": "explode"})


def test_note_as_plain_text():
    note = "# SOAP Note\n\n**Subjective:**\n* Chest pain for *three* days\n\n\n\n**Plan:** ECG"
    assert bc.note_for_emr(note) == "SOAP Note\n\nSubjective:\n- Chest pain for three days\n\nPlan: ECG"
    assert bc.note_for_emr("BP 140/90, 2*3 = 6") == "BP 140/90, 2*3 = 6"


def test_context_is_added():
    app = FakeApp()
    control = app.control()
    control.context({"text": "Metformin 500 mg BID"})
    assert app.context == "Metformin 500 mg BID"
    with pytest.raises(bc.ControlError):
        control.context({"text": "  "})


def test_pairing_codes():
    code = new_code()
    assert len(code) == 19 and code.count("-") == 3
    assert normalize_code(code.lower().replace("-", " ")) == normalize_code(code)


# ------------------------------------------------------------------ the server
def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server():
    app = FakeApp()
    control = app.control()
    srv = ExtensionServer(free_port(), code=lambda: "ABCD-EFGH-JKMN-PQRS",
                          get_routes={"/api/status": control.status, "/api/note": control.note},
                          post_routes={"/api/record": control.record, "/api/context": control.context},
                          user_errors=(bc.ControlError,))
    srv.start()
    yield srv, app
    srv.stop()


def call(srv, path, body=None, code="ABCD-EFGH-JKMN-PQRS", origin="chrome-extension://abcdefgh", host=None):
    headers = {CODE_HEADER: code, "Content-Type": "application/json"}
    if origin:
        headers["Origin"] = origin
    if host:
        headers["Host"] = host
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"http://127.0.0.1:{srv.port}{path}", data=data, headers=headers,
                                     method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read()), dict(response.headers)
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}"), dict(error.headers)


def test_server_controls_the_app(server):
    srv, app = server
    status, data, headers = call(srv, "/api/status")
    assert status == 200 and data["phase"] == "idle" and data["version"] == "v-test"
    assert headers.get("Access-Control-Allow-Origin") == "chrome-extension://abcdefgh"
    assert call(srv, "/api/record", {"action": "start"})[0] == 200 and app.state.recording
    status, data, _ = call(srv, "/api/record", {"action": "start"})
    assert status == 409 and "already recording" in data["error"]
    app.state.recording, app.state.note = False, "**Plan:** ECG"
    status, data, _ = call(srv, "/api/note")
    assert data["plain"] == "Plan: ECG"
    assert call(srv, "/api/context", {"text": "Ramipril 5 mg"})[0] == 200 and app.context == "Ramipril 5 mg"


def test_server_refuses_strangers(server):
    srv, _app = server
    assert call(srv, "/api/status", code="WRONG-CODE")[0] == 401
    assert call(srv, "/api/status", code="abcd efgh jkmn pqrs")[0] == 200          # spacing and case do not matter
    assert call(srv, "/api/status", origin="https://evil.example")[0] == 403      # web pages are refused
    assert call(srv, "/api/status", host="evil.example:80")[0] == 403             # DNS rebinding
    assert call(srv, "/api/nothing")[0] == 404


def test_plain_note_removes_asterisks():
    from services.note_text import plain_note
    note = ("**SOAP Note**\n\n**Subjective:**\n* Chest pain for three days.\n*   Takes *metformin*.\n"
            "  * nested point\n\n**Plan:** ECG.\n*\nBP 140/90, 2*3 tabs")
    assert plain_note(note) == ("SOAP Note\n\nSubjective:\n- Chest pain for three days.\n- Takes metformin.\n"
                                "  - nested point\n\nPlan: ECG.\n\nBP 140/90, 2*3 tabs")


class TestPatientLinking:
    """Linking the chart's patient: refused for another patient's session, switch, unlink, new session."""

    RAMAN = {"last_name": "Raman", "first_name": "Priya", "dob": "1984-03-14"}
    OKOYE = {"last_name": "Okoye", "first_name": "Daniel", "dob": "1991-07-30"}

    @pytest.fixture
    def setup(self):
        state = bc.AppState(recording=False, paused=False, session_id="s1", title="")
        calls = []

        def new_session():
            calls.append("new")
            state.session_id, state.patient, state.transcript, state.note = "s2", {}, "", ""

        control = bc.VisitControl(
            state=lambda: state, start=lambda: None, stop=lambda: None, pause=lambda: None,
            resume=lambda: None, add_context=lambda t: None,
            set_patient=lambda d: setattr(state, "patient", dict(d)),
            clear_patient=lambda: setattr(state, "patient", {}), new_session=new_session)
        return state, control, calls

    def test_link_then_other_patient_is_refused_with_details(self, setup):
        state, control, _ = setup
        assert control.patient({"patient": self.OKOYE})["patient"] == "Okoye, Daniel"
        state.transcript = "visit so far"
        with pytest.raises(bc.ControlError) as refused:
            control.patient({"patient": self.RAMAN})
        assert refused.value.code == "other_patient"
        assert refused.value.details["current"] == "Okoye, Daniel"
        assert refused.value.details["chart"] == "Raman, Priya"
        assert refused.value.details["has_content"] is True
        assert state.patient["last_name"] == "Okoye"            # unchanged

    def test_switch_and_clear(self, setup):
        state, control, _ = setup
        control.patient({"patient": self.OKOYE})
        assert control.patient({"patient": self.RAMAN, "action": "switch"})["patient"] == "Raman, Priya"
        assert state.patient["last_name"] == "Raman"
        assert control.patient({"action": "clear"})["unlinked"] == "Raman, Priya"
        assert state.patient == {}
        assert control.patient({"action": "clear"})["patient"] == ""   # nothing linked: fine

    def test_no_switch_while_recording(self, setup):
        state, control, _ = setup
        control.patient({"patient": self.OKOYE})
        state.recording = True
        with pytest.raises(bc.ControlError):
            control.patient({"patient": self.RAMAN, "action": "switch"})

    def test_new_session(self, setup):
        state, control, calls = setup
        control.patient({"patient": self.OKOYE})
        assert control.session({"action": "new"}) == {"ok": True}
        assert calls == ["new"] and state.patient == {}
        assert control.patient({"patient": self.RAMAN})["patient"] == "Raman, Priya"
        state.recording = True
        with pytest.raises(bc.ControlError):
            control.session({"action": "new"})

    def test_unknown_actions_are_refused(self, setup):
        _, control, _ = setup
        with pytest.raises(bc.ControlError):
            control.patient({"patient": self.RAMAN, "action": "delete"})
        with pytest.raises(bc.ControlError):
            control.session({"action": "close"})

    def test_server_passes_the_reason_to_the_extension(self, setup):
        state, control, _ = setup
        control.patient({"patient": self.OKOYE})
        server = ExtensionServer(0, code=lambda: "ABCD-EFGH-JKMN-PQRS", get_routes={},
                                 post_routes={"/api/patient": control.patient}, user_errors=(bc.ControlError,))
        port = server.start()
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{server.port}/api/patient", method="POST",
                data=json.dumps({"patient": self.RAMAN}).encode(),
                headers={CODE_HEADER: "ABCD-EFGH-JKMN-PQRS", "Content-Type": "application/json"})
            with pytest.raises(urllib.error.HTTPError) as answer:
                urllib.request.urlopen(req)
            body = json.loads(answer.value.read())
            assert answer.value.code == 409 and body["code"] == "other_patient"
            assert body["details"]["chart"] == "Raman, Priya"
        finally:
            server.stop()


def test_only_the_paired_extension_is_let_in():
    bound = {"origin": ""}
    server = ExtensionServer(0, code=lambda: "ABCD-EFGH-JKMN-PQRS", get_routes={"/api/status": lambda: {"ok": True}},
                             post_routes={}, bound_origin=lambda: bound["origin"],
                             bind_origin=lambda o: bound.update(origin=o))
    server.start()

    def ask(origin):
        req = urllib.request.Request(f"http://127.0.0.1:{server.port}/api/status",
                                     headers={CODE_HEADER: "ABCD-EFGH-JKMN-PQRS", "Origin": origin})
        try:
            return urllib.request.urlopen(req).status
        except urllib.error.HTTPError as error:
            return error.code
    try:
        assert ask("chrome-extension://ourextension") == 200
        assert bound["origin"] == "chrome-extension://ourextension"
        assert ask("chrome-extension://ourextension") == 200
        assert ask("chrome-extension://someotherextension") == 403     # has the code, still refused
        bound["origin"] = ""                                           # a new pairing code
        assert ask("chrome-extension://someotherextension") == 200
    finally:
        server.stop()


def test_a_waiting_question_and_an_error_reach_the_extension():
    question = {"id": "q1", "text": "Write the note anyway?",
                "options": [{"id": "cancel", "label": "Don't"}, {"id": "anyway", "label": "Write it"}]}
    state = bc.AppState(recording=False, paused=False, busy="Creating note", session_id="s", question=question)
    control = bc.VisitControl(state=lambda: state, start=lambda: None, stop=lambda: None, pause=lambda: None,
                              resume=lambda: None, add_context=lambda t: None)
    s = control.status()
    assert s["phase"] == "question" and s["question"]["options"][1]["id"] == "anyway"
    state.question, state.busy, state.error = None, "", "Nothing was transcribed."
    s = control.status()
    assert s["phase"] == "idle" and s["error"] == "Nothing was transcribed." and s["question"] is None


def test_the_note_template_reaches_the_extension():
    template = {"current": "SOAP Note - Default", "default": "History & Physical",
                "options": ["SOAP Note - Default", "History & Physical"]}
    state = bc.AppState(recording=True, paused=False, session_id="s", template=template)
    control = bc.VisitControl(state=lambda: state, start=lambda: None, stop=lambda: None, pause=lambda: None,
                              resume=lambda: None, add_context=lambda t: None)
    assert control.status()["template"] == template


def test_insert_only_takes_the_charts_own_note():
    ann = {"first_name": "Ann", "last_name": "Lee", "dob": "1980-02-03"}
    bob = {"first_name": "Bob", "last_name": "Ng", "dob": "1975-05-06"}
    state = bc.AppState(recording=True, paused=False, session_id="s2", title="Ng, Bob", patient=bob, note="")
    sessions = [{"id": "s2", "title": "Ng, Bob", "note": "", "patient": bob, "updated": "2026-10-27T10:30"},
                {"id": "s1", "title": "Lee, Ann", "note": "S: back pain", "patient": ann, "updated": "2026-10-27T10:10"}]
    control = bc.VisitControl(state=lambda: state, start=lambda: None, stop=lambda: None, pause=lambda: None,
                              resume=lambda: None, add_context=lambda t: None, sessions=lambda: sessions)
    # Back in Ann's chart while Bob is being recorded: Ann's note from the earlier session
    r = control.note_for({"patient": dict(ann)})
    assert r["match"] == "other" and r["plain"] == "S: back pain" and r["patient"] == "Lee, Ann"
    # Bob's chart: the open session, which has no note yet
    r = control.note_for({"patient": dict(bob)})
    assert r["match"] == "current" and r["plain"] == ""
    # Someone else's chart: nothing to insert, and it says whose the open session is
    r = control.note_for({"patient": {"first_name": "Cy", "last_name": "Diaz", "dob": "1990-01-01"}})
    assert r["match"] == "none" and r["chart"] == "Diaz, Cy" and r["session_patient"] == "Ng, Bob"
    # Same name, different date of birth: not the same patient
    r = control.note_for({"patient": {"first_name": "Ann", "last_name": "Lee", "dob": "1999-09-09"}})
    assert r["match"] == "none"
    with pytest.raises(bc.ControlError):
        control.note_for({"patient": {}})


def test_todays_notes_say_what_is_still_waiting():
    import datetime as dt
    today = dt.date.today().isoformat()
    ann = {"first_name": "Ann", "last_name": "Lee"}
    bob = {"first_name": "Bob", "last_name": "Ng"}
    cy = {"first_name": "Cy", "last_name": "Diaz"}
    sessions = [
        {"id": "s4", "patient": cy, "note": "", "recording": True, "created": today + "T11:00"},
        {"id": "s3", "patient": bob, "note": "", "busy": True, "created": today + "T10:40"},
        {"id": "s2", "patient": ann, "note": "S: ...", "inserted_at": today + "T10:42:10", "created": today + "T10:10"},
        {"id": "s1", "patient": {}, "note": "S: ...", "title": "Phone call", "created": today + "T09:00"},
        {"id": "s0", "patient": ann, "note": "old", "created": "2020-01-01T09:00"},
    ]
    control = bc.VisitControl(state=lambda: bc.AppState(recording=True, paused=False), start=lambda: None,
                              stop=lambda: None, pause=lambda: None, resume=lambda: None,
                              add_context=lambda t: None, sessions=lambda: sessions)
    notes = control.status()["notes"]
    assert [(n["name"], n["state"]) for n in notes] == [
        ("Diaz, Cy", "recording"), ("Ng, Bob", "writing"), ("Lee, Ann", "inserted"), ("Phone call", "ready")]
    assert notes[2]["inserted_at"] == "10:42"
