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
from dataclasses import dataclass
from typing import Callable, Optional

MAX_CONTEXT = 20000


class ControlError(Exception):
    """A request that cannot be done now; the message is shown in the extension."""


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


def note_for_emr(note: str) -> str:
    """The note as plain text for an EMR text box (see :func:`services.note_text.plain_note`)."""
    return plain_note(note)


class VisitControl:
    """
    :param state: returns the app's current :class:`AppState`
    :param start, stop, pause, resume: carry out the action in the app
    :param add_context: adds text to the open session's Context
    :param version: the app version, reported in status
    """

    def __init__(self, state: Callable[[], AppState], start: Callable[[], None], stop: Callable[[], None],
                 pause: Callable[[], None], resume: Callable[[], None], add_context: Callable[[str], None],
                 version: str = ""):
        self.state = state
        self._start, self._stop, self._pause, self._resume = start, stop, pause, resume
        self._add_context = add_context
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
            "session": {
                "id": s.session_id,
                "title": s.title,
                "has_transcript": bool(s.transcript.strip()),
                "has_note": bool(s.note.strip()),
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


def describe_phase(status: Optional[dict]) -> str:
    """A short description of the status, for logs and tests."""
    if not status:
        return "offline"
    return status.get("phase", "unknown")
