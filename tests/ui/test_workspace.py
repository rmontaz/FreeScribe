"""Behaviour tests for the main workspace.

They need a display (they are skipped without one, e.g. on a headless CI runner
that has no X server) but no audio devices and no models: the workspace is
driven through its public methods, exactly as client.py drives it.

This software is released under the AGPL-3.0 license.
"""
import os
import time
import zipfile

import pytest

tk = pytest.importorskip("tkinter")

from services.sessions import Session, SessionStore  # noqa: E402


class FakeMic:
    """Stands in for MicrophoneTestFrame."""
    enabled = True
    status_ok = True
    status_text = "Microphone: Connected"
    selected_name = "Test Microphone"
    level = 0.4

    def add_listener(self, callback):
        self.listener = callback

    def microphone_names(self):
        return ["Test Microphone", "Other Microphone"]

    def refresh_devices(self):
        pass

    def select_microphone(self, name):
        self.selected_name = name


class FakeState(dict):
    def set(self, key, value):
        self[key] = value


@pytest.fixture(scope="module")
def root():
    try:
        window = tk.Tk()
    except tk.TclError as error:  # no display available
        pytest.skip(f"Tk cannot open a display: {error}")
    from UI.Theme import Theme
    Theme.init(window)
    window.geometry("1200x760")
    yield window
    try:
        window.destroy()
    except tk.TclError:
        pass


@pytest.fixture
def workspace(root, tmp_path, monkeypatch):
    import tkinter.messagebox as messagebox
    from UI.NoteStyleSelector import NoteStyleSelector
    from UI.Widgets import Controls
    from UI.Workspace import Workspace

    # Keep template data out of the working directory and answer dialogs automatically.
    monkeypatch.setattr(NoteStyleSelector, "_styles_path", str(tmp_path / "note_styles.json"))
    monkeypatch.setattr(NoteStyleSelector, "_styles_loaded", False, raising=False)
    monkeypatch.setattr(NoteStyleSelector, "current_style", NoteStyleSelector.DEFAULT_STYLE)
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: True)
    monkeypatch.setattr(messagebox, "showerror", lambda *a, **k: None)
    monkeypatch.setattr(Controls.popup_menu, "enabled", False)

    calls = []
    names = ("toggle_recording", "resume_recording", "toggle_pause", "generate", "upload", "ask",
             "generate_document", "fill_form", "open_chat", "third_party_notices", "toggle_view", "open_settings",
             "sessions_changed",
             "sessions_cleared")
    actions = {name: (lambda *args, name=name: calls.append((name, args))) for name in names}
    actions["get_status"] = lambda: [{"label": "Speech-to-text", "detail": "On this device", "state": "ok"}]
    actions["get_language"] = lambda: "Auto-detect"
    actions["get_data_options"] = lambda: {"recordings": False, "logs": False}

    ws = Workspace(root, SessionStore(), actions, ui_state=FakeState(), mic=FakeMic(), version="vTest")
    ws.calls = calls
    pump(root)
    yield ws
    ws.frame.destroy()
    pump(root)


def pump(root, seconds=0.25):
    """Let Tk run its pending timers (the editors report changes after a short delay)."""
    end = time.time() + seconds
    while time.time() < end:
        root.update()
        time.sleep(0.01)


def type_into(root, area, text):
    area.set_text(text)
    area.flush_change()
    pump(root, 0.05)


# ------------------------------------------------------------------- start-up
def test_starts_on_a_blank_session(workspace):
    assert workspace.current.is_empty
    assert workspace.tabs.ids() == ["context", "transcript", "note"]
    assert workspace.current_tab() == "transcript"
    assert not workspace.generate_button.enabled      # nothing to create a note from yet
    assert not workspace.ask_send.enabled
    assert workspace.record_button.text == "Start transcribing"


def test_placeholders_are_never_content(workspace):
    assert workspace.title_entry.get_value() == ""
    assert workspace.transcript.get_text() == ""
    assert workspace.note.get_text() == ""
    assert workspace.current.title == ""


# ------------------------------------------------------------------- editing
def test_typing_updates_the_session_and_enables_create(root, workspace):
    type_into(root, workspace.transcript, "Doctor: hello. Patient: I have a cough.")
    assert workspace.current.transcript == "Doctor: hello. Patient: I have a cough."
    assert workspace.generate_button.enabled
    assert workspace.generate_button.text == "Create note"
    assert ("sessions_changed", ()) in workspace.calls


def test_title_and_context_are_kept_with_the_session(root, workspace):
    workspace.title_entry.set_value("Jane Doe")
    workspace._on_title_change("Jane Doe")
    type_into(root, workspace.context, "BP 132/84")
    assert (workspace.current.title, workspace.current.context) == ("Jane Doe", "BP 132/84")
    assert workspace.current.id in workspace._rows or True  # the row appears after the list refresh
    pump(root, 0.4)
    assert workspace.current.id in workspace._rows


# -------------------------------------------------------------------- capture
def test_recording_on_a_used_session_starts_a_new_one(root, workspace):
    type_into(root, workspace.transcript, "First patient's transcript.")
    first = workspace.current
    second = workspace.begin_capture()
    assert second is not first
    assert first.transcript == "First patient's transcript."     # untouched
    assert workspace.current is second and second.transcript == ""


def test_resume_continues_in_the_same_session(root, workspace):
    type_into(root, workspace.transcript, "Earlier part.")
    first = workspace.current
    assert workspace.begin_capture(resume=True) is first
    workspace.append_transcript("Later part.\n")
    workspace.flush_edits()
    assert first.transcript.splitlines() == ["Earlier part.", "Later part."]


def test_whole_recording_result_respects_resume(root, workspace):
    type_into(root, workspace.transcript, "Earlier part.")
    workspace.begin_capture(resume=True)
    workspace.set_transcript_result("Transcribed afterwards.")
    assert workspace.current.transcript == "Earlier part.\nTranscribed afterwards."


def test_named_but_unused_session_is_reused(root, workspace):
    workspace.title_entry.set_value("Jane Doe")
    workspace._on_title_change("Jane Doe")
    named = workspace.current
    assert workspace.begin_capture() is named


def test_recording_state_and_timer(root, workspace):
    workspace.begin_capture()
    workspace.set_locked(True)
    workspace.set_recording_state(True)
    assert workspace.record_button.text == "Stop transcribing"
    assert workspace.pause_button.winfo_manager() == "pack"
    assert not workspace.new_button.enabled and not workspace.generate_button.enabled

    pump(root, 0.6)
    workspace.set_recording_state(True, paused=True)
    paused_at = workspace.recording_seconds()
    assert workspace.pause_button.text == "Resume"
    pump(root, 0.4)
    assert workspace.recording_seconds() == pytest.approx(paused_at)      # the clock stops while paused

    workspace.set_recording_state(False)
    workspace.set_locked(False)
    assert workspace.record_button.text == "Start transcribing"
    assert workspace.pause_button.winfo_manager() == ""
    assert 0.5 <= workspace.current.duration <= 1.5


def test_locked_workspace_ignores_session_changes(root, workspace):
    type_into(root, workspace.transcript, "Something")
    current = workspace.current
    workspace.set_locked(True)
    assert workspace.new_session() is None
    workspace.delete_session(current)
    assert workspace.current is current and workspace.store.get(current.id) is current
    workspace.set_locked(False)


# ----------------------------------------------------------------------- notes
def test_apply_note_shows_it_and_offers_regenerate(root, workspace):
    type_into(root, workspace.transcript, "Doctor: hello.")
    workspace.apply_note(workspace.current.id, "Subjective:\n- Cough", "SOAP Note - Default")
    pump(root)
    assert workspace.current.note == "Subjective:\n- Cough"
    assert workspace.current.template == "SOAP Note - Default"
    assert workspace.current_tab() == "note"
    assert workspace.note.get_text() == "Subjective:\n- Cough"
    assert workspace.generate_button.text == "Regenerate"
    assert workspace.ask_send.enabled


def test_note_for_another_session_does_not_overwrite_the_open_one(root, workspace):
    type_into(root, workspace.transcript, "First.")
    first = workspace.current
    workspace.begin_capture()
    type_into(root, workspace.transcript, "Second.")
    workspace.apply_note(first.id, "Note for the first session")
    pump(root)
    assert first.note == "Note for the first session"
    assert workspace.current.note == "" and workspace.note.get_text() == ""


def test_edits_to_the_note_are_saved(root, workspace):
    type_into(root, workspace.transcript, "Doctor: hello.")
    workspace.apply_note(workspace.current.id, "Original")
    pump(root)
    type_into(root, workspace.note, "Corrected by the clinician")
    assert workspace.current.note == "Corrected by the clinician"


def test_revision_can_be_undone(root, workspace):
    type_into(root, workspace.transcript, "Doctor: hello.")
    workspace.apply_note(workspace.current.id, "Long original note")
    pump(root)
    target = workspace.ask_target()
    assert target["kind"] == "note" and target["text"] == "Long original note"
    workspace.apply_revision(target, "Short note")
    pump(root)
    assert workspace.current.note == "Short note"
    assert workspace.note.undo()
    workspace.flush_edits()
    assert workspace.current.note == "Long original note"


def test_ask_sends_the_instruction_and_target(root, workspace):
    type_into(root, workspace.transcript, "Doctor: hello.")
    workspace.apply_note(workspace.current.id, "A note")
    pump(root)
    workspace.ask_entry.set_value("make it shorter")
    workspace._submit_ask()
    name, (instruction, target) = [call for call in workspace.calls if call[0] == "ask"][-1]
    assert instruction == "make it shorter" and target["text"] == "A note"
    assert workspace.ask_entry.get_value() == ""


def test_ask_without_a_note_does_nothing(root, workspace):
    workspace.ask_entry.set_value("make it shorter")
    workspace._submit_ask()
    assert not [call for call in workspace.calls if call[0] == "ask"]


# ------------------------------------------------------------------- documents
def test_documents_get_their_own_closable_tabs(root, workspace):
    type_into(root, workspace.transcript, "Doctor: hello.")
    session = workspace.current
    first = workspace.add_document(session.id, "Referral Letter", "Referral Letter", "Dear colleague")
    second = workspace.add_document(session.id, "Referral Letter", "Referral Letter", "Dear colleague, again")
    pump(root)
    assert [d.title for d in session.documents] == ["Referral Letter", "Referral Letter (2)"]
    assert workspace.current_tab() == f"doc:{second.id}"
    assert workspace.ask_target()["kind"] == "document"
    assert workspace.generate_button.winfo_manager() == ""       # note controls hide on document tabs

    workspace._on_document_close(f"doc:{second.id}")
    assert session.documents == [first]
    assert workspace.current_tab() == "note"
    assert workspace.generate_button.winfo_manager() == "pack"


def test_document_tabs_follow_the_selected_session(root, workspace):
    type_into(root, workspace.transcript, "First.")
    first = workspace.current
    workspace.add_document(first.id, "Referral Letter", "Referral Letter", "Dear colleague")
    workspace.begin_capture()
    assert workspace.tabs.ids() == ["context", "transcript", "note"]
    workspace.select_session(first)
    assert len(workspace.tabs.ids()) == 4


# -------------------------------------------------------------------- sessions
def test_search_filters_the_list(root, workspace):
    workspace.store.add(Session(title="Alpha", transcript="knee pain", note="n"))
    workspace.store.add(Session(title="Beta", transcript="night-time wheeze", note="n"))
    workspace.reload_sessions()
    workspace._on_search("wheeze")
    pump(root, 0.4)
    assert [row.session.title for row in workspace._rows.values()] == ["Beta"]
    workspace._on_search("")
    pump(root, 0.4)
    assert {row.session.title for row in workspace._rows.values()} >= {"Alpha", "Beta"}


def test_selecting_a_session_loads_it_and_drops_the_blank_one(root, workspace):
    saved = workspace.store.add(Session(title="Alpha", transcript="knee pain", note="Knee note", context="ctx"))
    blank = workspace.current
    workspace.reload_sessions()
    workspace._on_row_select(saved)
    assert workspace.title_entry.get_value() == "Alpha"
    assert workspace.note.get_text() == "Knee note" and workspace.context.get_text() == "ctx"
    assert workspace.current_tab() == "note"
    assert workspace.store.get(blank.id) is None


def test_switching_sessions_does_not_leak_text_between_them(root, workspace):
    a = workspace.store.add(Session(title="A", transcript="transcript A", note="note A"))
    b = workspace.store.add(Session(title="B", transcript="transcript B", note="note B"))
    workspace.reload_sessions()
    for _ in range(3):
        workspace._on_row_select(a)
        workspace._on_row_select(b)
    pump(root, 0.4)
    assert (a.transcript, a.note, a.title) == ("transcript A", "note A", "A")
    assert (b.transcript, b.note, b.title) == ("transcript B", "note B", "B")


def test_pending_edit_is_saved_before_switching(root, workspace):
    a = workspace.store.add(Session(title="A", transcript="t", note="note A"))
    b = workspace.store.add(Session(title="B", transcript="t", note="note B"))
    workspace.reload_sessions()
    workspace._on_row_select(a)
    workspace.note.set_text("note A, edited a moment ago")      # still waiting on the debounce timer
    workspace._on_row_select(b)
    assert a.note == "note A, edited a moment ago"
    assert b.note == "note B"


def test_delete_session_and_delete_all(root, workspace):
    a = workspace.store.add(Session(title="A", note="n"))
    workspace.store.add(Session(title="B", note="n"))
    workspace.reload_sessions()
    workspace._on_row_select(a)
    workspace.delete_session(a)
    assert workspace.store.get(a.id) is None and workspace.current.is_empty

    workspace.delete_all_sessions()
    assert [s for s in workspace.store.sessions if not s.is_empty] == []
    assert ("sessions_cleared", ()) in workspace.calls


# ---------------------------------------------------------------------- export
def test_export_writes_the_open_tab(root, workspace, tmp_path):
    type_into(root, workspace.transcript, "Doctor: hello.")
    workspace.title_entry.set_value("Jane Doe")
    workspace._on_title_change("Jane Doe")
    workspace.apply_note(workspace.current.id, "Subjective:\n- Cough for two weeks", "SOAP Note - Default")
    pump(root)

    text_path = workspace.export_current(".txt", path=str(tmp_path / "note.txt"))
    assert "Cough for two weeks" in open(text_path, encoding="utf-8").read()
    docx_path = workspace.export_current(".docx", path=str(tmp_path / "note.docx"))
    with zipfile.ZipFile(docx_path) as archive:
        assert "Cough for two weeks" in archive.read("word/document.xml").decode("utf-8")
    pdf_path = workspace.export_current(".pdf", path=str(tmp_path / "note.pdf"))
    assert open(pdf_path, "rb").read().startswith(b"%PDF")
    assert workspace.ui_state["export_dir"] == str(tmp_path)


def test_export_of_an_empty_tab_is_refused(workspace, tmp_path):
    assert workspace.export_current(".txt", path=str(tmp_path / "empty.txt")) is None
    assert not os.path.exists(tmp_path / "empty.txt")


# -------------------------------------------------------------------- feedback
def test_warnings_can_be_cleared_by_kind(workspace):
    workspace.show_warning("The note could not be created.", closable=True, kind="error")
    workspace.hide_warning(kind="silence")           # the silence check must not dismiss an error
    assert workspace.warning_text == "The note could not be created."
    workspace.hide_warning(kind="error")
    assert workspace.warning_text is None


def test_busy_indicator(workspace):
    workspace.set_busy("Creating note")
    assert workspace.busy_chip.winfo_manager() == "pack"
    workspace.set_busy(None)
    assert workspace.busy_chip.winfo_manager() == ""


def test_compact_view_round_trip(root, workspace):
    workspace.show_compact()
    assert workspace.compact and workspace.compact_bar.winfo_manager() == "pack"
    assert workspace.new_session() is None             # nothing in the full view reacts while compact
    workspace.show_full()
    assert not workspace.compact and workspace.full.winfo_manager() == "pack"


def test_settings_lock(workspace):
    workspace.set_settings_enabled(False)
    assert not workspace.settings_button.enabled
    workspace.set_settings_enabled(True)
    assert workspace.settings_button.enabled


def test_template_choice_is_reflected(root, workspace):
    from UI.NoteStyleSelector import NoteStyleSelector
    names = NoteStyleSelector.template_names()
    assert names[0] == "SOAP Note - Default" and "Referral Letter" in names
    assert all(NoteStyleSelector.is_protected(name) for name in names)
    workspace._choose_template("Progress Note")
    assert NoteStyleSelector.current_style == "Progress Note"
    assert NoteStyleSelector.get_current_prompt_info().pre_prompt
    workspace._choose_detail("Brief")
    assert workspace.detail_level() == "Brief"


def test_long_history_is_listed_a_page_at_a_time(root, workspace):
    for index in range(workspace.LIST_PAGE + 40):
        workspace.store.add(Session(title=f"Patient {index}", note="n"))
    workspace.reload_sessions()
    assert len(workspace._rows) == workspace.LIST_PAGE
    workspace._show_more_sessions()
    listed = [row for row in workspace._rows.values() if not row.session.is_empty]   # besides the blank current one
    assert len(listed) == workspace.LIST_PAGE + 40

    workspace._on_search("Patient 17")           # searching looks through everything, not just the visible page
    pump(root, 0.4)
    assert "Patient 17" in [row.session.title for row in workspace._rows.values()]


# ------------------------------------------------------------------ PDF forms
def test_fill_pdf_form_has_its_own_sidebar_button(root, workspace):
    from UI.Widgets import Controls
    assert workspace.fill_form_button.enabled                      # usable even before anything is recorded
    workspace.fill_form_button.invoke()
    assert ("fill_form", ()) in workspace.calls
    type_into(root, workspace.transcript, "Doctor: hello.")
    workspace._open_document_menu()                                  # the + menu is for documents only
    labels = [item["label"] for item in Controls.popup_menu.last_items if item]
    assert "Fill a PDF form…" not in labels


def test_chat_has_its_own_sidebar_button(workspace):
    workspace.chat_button.invoke()
    assert ("open_chat", ()) in workspace.calls


def test_chat_panel_docks_and_follows_the_session(root, workspace):
    sent = []
    panel = workspace.toggle_chat(on_send=lambda s, t: sent.append((s.title, t)))
    pump(root)
    assert workspace.chat_visible() and panel.session is workspace.current
    workspace.title_entry.set_value("Alpha")
    workspace._on_title_change("Alpha")
    panel.entry.insert("1.0", "Summarise")
    panel.send()
    assert sent == [("Alpha", "Summarise")]
    type_into(root, workspace.transcript, "Doctor: hello.")
    workspace.new_session()
    assert panel.session is workspace.current
    workspace.toggle_chat(on_send=None)
    assert not workspace.chat_visible()


def test_help_menu_lists_third_party_licences(workspace):
    from UI.Widgets import Controls
    workspace._open_help_menu()
    item = next(i for i in Controls.popup_menu.last_items if i and i["label"] == "Third-party licences")
    item["command"]()
    assert ("third_party_notices", ()) in workspace.calls


# ------------------------------------------------------------ transcribe only
def test_transcribe_only_mode_hides_the_note_tools(root, workspace):
    type_into(root, workspace.transcript, "Doctor: hello.")
    workspace.set_transcribe_only(True)
    pump(root)
    assert not workspace.notes_enabled()
    assert workspace.generate_button.winfo_manager() == ""
    assert workspace.template_button.winfo_manager() == ""
    assert workspace.add_doc_button.winfo_manager() == ""
    assert workspace.footer.winfo_manager() == ""                  # the AI edit bar
    assert "Transcribe Only Mode" in workspace.note._placeholder.cget("text")

    workspace.set_transcribe_only(False)
    pump(root)
    assert workspace.notes_enabled()
    assert workspace.generate_button.winfo_manager() == "pack"
    assert workspace.footer.winfo_manager() == "pack"
    assert workspace.add_doc_button.winfo_manager() == "pack"


def test_no_note_template_keeps_the_template_menu(root, workspace):
    from UI.NoteStyleSelector import NoteStyleSelector
    type_into(root, workspace.transcript, "Doctor: hello.")
    workspace._choose_template(NoteStyleSelector.NO_NOTE_STYLE)
    pump(root)
    assert not workspace.notes_enabled()
    assert workspace.generate_button.winfo_manager() == ""
    assert workspace.template_button.winfo_manager() == "pack"   # to switch back
    assert workspace.footer.winfo_manager() == "pack"
    workspace._choose_template(NoteStyleSelector.DEFAULT_STYLE)
    pump(root)
    assert workspace.notes_enabled() and workspace.generate_button.winfo_manager() == "pack"


# ------------------------------------------------------------ default template
def test_new_sessions_start_with_the_default_template(root, workspace):
    from UI.NoteStyleSelector import NoteStyleSelector
    from UI.Widgets import Controls
    type_into(root, workspace.transcript, "Doctor: hello.")
    workspace._choose_template("History & Physical")
    workspace._open_template_menu()
    items = [i for i in Controls.popup_menu.last_items if i]
    make_default = next(i for i in items if i["label"].startswith("Make "))
    assert make_default["label"] == "Make \u201cHistory & Physical\u201d the default" and make_default["enabled"]
    assert any(i["label"] == "SOAP Note   (default)" for i in items)

    # A one-off choice: the next session goes back to the default (SOAP)
    workspace.new_session()
    assert NoteStyleSelector.current_style == NoteStyleSelector.DEFAULT_STYLE

    # Make H&P the default: new sessions, and recordings that start a new session, use it
    workspace._choose_template("History & Physical")
    workspace._open_template_menu()
    next(i for i in Controls.popup_menu.last_items if i and i["label"].startswith("Make "))["command"]()
    assert NoteStyleSelector.default_style == "History & Physical"
    workspace._choose_template(NoteStyleSelector.DEFAULT_STYLE)
    type_into(root, workspace.transcript, "Doctor: second visit.")
    workspace.begin_capture()                       # current session has a transcript -> new session
    assert NoteStyleSelector.current_style == "History & Physical"
    workspace._open_template_menu()
    labels = [i["label"] for i in Controls.popup_menu.last_items if i]
    assert "History & Physical   (default)" in labels


# ------------------------------------------------------------ redesigned layout
def test_new_session_shows_the_start_screen(root, workspace):
    pump(root)
    assert workspace.start_screen.winfo_manager() == "grid"
    assert workspace.footer.winfo_manager() == ""                 # nothing to edit yet
    assert workspace.start_screen.cards and workspace.start_screen.cards[0].selected
    workspace.start_screen.record_button.invoke()
    assert ("toggle_recording", ()) in workspace.calls
    workspace.start_screen.upload_button.invoke()
    assert ("upload", ()) in workspace.calls
    workspace.start_screen.paste_button.invoke()                 # put aside for typing a transcript
    pump(root)
    assert workspace.start_screen.winfo_manager() == "" and workspace.current_tab() == "transcript"


def test_start_screen_template_cards_choose_the_note_type(root, workspace):
    from UI.NoteStyleSelector import NoteStyleSelector
    pump(root)
    card = next(c for c in workspace.start_screen.cards[1:] if c.title != "More…")
    card.invoke()
    pump(root)
    assert NoteStyleSelector.current_style.replace(" - Default", "") == card.title
    assert next(c for c in workspace.start_screen.cards if c.title == card.title).selected


def test_recording_layout(root, workspace):
    workspace.begin_capture()
    workspace.set_locked(True)
    workspace.set_recording_state(True)
    pump(root)
    assert workspace.rec_banner.winfo_manager() == "pack"
    assert workspace.rail.winfo_manager() == "pack" and workspace.sidebar.winfo_manager() == ""
    assert workspace.capture_side.winfo_manager() == "grid"
    assert workspace.footer.winfo_manager() == "" and workspace.start_screen.winfo_manager() == ""
    assert workspace.rec_banner.stop_button._text == "Stop and write note"
    workspace.capture_context.insert("1.0", "BP 132/84")
    workspace.capture_context.event_generate("<<Modified>>")
    pump(root, 0.5)
    assert workspace.current.context == "BP 132/84"
    workspace.set_recording_state(True, paused=True)
    assert workspace.rec_banner.state_label.cget("text") == "PAUSED"
    workspace.rec_banner.pause_button.invoke()
    assert ("toggle_pause", ()) in workspace.calls
    workspace.set_recording_state(False)
    workspace.set_locked(False)
    pump(root)
    assert workspace.rec_banner.winfo_manager() == "" and workspace.sidebar.winfo_manager() == "pack"
    assert workspace.capture_side.winfo_manager() == ""
    assert workspace.context.get_text() == "BP 132/84"


def test_session_status_pills_and_footer_status(root, workspace):
    from UI.Workspace import session_status
    s = Session(title="x")
    assert session_status(s) is None
    s.transcript = "hello"
    assert session_status(s)[0] == "Transcript"
    s.note = "S: x"
    assert session_status(s)[0] == "Note ready"
    assert session_status(s, recording=True)[0] == "Recording"
    workspace._status_signature = None
    workspace.refresh_status()
    assert workspace.status_chip._text == "Private · on this computer"
    workspace.toggle_details()
    assert workspace.details_frame.winfo_manager() == "pack"
    workspace.toggle_details()
    assert workspace.details_frame.winfo_manager() == ""


def test_note_sections_ticks_and_edit_suggestions(root, workspace):
    from UI.Workspace import EDIT_SUGGESTIONS
    type_into(root, workspace.transcript, "Doctor: hello.")
    workspace.apply_note(workspace.current.id, "Subjective:\nCough for 2 weeks\n\nPlan:\nChest X-ray\nReview in 1 week")
    pump(root, 0.4)
    assert workspace.note.section_texts() == [("Subjective", "Cough for 2 weeks"),
                                              ("Plan", "Chest X-ray\nReview in 1 week")]
    assert workspace.note.copy_section(1) == "Chest X-ray\nReview in 1 week"
    assert workspace.tabs._tabs["transcript"]["done"] and workspace.tabs._tabs["note"]["done"]
    assert not workspace.tabs._tabs["context"].get("done")
    assert workspace.suggestions.winfo_manager() == "pack"
    workspace._use_suggestion(EDIT_SUGGESTIONS[0])
    assert workspace.calls[-1][0] == "ask" and workspace.calls[-1][1][0] == EDIT_SUGGESTIONS[0]
