"""
voice_control.py

The browser extension's requests for dictation:

- ``/api/dictate``  start, stop or cancel dictation, and ``poll`` for the text
  typed so far (piece by piece, so the extension types it into the chart as
  you talk), plus the AI-tidied version when that was asked for;
- ``/api/eform``    ``fill`` a form open in the EMR from what was said, and
  ``poll`` for the answers.

One dictation at a time: the microphone is shared with visit recording.

This software is released under the AGPL-3.0 license.
"""

from __future__ import annotations

import threading
import uuid
from typing import Callable, Dict, Optional

from services.live_dictation import LiveDictation, LiveDictationError, accept_tidy


class VoiceError(Exception):
    """A request the clinician should see the reason for."""

    def __init__(self, message: str, code: str = ""):
        super().__init__(message)
        self.code = code
        self.details = {}


class VoiceControl:
    """
    :param make_dictation: ``() -> LiveDictation`` using the app's microphone and speech-to-text
    :param mic_busy: ``() -> str``: why the microphone can't be used now ("" when it can)
    :param tidy: ``(text, done)``: ask the AI to tidy ``text``; call ``done(reply_or_None, problem)``
    :param fill_form: ``(payload, dictation_text, done)``: fill a web form; call ``done(values, labels, problems)``
    """

    def __init__(self, make_dictation: Callable[[], LiveDictation], mic_busy: Callable[[], str],
                 tidy: Callable[[str, Callable], None], fill_form: Callable[[dict, str, Callable], None]):
        self.make_dictation = make_dictation
        self.mic_busy = mic_busy
        self.tidy = tidy
        self.fill_form = fill_form
        self._lock = threading.Lock()
        self._id = ""
        self._dictation: Optional[LiveDictation] = None
        self._tidy_wanted = False
        self._tidy: Dict[str, str] = {}
        self._jobs: Dict[str, dict] = {}

    # ---------------------------------------------------------------- dictation
    @property
    def busy(self) -> bool:
        """Dictation is using the microphone (visit recording must wait)."""
        d = self._dictation
        return d is not None and d.recording

    def dictate(self, payload: dict) -> dict:
        action = str(payload.get("action", ""))
        if action == "start":
            return self._start(bool(payload.get("tidy")))
        if action == "poll":
            return self._poll(str(payload.get("id", "")), int(payload.get("since") or 0))
        if action in ("stop", "cancel"):
            with self._lock:
                d = self._dictation if str(payload.get("id", "")) in ("", self._id) else None
            if d is not None:
                if action == "cancel":
                    d.cancel()
                    self._tidy_wanted = False
                else:
                    d.stop()
                    if "tidy" in payload:       # chosen when stopping ("Stop" or "Stop & tidy")
                        self._tidy_wanted = bool(payload.get("tidy"))
                        self._tidy = {"state": "wanted" if self._tidy_wanted else "none"}
                    if self._tidy_wanted:
                        threading.Thread(target=self._tidy_when_done, args=(d, self._id), daemon=True).start()
            return {"id": self._id}
        raise VoiceError("Unknown dictation request.")

    def _start(self, tidy: bool) -> dict:
        with self._lock:
            if self.busy:
                raise VoiceError("Dictation is already on. Stop it first.", "busy")
            reason = self.mic_busy()
            if reason:
                raise VoiceError(reason, "mic_busy")
            dictation = self.make_dictation()
            try:
                dictation.start()
            except LiveDictationError as e:
                raise VoiceError(str(e), "mic")
            self._dictation = dictation
            self._id = uuid.uuid4().hex[:12]
            self._tidy_wanted = tidy
            self._tidy = {"state": "wanted" if tidy else "none"}
            return {"id": self._id}

    def _poll(self, dictation_id: str, since: int) -> dict:
        d = self._dictation
        if d is None or dictation_id != self._id:
            raise VoiceError("That dictation has ended.", "gone")
        return {
            "id": self._id,
            "pieces": [[n, text] for n, text in d.pieces(since)],
            "recording": d.recording,
            "finished": d.finished,
            "level": round(d.level, 3),
            "elapsed": round(d.elapsed, 1),
            "error": d.error,
            "tidy": dict(self._tidy),
        }

    def _tidy_when_done(self, d: LiveDictation, dictation_id: str):
        d.stop(wait=600)
        text = d.text.strip()
        if dictation_id != self._id:
            return
        if not text:
            self._tidy = {"state": "none"}
            return
        self._tidy = {"state": "working"}

        def done(reply, problem=""):
            if dictation_id != self._id:
                return
            if reply is None:
                self._tidy = {"state": "failed", "problem": problem or "The AI could not tidy the text."}
                return
            tidied, issue = accept_tidy(text, reply)
            self._tidy = {"state": "failed", "problem": issue} if issue else \
                {"state": "done", "original": text, "text": tidied}

        self.tidy(text, done)

    # ---------------------------------------------------------------- web forms
    def eform(self, payload: dict) -> dict:
        action = str(payload.get("action", ""))
        if action == "fill":
            text = str(payload.get("text") or "").strip()
            if not text and payload.get("dictation_id") == self._id and self._dictation is not None:
                self._dictation.stop(wait=600)
                text = self._dictation.text.strip()
            job = uuid.uuid4().hex[:12]
            self._jobs = {k: v for k, v in self._jobs.items() if not v.get("done")}   # keep it small
            self._jobs[job] = {"done": False}

            def done(values, labels=None, problems=None, options=None):
                self._jobs[job] = {"done": True, "values": values or {}, "labels": labels or {},
                                   "options": options or {}, "problems": list(problems or [])}

            self.fill_form(payload, text, done)
            return {"job": job}
        if action == "poll":
            job = self._jobs.get(str(payload.get("job", "")))
            if job is None:
                raise VoiceError("That request has ended.", "gone")
            return dict(job)
        raise VoiceError("Unknown form request.")
