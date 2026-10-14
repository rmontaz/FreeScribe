"""
Tests for the encounter chat window.

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
    yield window
    try:
        window.destroy()
    except tk.TclError:
        pass


@pytest.fixture
def chat(root):
    from UI.ChatWindow import ChatWindow
    session = Session(title="Maria Lopez", note="A: lumbar strain")
    sent, cleared, closed = [], [], []

    def clear():
        session.chat.clear()
        cleared.append(True)

    win = ChatWindow(root, session, on_send=sent.append, on_clear=clear, on_close=lambda: closed.append(True))
    win.calls = SimpleNamespace(sent=sent, cleared=cleared, closed=closed)
    root.update()
    yield win
    if win.exists():
        win.close()


def log_text(win):
    return win.log.get("1.0", "end")


def test_empty_chat_shows_a_hint(chat):
    assert "No messages yet" in log_text(chat)
    assert "Maria Lopez" in chat.title_label.cget("text")
    assert not chat.clear_button.enabled


def test_enter_sends_and_clears_the_box(root, chat):
    chat.entry.insert("1.0", "What follow-up was planned?")
    assert chat._on_return(SimpleNamespace(state=0x0001)) is None     # Shift+Enter: a new line
    assert chat._on_return(SimpleNamespace(state=0)) == "break"   # Enter: send
    assert chat.calls.sent == ["What follow-up was planned?"]
    assert chat.text() == ""
    chat.send()                                   # nothing typed: nothing sent
    assert chat.calls.sent == ["What follow-up was planned?"]


def test_conversation_busy_state_and_errors(root, chat):
    chat.session.add_chat("user", "Summarise the visit")
    chat.session.add_chat("event", "Filled the form “WSIB”")
    chat.set_busy(True, "Sending…")
    text = log_text(chat)
    assert "You\nSummarise the visit" in text and "FreeScribe\nFilled the form" in text
    assert "thinking" in text and not chat.send_button.enabled
    chat.entry.insert("1.0", "another")
    chat.send()
    assert chat.calls.sent == []                  # one question at a time
    chat.session.add_chat("assistant", "Back pain, lumbar strain.")
    chat.set_busy(False)
    assert "FreeScribe AI\nBack pain" in log_text(chat) and "thinking" not in log_text(chat)
    chat.show_error("The AI could not be reached.")
    assert chat.status.cget("text") == "The AI could not be reached." and chat.send_button.enabled


def test_clear_and_close(root, chat):
    chat.session.add_chat("user", "Hello")
    chat.refresh()
    assert chat.clear_button.enabled
    chat._clear()
    assert chat.calls.cleared and chat.session.chat == [] and "No messages yet" in log_text(chat)
    chat.close()
    assert not chat.exists() and chat.calls.closed
