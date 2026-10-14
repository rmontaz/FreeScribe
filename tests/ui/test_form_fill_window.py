"""Tests for the window that reviews a filled PDF form before it is saved.

This software is released under the AGPL-3.0 license.
"""
import os

import pytest

tk = pytest.importorskip("tkinter")
pytest.importorskip("pypdf")

from pypdf import PdfReader  # noqa: E402

from services import form_filler as ff  # noqa: E402

FORM = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures", "work_absence_form.pdf")


@pytest.fixture(scope="module")
def root():
    try:
        window = tk.Tk()
    except tk.TclError as error:  # no display available
        pytest.skip(f"Tk cannot open a display: {error}")
    from UI.Theme import Theme
    Theme.init(window)
    yield window
    try:
        window.destroy()
    except tk.TclError:
        pass


@pytest.fixture
def window(root, monkeypatch):
    from UI import FormFillWindow as module
    opened = []
    monkeypatch.setattr(module, "open_file", opened.append)
    fields = ff.read_form(FORM)
    values = {"Text1": "Maria Lopez", "hcn": "1234567890", "fit_to_return": "yes", "followup": "No",
              "restrictions": "Light duties", "comments": "x" * 2000}
    saved = []
    win = module.FormFillWindow(root, FORM, fields, values, session_title="Maria Lopez", on_saved=saved.append)
    win.saved, win.opened, win.module = saved, opened, module
    root.update()
    yield win
    win.close()


def rows(win):
    return {row.field.name: row for row in win.rows}


def test_shows_every_field_with_ai_values(window):
    r = rows(window)
    assert len(window.rows) == 13
    assert r["Text1"].value() == "Maria Lopez" and r["Text1"].mark.cget("text") == "AI"
    assert r["fit_to_return"].value() == "yes"
    assert r["followup"].var.get() == "No"
    assert r["Text2"].value() == "" and r["Text2"].mark.cget("text") == ""
    assert r["comments"].mark.cget("text") == "Too long for the box"
    assert window.count_label.cget("text") == "6 of 13 fields have a value"


def test_hiding_blank_fields(root, window):
    window.show_empty.set(False)
    window._apply_filter()
    root.update()
    visible = [row.field.name for row in window.rows if row.label.winfo_manager()]
    assert visible == ["Text1", "hcn", "fit_to_return", "followup", "restrictions", "comments"]


def test_edits_are_saved_to_a_new_pdf(root, window, tmp_path, monkeypatch):
    r = rows(window)
    r["Text2"].var.set("1970-01-02")
    r["fit_to_return"].var.set(False)
    r["comments"].text.delete("1.0", "end")
    r["comments"].text.insert("1.0", "Rest for one week.")
    root.update()
    assert r["Text2"].mark.cget("text") == ""          # typed by the clinician, not the AI

    target = tmp_path / "out.pdf"
    monkeypatch.setattr(window.module.filedialog, "asksaveasfilename", lambda **k: str(target))
    window.save()
    result = PdfReader(str(target)).get_fields()
    assert result["Text2"]["/V"] == "1970-01-02"
    assert result["fit_to_return"]["/V"] == "/Off"
    assert result["comments"]["/V"] == "Rest for one week."
    assert result["restrictions"]["/V"] == "Light duties"
    assert window.saved == [str(target)] and window.opened == [str(target)]
    # What was written stays readable after the window closes (the chat records it)
    assert window.saved_values["comments"] == "Rest for one week."
    assert window.saved_values["Text2"] == "1970-01-02"


def test_refuses_to_overwrite_the_blank_form(window, monkeypatch):
    warnings = []
    monkeypatch.setattr(window.module.filedialog, "asksaveasfilename", lambda **k: FORM)
    monkeypatch.setattr(window.module.messagebox, "showwarning", lambda *a, **k: warnings.append(a))
    window.save()
    assert warnings and window.saved == []


def test_flat_form_is_filled_from_its_fillable_copy(root, tmp_path, monkeypatch):
    pytest.importorskip("pymupdf")
    from UI import FormFillWindow as module
    monkeypatch.setattr(module, "open_file", lambda path: None)
    flat = os.path.join(os.path.dirname(FORM), "flat_intake_form.pdf")
    prepared = ff.prepare_form(flat, work_dir=str(tmp_path))
    name = next(f.name for f in prepared.fields if f.label == "Patient name")
    win = module.FormFillWindow(root, prepared.original_path, prepared.fields, {name: "Maria Lopez"},
                                fill_path=prepared.fill_path, converted=True)
    root.update()
    assert "had no fillable fields" in win.window.winfo_children()[1].winfo_children()[0].cget("text")
    target = tmp_path / "flat filled.pdf"
    monkeypatch.setattr(module.filedialog, "asksaveasfilename", lambda **k: str(target))
    win.save()
    assert {f.label: f.existing for f in ff.read_form(str(target))}["Patient name"] == "Maria Lopez"
    assert not PdfReader(flat).get_fields()         # the original stays a flat PDF


class FakeTalk:
    def __init__(self):
        self.calls = []
        self.text = "Off work from March 3 to March 10. Light duties."
        self.values = {"restrictions": "No lifting over 10 kg", "fit_to_return": "yes"}

    def start(self):
        self.calls.append("start")

    def level(self):
        return 0.5

    def elapsed(self):
        return 3.0

    def cancel(self):
        self.calls.append("cancel")

    def finish(self, on_text, on_error):
        self.calls.append("finish")
        on_text(self.text)

    def fill(self, text, on_values, on_error):
        self.calls.append(("fill", text))
        on_values(self.values)


@pytest.fixture
def blank_window(root):
    from UI import FormFillWindow as module
    talk = FakeTalk()
    win = module.FormFillWindow(root, FORM, ff.read_form(FORM), {}, talk=talk, from_session=False)
    win.fake_talk = talk
    root.update()
    yield win
    win.close()


def test_blank_form_offers_talk_to_fill(root, blank_window):
    win = blank_window
    assert win.talk_panel is not None
    assert "talk through the form" in win.notice_label.cget("text")
    assert not any(row.value() for row in win.rows if not row.field.existing)

    win.talk_panel.main_button.invoke()                 # start talking
    root.update()
    assert win.talk_panel.state == "recording"
    assert win.talk_panel.main_button._text == "Transcribe and fill"
    win.talk_panel.main_button.invoke()                 # transcribe and fill
    root.update()
    r = rows(win)
    assert r["restrictions"].value() == "No lifting over 10 kg" and r["restrictions"].mark.cget("text") == "AI"
    assert r["fit_to_return"].value() == "yes"
    assert win.talk_panel.said_text() == win.fake_talk.text
    assert ("fill", win.fake_talk.text) in win.fake_talk.calls
    assert win.talk_panel.state == "idle" and "Filled 2 fields" in win.talk_panel.status.cget("text")
    assert "from what you said" in win.notice_label.cget("text")


def test_talking_again_adds_to_what_was_said(root, blank_window):
    win = blank_window
    win.talk_panel.start(); win.talk_panel.finish()
    win.fake_talk.text = "Review in two weeks."
    win.talk_panel.start(); win.talk_panel.finish()
    root.update()
    assert win.talk_panel.said_text().endswith("Light duties.\nReview in two weeks.")


def test_errors_and_discard(root, blank_window):
    win = blank_window
    win.fake_talk.start = lambda: (_ for _ in ()).throw(RuntimeError("The microphone is in use."))
    win.talk_panel.start()
    assert win.talk_panel.state == "idle" and win.talk_panel.status.cget("text") == "The microphone is in use."
    del win.fake_talk.start
    win.talk_panel.start()
    win.talk_panel._discard()
    assert "cancel" in win.fake_talk.calls and win.talk_panel.state == "idle"
    win.talk_panel._got_text("")
    assert "Nothing was heard" in win.talk_panel.status.cget("text")
