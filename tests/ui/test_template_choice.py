"""The note template: a choice for one session, the default for new ones, and changing it from the extension.

This software is released under the AGPL-3.0 license.
"""
import pytest

tk = pytest.importorskip("tkinter")

from .test_workspace import pump, root, workspace  # noqa: E402,F401  (fixtures)


def test_template_for_this_session_and_for_new_sessions(root, workspace, monkeypatch):
    from UI.NoteStyleSelector import NoteStyleSelector
    names = NoteStyleSelector.template_names()
    other = next(n for n in names if n not in (NoteStyleSelector.default_style, NoteStyleSelector.NO_NOTE_STYLE))
    toasts = []
    monkeypatch.setattr(workspace, "toast", lambda message, **kw: toasts.append((message, kw)))

    workspace._choose_template(other)
    assert NoteStyleSelector.current_style == other
    message, kw = toasts[-1]
    assert "New sessions start with" in message and kw.get("action_text") == "Use for new sessions"

    # A new session (also one the extension starts) goes back to the default ...
    workspace.current.transcript = "Something said."
    workspace.new_session()
    assert NoteStyleSelector.current_style == NoteStyleSelector.default_style

    # ... unless the choice is made the default, from the app's notice or the extension
    assert workspace.choose_template(other, make_default=True)
    state = workspace.template_state()
    assert state["current"] == state["default"] == other and other in state["options"]
    workspace.current.transcript = "More."
    workspace.new_session()
    assert NoteStyleSelector.current_style == other
    assert not workspace.choose_template("No such template")
