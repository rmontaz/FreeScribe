"""
browser_control.py

What the FreeScribe browser extension can ask for, independent of the window
code (client.py connects it to the running app):

* ``status``: recording or not, paused, busy ("Transcribing", "Creating note"),
  and the open session (title, whether a transcript and a note exist)
* ``record``: start, pause, resume or stop the recording; stopping transcribes
  the visit and writes the note, as the "Stop and write note" button does
* ``note``: the open session's note, also as plain text for an EMR text box
* ``context``: add text (for example the medication list selected in the EMR)
  to the open session's Context, which goes to the AI with the transcript

This software is released under the AGPL-3.0 license.
"""

from __future__ import annotations

import re

from services.note_text import plain_note
from services import patient_details
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional

MAX_CONTEXT = 20000


class ControlError(Exception):
    """
    A request that cannot be done now; the message is shown in the extension.
    ``code`` lets the extension react (``other_patient``: offer a new session or a switch)
    and ``details`` carries what it needs for that.
    """

    def __init__(self, message: str, code: str = "", details: Optional[dict] = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclass
class AppState:
    recording: bool
    paused: bool
    busy: str = ""                 # what the app is doing, e.g. "Creating note"; "" when idle
    session_id: str = ""
    title: str = ""
    transcript: str = ""
    note: str = ""
    context: str = ""
    writes_note: bool = True        # False in Transcribe Only mode / "No Note Generation"
    speech_ready: bool = True
    patient: Dict[str, str] = field(default_factory=dict)   # the patient linked to the session (from the EMR)
    seconds: float = 0.0            # length of the recording so far


def note_for_emr(note: str) -> str:
    """The note as plain text for an EMR text box (see :func:`services.note_text.plain_note`)."""
    return plain_note(note)


class VisitControl:
    """
    :param state: returns the app's current :class:`AppState`
    :param start, stop, pause, resume: carry out the action in the app
    :param add_context: adds text to the open session's Context
    :param set_patient: links the patient's details (from the EMR chart) to the open session
    :param version: the app version, reported in status
    """

    def __init__(self, state: Callable[[], AppState], start: Callable[[], None], stop: Callable[[], None],
                 pause: Callable[[], None], resume: Callable[[], None], add_context: Callable[[str], None],
                 version: str = "", set_patient: Optional[Callable[[Dict[str, str]], None]] = None,
                 clear_patient: Optional[Callable[[], None]] = None,
                 new_session: Optional[Callable[[], None]] = None):
        self.state = state
        self._start, self._stop, self._pause, self._resume = start, stop, pause, resume
        self._add_context = add_context
        self._set_patient = set_patient
        self._clear_patient = clear_patient
        self._new_session = new_session
        self.version = version

    # ------------------------------------------------------------------ status
    def status(self) -> dict:
        s = self.state()
        if s.recording:
            phase = "paused" if s.paused else "recording"
        elif s.busy:
            phase = "working"
        elif s.note.strip():
            phase = "note_ready"
        else:
            phase = "idle"
        return {
            "app": "FreeScribe",
            "version": self.version,
            "phase": phase,
            "recording": s.recording,
            "paused": s.paused,
            "busy": s.busy,
            "writes_note": s.writes_note,
            "seconds": round(float(s.seconds or 0), 1),
            "session": {
                "id": s.session_id,
                "title": s.title,
                "has_transcript": bool(s.transcript.strip()),
                "has_note": bool(s.note.strip()),
                "patient": patient_details.display_name(s.patient) if s.patient else "",
                "patient_dob": (s.patient or {}).get("dob", ""),
            } if s.session_id else None,
        }

    # ------------------------------------------------------------------ actions
    def record(self, payload: dict) -> dict:
        action = str((payload or {}).get("action") or "").lower()
        s = self.state()
        if action == "start":
            if s.recording:
                raise ControlError("FreeScribe is already recording.")
            if s.busy:
                raise ControlError(f"FreeScribe is busy ({s.busy}). Try again when it has finished.")
            self._start()
        elif action == "stop":
            if not s.recording:
                raise ControlError("FreeScribe is not recording.")
            self._stop()
        elif action == "pause":
            if not s.recording or s.paused:
                raise ControlError("There is no running recording to pause.")
            self._pause()
        elif action == "resume":
            if not s.paused:
                raise ControlError("The recording is not paused.")
            self._resume()
        else:
            raise ControlError("Unknown action. Use start, pause, resume or stop.")
        return {"ok": True, "action": action}

    def note(self) -> dict:
        s = self.state()
        if not s.session_id:
            raise ControlError("No session is open in FreeScribe.")
        return {"session_id": s.session_id, "title": s.title, "note": s.note, "plain": note_for_emr(s.note),
                "busy": s.busy}

    def context(self, payload: dict) -> dict:
        text = str((payload or {}).get("text") or "").strip()
        if not text:
            raise ControlError("There was no text to add.")
        if len(text) > MAX_CONTEXT:
            raise ControlError("That is too much text to add at once.")
        s = self.state()
        if not s.session_id and not s.recording:
            raise ControlError("Start or open a session in FreeScribe first.")
        self._add_context(text)
        return {"ok": True, "added": len(text)}


    def patient(self, payload: dict) -> dict:
        """
        Links the patient open in the EMR to the open session (name, date of birth, health card…).

        ``action``: "link" (the default) refuses a session that already belongs to someone
        else, with code ``other_patient``; "switch" moves the session to this patient (the
        clinician confirmed it in the extension); "clear" unlinks the session's patient.
        """
        payload = payload or {}
        action = str(payload.get("action") or "link").lower()
        if action not in ("link", "switch", "clear"):
            raise ControlError("Unknown patient action.")
        s = self.state()
        if action == "clear":
            if self._clear_patient is None:
                raise ControlError("This version of FreeScribe cannot unlink patients.")
            if not s.patient:
                return {"ok": True, "patient": ""}
            name = patient_details.display_name(s.patient)
            self._clear_patient()
            return {"ok": True, "patient": "", "unlinked": name}
        details = patient_details.clean(payload.get("patient") or {})
        if not (details.get("last_name") or details.get("first_name")):
            raise ControlError("No patient details were found on this page. Open the patient's chart first.")
        if self._set_patient is None:
            raise ControlError("This version of FreeScribe cannot take patient details.")
        if not s.session_id and not s.recording:
            raise ControlError("Start or open a session in FreeScribe first.")
        if s.patient and not same_patient(s.patient, details) and action != "switch":
            raise ControlError(
                f"This FreeScribe session is for {patient_details.display_name(s.patient)}, "
                f"not {patient_details.display_name(details)}.",
                code="other_patient",
                details={"current": patient_details.display_name(s.patient),
                         "current_dob": s.patient.get("dob", ""),
                         "chart": patient_details.display_name(details),
                         "chart_dob": details.get("dob", ""),
                         "has_content": bool(s.transcript.strip() or s.note.strip()),
                         "recording": bool(s.recording)})
        if action == "switch" and s.recording:
            raise ControlError("Stop the recording before moving this session to another patient.")
        self._set_patient(details)
        return {"ok": True, "patient": patient_details.display_name(details)}

    def session(self, payload: dict) -> dict:
        """``{"action": "new"}`` starts a fresh, empty session in FreeScribe."""
        action = str((payload or {}).get("action") or "").lower()
        if action != "new":
            raise ControlError("Unknown session action.")
        if self._new_session is None:
            raise ControlError("This version of FreeScribe cannot start sessions from the browser.")
        s = self.state()
        if s.recording:
            raise ControlError("FreeScribe is recording. Stop it before starting a new session.")
        if s.busy:
            raise ControlError(f"FreeScribe is busy ({s.busy}). Try again when it has finished.")
        self._new_session()
        return {"ok": True}

def same_patient(a: Dict[str, str], b: Dict[str, str]) -> bool:
    """Same person: the health card number matches, or else the name and date of birth do."""
    if a.get("hin") and b.get("hin"):
        return re.sub(r"\D", "", a["hin"]) == re.sub(r"\D", "", b["hin"])
    def name(d):
        return (d.get("last_name", "").lower(), d.get("first_name", "").lower())
    return name(a) == name(b) and (not a.get("dob") or not b.get("dob") or a["dob"] == b["dob"])


def describe_phase(status: Optional[dict]) -> str:
    """A short description of the status, for logs and tests."""
    if not status:
        return "offline"
    return status.get("phase", "unknown")
