"""Dictation typed into FreeScribe's own boxes: the note at the cursor, the assistant bar (change the note
by voice) and the fields of the PDF form review. The microphone and speech-to-text are fakes.

This software is released under the AGPL-3.0 license.
"""
import os
import time

import numpy as np
import pytest

tk = pytest.importorskip("tkinter")

from services import form_filler as ff  # noqa: E402
from services.live_dictation import LiveDictation  # noqa: E402

from .test_workspace import pump, root, workspace  # noqa: E402,F401  (fixtures)

RATE = 16000
FORM = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures", "work_absence_form.pdf")


class Mic:
    """Says the phrases in order (amplitude tells the fake speech-to-text which one), then stays silent."""

    def __init__(self, phrases):
        parts = []
        for n in range(1, len(phrases) + 1):
            t = np.arange(int(1.3 * RATE)) / RATE
            parts += [(np.sin(2 * np.pi * 220 * t) * n / 20 * 32767).astype(np.int16), np.zeros(int(0.8 * RATE), np.int16)]
        self.data = np.concatenate(parts).tobytes()
        self.pos = 0

    def read(self, n, exception_on_overflow=False):
        time.sleep(n / RATE / 8)
        chunk = self.data[self.pos:self.pos + 2 * n]
        self.pos += 2 * n
        return chunk.ljust(2 * n, b"\0")

    def stop_stream(self):
        pass

    def close(self):
        pass


def voice_for(root, phrases, busy=""):
    from UI.VoiceTyping import VoiceTyping

    def make(on_piece=None):
        mic = Mic(phrases)
        say = lambda audio: phrases[int(round(float(np.max(np.abs(audio))) * 20)) - 1]  # noqa: E731
        return LiveDictation(lambda: (mic, None), say)
    return VoiceTyping(root, make, lambda: busy)


def run_until(root, test, seconds=15):
    end = time.time() + seconds
    while time.time() < end and not test():
        root.update()
        time.sleep(0.01)
    return test()


def test_types_at_the_cursor_of_a_text_box(root):
    text = tk.Text(root)
    text.insert("1.0", "Plan:\nFollow up.")
    text.mark_set("insert", "1.5")
    voice = voice_for(root, ["rest for a week", "Ibuprofen as needed."])
    said = []
    assert voice.start(text, on_done=said.append) == ""
    assert run_until(root, lambda: "needed" in text.get("1.0", "end-1c"))
    voice.stop()
    assert run_until(root, lambda: said)
    assert text.get("1.0", "end-1c") == "Plan: rest for a week ibuprofen as needed.\nFollow up."
    assert said == [" rest for a week ibuprofen as needed."] or said[0].strip() == "rest for a week ibuprofen as needed."
    text.destroy()


def test_entry_and_one_at_a_time(root):
    entry = tk.Entry(root)
    other = tk.Entry(root)
    voice = voice_for(root, ["left knee", "pain"])
    assert voice.start(entry) == ""
    assert "already" in voice.start(other)
    assert run_until(root, lambda: entry.get().endswith("pain"))
    voice.toggle(entry)                       # pressing the same microphone again stops
    assert run_until(root, lambda: not voice.active)
    assert entry.get() == "left knee pain"
    entry.destroy()
    other.destroy()


def test_refused_while_the_microphone_is_busy(root):
    voice = voice_for(root, ["x"], busy="The microphone is in use for the visit recording.")
    assert "visit recording" in voice.start(tk.Entry(root))
    assert not voice.active


def test_dictate_into_the_note(root, workspace):
    workspace.select_session(workspace.current)
    workspace.current.note = "Assessment:"
    workspace.note.set_text("Assessment:")
    workspace.show_tab("note")
    workspace.enable_dictation(voice_for(root, ["mechanical low back pain"]))
    pump(root)
    assert workspace.dictate_button.winfo_ismapped()
    workspace.note.text.mark_set("insert", "end-1c")
    workspace.toggle_dictation()
    assert workspace.voice.active and "Stop" in workspace.dictate_button.text
    assert run_until(root, lambda: "pain" in workspace.note.get_text())
    workspace.toggle_dictation()              # the same button stops
    assert run_until(root, lambda: not workspace.voice.active)
    pump(root)
    assert workspace.note.get_text() == "Assessment: mechanical low back pain"
    assert workspace.current.note == "Assessment: mechanical low back pain"
    assert workspace.dictate_button.text == "Dictate"


def test_change_the_note_by_voice(root, workspace):
    workspace.current.note = "Plan: rest."
    workspace.note.set_text("Plan: rest.")
    workspace.show_tab("note")
    workspace.enable_dictation(voice_for(root, ["add that BP was 130 over 80"]))
    workspace._refresh_toolbar()
    pump(root)
    workspace.dictate_instruction()
    assert run_until(root, lambda: "80" in workspace.ask_entry.get_value())
    assert not any(c[0] == "ask" for c in workspace.calls)     # sent only when you stop
    workspace.dictate_instruction()
    assert run_until(root, lambda: any(c[0] == "ask" for c in workspace.calls))
    instruction, target = next(c[1] for c in workspace.calls if c[0] == "ask")
    assert instruction == "add that BP was 130 over 80" and target["kind"] == "note"


def test_form_review_fields_have_a_microphone(root, monkeypatch):
    from UI import FormFillWindow as module
    monkeypatch.setattr(module, "open_file", lambda path: None)
    fields = ff.read_form(FORM)
    voice = voice_for(root, ["light duties only"])
    win = module.FormFillWindow(root, FORM, fields, {}, voice=voice)
    root.update()
    text_rows = [r for r in win.rows if r.field.kind == ff.TEXT]
    assert text_rows and all(r.mic is not None for r in text_rows)
    assert all(r.mic is None for r in win.rows if r.field.kind != ff.TEXT)
    row = next(r for r in text_rows if "restriction" in r.field.label.lower() or r.field.multiline)
    row._dictate()
    assert run_until(root, lambda: row.value().strip().endswith("only"))
    row._dictate()                            # the field's microphone again stops
    assert run_until(root, lambda: not voice.active)
    assert row.value().strip().endswith("light duties only")
    win.close()


def test_dictate_from_the_start_screen(root, workspace):
    workspace.new_session()
    workspace.enable_dictation(voice_for(root, ["patient seen for follow up"]))
    workspace._refresh_start_screen()
    pump(root)
    assert workspace.start_screen.winfo_manager() and workspace.start_screen.dictate_button.winfo_ismapped()
    assert not workspace.tabs.actions.winfo_manager()          # the toolbar is hidden on the start screen ...
    workspace.start_screen.dictate_button.invoke()             # ... so the start screen offers Dictate itself
    pump(root)
    assert not workspace.start_screen.winfo_manager() and workspace.current_tab() == "note"
    assert run_until(root, lambda: "follow up" in workspace.note.get_text())
    workspace.toggle_dictation()
    assert run_until(root, lambda: not workspace.voice.active)
    assert workspace.dictate_button.winfo_ismapped() and workspace.dictate_button.text in ("Dictate", "")
