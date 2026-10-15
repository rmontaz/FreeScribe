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
