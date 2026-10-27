"""
VoiceTyping.py

Dictation typed into FreeScribe's own text boxes as you talk: the note (or the
open tab) at the cursor, a field of the form review, or the assistant bar ("change
the note by voice"). One at a time; the microphone is shared with recording.

The words come from :class:`services.live_dictation.LiveDictation` on worker
threads and are put into the widget here, on the Tk thread.

This software is released under the AGPL-3.0 license.
"""

import tkinter as tk
from typing import Callable, Optional

POLL_MS = 200


def _spaced(before: str, piece: str) -> str:
    """``piece`` with a space in front when the text before the cursor needs one, without when it doesn't."""
    if not before or before[-1].isspace():
        return piece.lstrip(" ")
    if piece[:1] in (" ", "\n", ",", ".", ";", ":", "?", "!"):
        return piece
    return " " + piece


class VoiceTyping:
    """
    :param root: the Tk root (for polling)
    :param make_dictation: ``(on_piece=None) -> LiveDictation`` with the app's microphone and speech-to-text
    :param busy_reason: ``() -> str``: why the microphone can't be used now ("" when it can)
    """

    def __init__(self, root, make_dictation: Callable, busy_reason: Callable[[], str]):
        self.root = root
        self.make_dictation = make_dictation
        self.busy_reason = busy_reason
        self.dictation = None
        self.widget = None
        self._since = 0
        self._job = None
        self._on_state = None
        self._on_done = None
        self._entry_pos = 0
        self.text = ""
        self.last_error = ""

    @property
    def active(self) -> bool:
        return self.dictation is not None

    def is_typing_into(self, widget) -> bool:
        return self.dictation is not None and self.widget is widget

    def start(self, widget, on_state: Optional[Callable[[str], None]] = None,
              on_done: Optional[Callable[[str], None]] = None) -> str:
        """
        Start typing into ``widget`` (a tk.Text or tk.Entry) at its cursor. ``on_state(state)``
        hears "listening", "finishing" and "idle"; ``on_done(text)`` gets everything said.
        Returns why it could not start ("" when it started).
        """
        if self.dictation is not None:
            return "Dictation is already on. Stop it first."
        reason = self.busy_reason()
        if reason:
            return reason
        dictation = self.make_dictation()
        try:
            dictation.start()
        except Exception as e:
            return str(e)
        self.dictation = dictation
        self.widget = widget
        self._since = 0
        self.text = ""
        self._on_state = on_state
        self._on_done = on_done
        if isinstance(widget, tk.Text):
            widget.mark_set("fs_dictation", "insert")
            widget.mark_gravity("fs_dictation", "right")
            widget.edit_separator()
        elif isinstance(widget, tk.Entry):
            try:
                self._entry_pos = widget.index("insert")
            except tk.TclError:
                self._entry_pos = len(widget.get())
        self._state("listening")
        self._job = self.root.after(POLL_MS, self._poll)
        return ""

    def stop(self):
        """Stop listening; the last words are still typed."""
        if self.dictation is not None and self.dictation.recording:
            self.dictation.stop()
            self._state("finishing")

    def cancel(self):
        """Stop without typing what is still being transcribed (what was typed stays)."""
        if self.dictation is not None:
            self.dictation.cancel()
            self._end(call_done=False)

    def toggle(self, widget, **kwargs) -> str:
        if self.dictation is not None:
            if self.widget is widget:
                self.stop()
                return ""
            return "Dictation is on in another box. Stop it first."
        return self.start(widget, **kwargs)

    # ------------------------------------------------------------ internals
    def _state(self, state):
        if self._on_state is not None:
            try:
                self._on_state(state)
            except tk.TclError:
                pass

    def _poll(self):
        self._job = None
        d = self.dictation
        if d is None:
            return
        try:
            alive = bool(self.widget.winfo_exists())
        except tk.TclError:
            alive = False
        if not alive:
            d.cancel()
            self._end(call_done=False)
            return
        for number, piece in d.pieces(self._since):
            self._since = number
            self._type(piece)
        if not d.recording and not d.finished:
            self._state("finishing")
        if d.finished:
            self._end(call_done=True)
            return
        self._job = self.root.after(POLL_MS, self._poll)

    def _type(self, piece):
        w = self.widget
        if isinstance(w, tk.Text):
            disabled = str(w.cget("state")) == "disabled"
            if disabled:
                return
            before = w.get("fs_dictation -1c", "fs_dictation")
            text = _spaced(before, piece)
            w.insert("fs_dictation", text)
            w.mark_set("insert", "fs_dictation")
            w.see("fs_dictation")
            w.event_generate("<<Modified>>")
        elif isinstance(w, tk.Entry):
            value = w.get()
            pos = min(self._entry_pos, len(value))
            text = _spaced(value[:pos], piece.replace("\n", " "))
            w.insert(pos, text)
            self._entry_pos = pos + len(text)
            w.icursor(self._entry_pos)
            w.xview_moveto(1.0)
        else:
            return
        self.text += piece

    def _end(self, call_done):
        if self._job is not None:
            try:
                self.root.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None
        if isinstance(self.widget, tk.Text):
            try:
                self.widget.edit_separator()
                self.widget.mark_unset("fs_dictation")
            except tk.TclError:
                pass
        self.last_error = self.dictation.error if self.dictation is not None else ""
        text, on_done = self.text, self._on_done
        self.dictation = None
        self.widget = None
        self._state("idle")
        if call_done and on_done is not None:
            on_done(text)
