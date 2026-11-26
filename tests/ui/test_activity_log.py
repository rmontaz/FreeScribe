"""The activity log window, and what the workspace records into it."""
import tkinter as tk
import tkinter.messagebox as mb

import pytest

from services import activity_log as al
from services.sessions import Session, SessionStore
from UI.ActivityLogWindow import ActivityLogWindow


class Settings(dict):
    def set(self, key, value):
        self[key] = value


@pytest.fixture(scope="module")
def tk_root():
    try:
        window = tk.Tk()
    except tk.TclError as error:
        pytest.skip(f"Tk cannot open a display: {error}")
    from UI.Theme import Theme
    Theme.init(window)
    yield window
    try:
        window.destroy()
    except tk.TclError:
        pass


@pytest.fixture
def log(tmp_path):
    enc = lambda text: text[::-1].encode().hex()
    dec = lambda text: bytes.fromhex(text).decode()[::-1]
    shared = al.ActivityLog(str(tmp_path / "log.txt"), enc, dec, Settings(), lambda: ("s1", "Sample patient A"))
    shared.suspended = False
    return shared


@pytest.fixture
def window(tk_root, log):
    log.record("ai", "Writing a note", prompt="p", reply="r")
    log.record("oscar", "Note put into OSCAR", chart="Sample chart", text="n")
    ActivityLogWindow._open = None
    shown = ActivityLogWindow.show(tk_root, log)
    tk_root.update()
    yield shown
    shown.window.destroy()
    ActivityLogWindow._open = None


def test_lists_entries_and_filters_by_search_and_type(window):
    assert len(window.tree.get_children()) == 2
    window.search.set_value("sample chart")
    window.refresh()
    assert len(window.tree.get_children()) == 1
    window.search.set_value("")
    window._set_kind("ai")
    assert [window._entry(i)["kind"] for i in window.tree.get_children()] == ["ai"]


def test_reading_an_entry_in_full(window):
    window.tree.selection_set(window.tree.get_children()[0])
    window._show_detail()
    assert window.detail.get("1.0", "end").strip()


def test_export_warns_first_and_can_be_declined(window, monkeypatch, tmp_path):
    asked = []
    monkeypatch.setattr(mb, "askokcancel", lambda *a, **k: asked.append(a) or False)
    window.export("csv")
    assert asked and "patient information" in asked[0][1] and not list(tmp_path.glob("*.csv"))


def test_export_writes_what_is_shown(window, monkeypatch, tmp_path):
    target = tmp_path / "out.csv"
    monkeypatch.setattr(mb, "askokcancel", lambda *a, **k: True)
    monkeypatch.setattr(mb, "showinfo", lambda *a, **k: None)
    import UI.ActivityLogWindow as module
    monkeypatch.setattr(module.filedialog, "asksaveasfilename", lambda **k: str(target))
    window._set_kind("oscar")
    window.export("csv")
    text = target.read_text(encoding="utf-8-sig")
    assert "Sample chart" in text and "Writing a note" not in text


def test_clear_needs_confirmation_and_turn_off_stops_recording(window, log, monkeypatch):
    monkeypatch.setattr(mb, "askyesno", lambda *a, **k: False)
    window.clear()
    assert log.count() == 2
    monkeypatch.setattr(mb, "askyesno", lambda *a, **k: True)
    window.clear()
    assert log.count() == 0
    window.toggle_logging()
    assert log.record("ai", "x") is None


def test_retention_can_be_changed(window, log):
    window.set_retention(30)
    assert log.retention_days == 30
