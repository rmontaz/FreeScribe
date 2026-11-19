"""
voice_control.py

The browser extension's requests for dictation:

- ``/api/dictate``  start, stop or cancel dictation, and ``poll`` for the text
  typed so far (piece by piece, so the extension types it into the chart as
  you talk), plus the AI-tidied version when that was asked for;
- ``/api/eform``    ``fill`` a form open in the EMR from what was said, and
  ``poll`` for the answers.

One dictation at a time listens: the microphone is shared with visit recording. A dictation that
has been stopped can still be finishing its last words (and being tidied) while the next one starts.
A dictation whose page stops asking for its text (the page was closed) is stopped after a while,
so FreeScribe is not left listening.

This software is released under the AGPL-3.0 license.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Callable, Dict, Optional

from services.live_dictation import LiveDictation, LiveDictationError, accept_tidy


class VoiceError(Exception):
    """A request the clinician should see the reason for."""

    def __init__(self, message: str, code: str = ""):
        super().__init__(message)
        self.code = code
        self.details = {}


IDLE_SECONDS = 90   # no poll for this long while listening: the page that started it has gone
KEEP_FINISHED = 5   # finished dictations kept so a late poll still finds them


class VoiceControl:
    """
    :param make_dictation: ``() -> LiveDictation`` using the app's microphone and speech-to-text
    :param mic_busy: ``() -> str``: why the microphone can't be used now ("" when it can)
    :param tidy: ``(text, done)``: ask the AI to tidy ``text``; call ``done(reply_or_None, problem)``
    :param fill_form: ``(payload, dictation_text, done)``: fill a web form; call ``done(values, labels, problems)``
    :param idle_seconds: stop listening when the page has not asked for the text for this long
    """

    def __init__(self, make_dictation: Callable[[], LiveDictation], mic_busy: Callable[[], str],
                 tidy: Callable[[str, Callable], None], fill_form: Callable[[dict, str, Callable], None],
                 idle_seconds: float = IDLE_SECONDS, check_every: float = 1.0):
        self.idle_seconds = idle_seconds
        self.check_every = check_every
        self.make_dictation = make_dictation
        self.mic_busy = mic_busy
        self.tidy = tidy
        self.fill_form = fill_form
        self._lock = threading.Lock()
        self._id = ""                                  # the newest dictation
        self._entries: Dict[str, dict] = {}            # id -> {"d", "tidy_wanted", "tidy", "seen"}
        self._jobs: Dict[str, dict] = {}

    @property
    def _dictation(self) -> Optional[LiveDictation]:
        entry = self._entries.get(self._id)
        return entry["d"] if entry else None

    def _entry(self, dictation_id: str) -> Optional[dict]:
        return self._entries.get(dictation_id or self._id)

    # ---------------------------------------------------------------- dictation
    @property
    def busy(self) -> bool:
        """Dictation is using the microphone (visit recording must wait)."""
        return any(e["d"].recording for e in list(self._entries.values()))

    def dictate(self, payload: dict) -> dict:
        action = str(payload.get("action", ""))
        if action == "start":
            return self._start(bool(payload.get("tidy")))
        if action == "poll":
            return self._poll(str(payload.get("id", "")), int(payload.get("since") or 0))
        if action in ("stop", "cancel"):
            with self._lock:
                entry = self._entry(str(payload.get("id", "")))
            if entry is not None:
                d, dictation_id = entry["d"], str(payload.get("id", "")) or self._id
                if action == "cancel":
                    d.cancel()
                    entry["tidy_wanted"] = False
                else:
                    d.stop()
                    if "tidy" in payload:       # chosen when stopping ("Stop" or "Stop & tidy")
                        entry["tidy_wanted"] = bool(payload.get("tidy"))
                        entry["tidy"] = {"state": "wanted" if entry["tidy_wanted"] else "none"}
                    if entry["tidy_wanted"]:
                        threading.Thread(target=self._tidy_when_done, args=(entry, dictation_id), daemon=True).start()
                return {"id": dictation_id}
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
            self._id = uuid.uuid4().hex[:12]
            self._entries[self._id] = {"d": dictation, "tidy_wanted": tidy, "seen": time.monotonic(),
                                       "tidy": {"state": "wanted" if tidy else "none"}}
            self._forget_old()
            threading.Thread(target=self._watch, args=(self._id,), daemon=True, name="dictation-watch").start()
            return {"id": self._id}

    def _forget_old(self):
        """Keep the newest dictation and a few finished ones."""
        finished = [i for i, e in self._entries.items() if i != self._id and e["d"].finished and not e["d"].recording]
        for dictation_id in finished[:-KEEP_FINISHED] if len(finished) > KEEP_FINISHED else []:
            self._entries.pop(dictation_id, None)

    def _watch(self, dictation_id: str):
        """Stop listening when the page that started the dictation stops asking for its text."""
        entry = self._entries.get(dictation_id)
        while entry is not None and entry["d"].recording:
            time.sleep(self.check_every)
            if time.monotonic() - entry["seen"] > self.idle_seconds and entry["d"].recording:
                entry["d"].stop()
                return

    def _poll(self, dictation_id: str, since: int) -> dict:
        entry = self._entries.get(dictation_id)
        if entry is None:
            raise VoiceError("That dictation has ended.", "gone")
        entry["seen"] = time.monotonic()
        d = entry["d"]
        return {
            "id": dictation_id,
            "pieces": [[n, text] for n, text in d.pieces(since)],
            "recording": d.recording,
            "finished": d.finished,
            "level": round(d.level, 3),
            "elapsed": round(d.elapsed, 1),
            "error": d.error,
            "tidy": dict(entry["tidy"]),
        }

    def _tidy_when_done(self, entry: dict, dictation_id: str):
        d = entry["d"]
        d.stop(wait=600)
        text = d.text.strip()
        if dictation_id not in self._entries:
            return
        if not text:
            entry["tidy"] = {"state": "none"}
            return
        entry["tidy"] = {"state": "working"}

        def done(reply, problem=""):
            if reply is None:
                entry["tidy"] = {"state": "failed", "problem": problem or "The AI could not tidy the text."}
                return
            tidied, issue = accept_tidy(text, reply)
            entry["tidy"] = {"state": "failed", "problem": issue} if issue else \
                {"state": "done", "original": text, "text": tidied}

        self.tidy(text, done)

    # ---------------------------------------------------------------- web forms
    def eform(self, payload: dict) -> dict:
        action = str(payload.get("action", ""))
        if action == "fill":
            text = str(payload.get("text") or "").strip()
            entry = self._entries.get(str(payload.get("dictation_id") or ""))
            if not text and entry is not None:
                entry["d"].stop(wait=600)
                text = entry["d"].text.strip()
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
