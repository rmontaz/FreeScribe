"""
Tests for the docked chat panel.

This software is released under the AGPL-3.0 license.
"""
import tkinter as tk
from types import SimpleNamespace

import pytest

from services.sessions import Session


@pytest.fixture(scope="module")
def root():
    try:
        window = tk.Tk()
    except tk.TclError as error:  # no display available
        pytest.skip(f"Tk cannot open a display: {error}")
    from UI.Theme import Theme
    Theme.init(window)
    window.geometry("420x700")
    yield window
    try:
        window.destroy()
    except tk.TclError:
        pass


@pytest.fixture
def chat(root):
    from UI.ChatWindow import ChatPanel
    session = Session(title="Maria Lopez", note="A: lumbar strain")
    calls = SimpleNamespace(sent=[], cleared=[], closed=[])

    def clear(s):
        s.chat.clear()
        calls.cleared.append(s.id)

    panel = ChatPanel(root, on_send=lambda s, t: calls.sent.append((s.id, t)), on_clear=clear,
                      on_close=lambda: calls.closed.append(True))
    panel.frame.pack(fill="both", expand=True)
    panel.set_session(session)
    panel.calls = calls
    root.update()
    yield panel
    panel.frame.destroy()


def texts(panel):
    found = []

    def walk(widget):
        for child in widget.winfo_children():
            if isinstance(child, tk.Label) and child.cget("text"):
                found.append(child.cget("text"))
            walk(child)
    walk(panel.log.body)
    return found


def test_empty_chat_shows_a_hint(chat):
    assert any("Ask about this visit" in t for t in texts(chat))
    assert not chat.clear_button.enabled


def test_enter_sends_and_shift_enter_adds_a_line(root, chat):
    chat.entry.insert("1.0", "What follow-up was planned?")
    assert chat._on_return(SimpleNamespace(state=0x0001)) is None
    assert chat._on_return(SimpleNamespace(state=0)) == "break"
    assert chat.calls.sent == [(chat.session.id, "What follow-up was planned?")]
    assert chat.text() == ""
    chat.send()                                   # nothing typed: nothing sent
    assert len(chat.calls.sent) == 1


def test_messages_busy_state_and_errors(root, chat):
    session = chat.session
    session.add_chat("user", "Summarise the visit")
    session.add_chat("event", "Filled the form “WSIB”")
    chat.set_busy(session.id, True, "Sending…")
    shown = texts(chat)
    assert "Summarise the visit" in shown and "Filled the form “WSIB”" in shown
    assert "FreeScribe AI is writing…" in shown and not chat.send_button.enabled
    chat.entry.insert("1.0", "another")
    chat.send()
    assert chat.calls.sent == []                  # one question at a time
    session.add_chat("assistant", "Back pain, lumbar strain.")
    chat.set_busy(session.id, False)
    shown = texts(chat)
    assert "Back pain, lumbar strain." in shown and "FreeScribe AI is writing…" not in shown
    chat.show_error(session.id, "No reply: the AI could not be reached.")
    assert chat.status.cget("text") == "No reply: the AI could not be reached." and chat.send_button.enabled


def test_follows_the_open_session_and_keeps_each_busy_state(root, chat):
    first = chat.session
    chat.set_busy(first.id, True)
    other = Session(title="J. Okafor")
    other.add_chat("user", "Hello")
    chat.set_session(other)
    assert "Hello" in texts(chat) and chat.send_button.enabled     # the other session is not waiting
    chat.set_session(first)
    assert not chat.send_button.enabled


def test_clear_and_close(root, chat):
    chat.session.add_chat("user", "Hello")
    chat.refresh()
    assert chat.clear_button.enabled
    chat._clear()
    assert chat.calls.cleared and chat.session.chat == []
    chat.close()
    assert chat.calls.closed
