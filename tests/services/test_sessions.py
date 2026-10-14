"""Tests for the session model that backs the workspace sidebar.

This software is released under the AGPL-3.0 license.
"""
import datetime
import json
import os

import pytest

from services.sessions import (CONTEXT_HEADING, Document, Session, SessionStore, compose_revision_prompt,
                               compose_source, format_clock, format_duration)


def fake_encrypt(text):
    return "ENC:" + text[::-1]


def fake_decrypt(text):
    assert text.startswith("ENC:")
    return text[4:][::-1]


class TestSession:
    def test_new_session_is_empty(self):
        session = Session()
        assert session.is_empty
        assert not session.has_output
        assert session.display_title == "Untitled session"

    def test_any_content_makes_it_non_empty(self):
        for field in ("title", "transcript", "context", "note"):
            session = Session(**{field: "something"})
            assert not session.is_empty, field
        session = Session()
        session.documents.append(Document(title="Referral Letter"))
        assert not session.is_empty

    def test_whitespace_only_content_is_still_empty(self):
        assert Session(transcript="  \n ", note="\t").is_empty

    def test_title_alone_is_not_output(self):
        # A named but unused session must not make "Start transcribing" open a new session.
        assert not Session(title="Jane", context="history").has_output
        assert Session(transcript="hello").has_output

    def test_preview_skips_headings_and_markup(self):
        session = Session(note="**Subjective:**\n- Two weeks of cough\n")
        assert session.preview() == "Two weeks of cough"

    def test_matches_searches_all_text(self):
        session = Session(title="Jane", transcript="wheeze at night", note="Asthma", context="ex-smoker")
        session.documents.append(Document(title="Referral Letter", text="Dear colleague"))
        for query in ("jane", "WHEEZE", "asthma", "smoker", "colleague", "referral", ""):
            assert session.matches(query), query
        assert not session.matches("fracture")

    def test_round_trip_keeps_everything(self):
        session = Session(title="Jane", transcript="t", context="c", note="n", template="SOAP Note - Default",
                          duration=93.4)
        session.documents.append(Document(title="Referral Letter", text="Dear colleague", template="Referral Letter"))
        restored = Session.from_dict(json.loads(json.dumps(session.to_dict())))
        assert restored.id == session.id
        assert (restored.title, restored.transcript, restored.context, restored.note) == ("Jane", "t", "c", "n")
        assert restored.template == "SOAP Note - Default"
        assert restored.duration == pytest.approx(93.4)
        assert restored.created == session.created
        assert [(d.id, d.title, d.text, d.template) for d in restored.documents] == \
               [(d.id, d.title, d.text, d.template) for d in session.documents]

    def test_serialised_form_keeps_the_keys_older_versions_read(self):
        data = Session(transcript="the transcript", note="the note").to_dict()
        assert data["user_message"] == "the transcript"
        assert data["response_text"] == "the note"
        # Untitled sessions keep a timestamp label, exactly like entries written by older versions.
        datetime.datetime.strptime(data["timestamp"], "%Y-%m-%d %H:%M:%S")
        assert Session(title="Jane", note="x").to_dict()["timestamp"] == "Jane"


class TestLegacyHistory:
    """History files written before sessions existed: a list of three-key entries."""

    def test_timestamp_entry(self):
        session = Session.from_dict({"timestamp": "2025-02-19 12:22:47", "user_message": "hi", "response_text": "note"})
        assert session.title == ""
        assert session.created_at == datetime.datetime(2025, 2, 19, 12, 22, 47)
        assert (session.transcript, session.note) == ("hi", "note")

    def test_renamed_entry_becomes_the_title(self):
        session = Session.from_dict({"timestamp": "Mr. Johnson", "user_message": "hi", "response_text": "note"})
        assert session.title == "Mr. Johnson"

    def test_damaged_entries_are_skipped_not_fatal(self):
        store = SessionStore()
        text = json.dumps([{"timestamp": "2025-02-19 12:22:47", "user_message": "a", "response_text": "b"},
                           "garbage", None, 42,
                           {"timestamp": "x", "user_message": None, "response_text": None, "documents": "bad",
                            "duration": "n/a"}])
        assert store.load_json(text) == 2
        assert store.sessions[1].duration == 0
        assert store.sessions[1].documents == []

    def test_not_a_list_is_rejected(self):
        with pytest.raises(ValueError):
            SessionStore().load_json('{"a": 1}')


class TestSessionStore:
    def test_new_sessions_go_first(self):
        store = SessionStore()
        first, second = store.new(), store.new()
        assert store.sessions == [second, first]

    def test_remove_and_get(self):
        store = SessionStore()
        session = store.new()
        assert store.get(session.id) is session
        assert store.remove(session.id) is session
        assert store.get(session.id) is None
        assert store.remove("missing") is None

    def test_visible_hides_empty_sessions_except_the_current_one(self):
        store = SessionStore()
        blank = store.new()
        used = store.add(Session(note="note"))
        assert store.visible() == [used]
        assert set(store.visible(current=blank)) == {blank, used}

    def test_prune_empty_keeps_requested_session(self):
        store = SessionStore()
        keep, drop = store.new(), store.new()
        used = store.add(Session(note="n"))
        store.prune_empty(keep=keep)
        assert set(store.sessions) == {keep, used}
        assert drop not in store.sessions

    def test_grouping_by_day(self):
        now = datetime.datetime(2025, 3, 20, 15, 0, 0)

        def session(days_ago, title):
            return Session(title=title, note="n", created=(now - datetime.timedelta(days=days_ago)).isoformat())

        store = SessionStore()
        for days, title in ((0, "today"), (1, "yesterday"), (4, "this week"), (30, "last month"), (400, "last year")):
            store.add(session(days, title))
        groups = store.grouped(now=now)
        assert [label for label, _ in groups] == ["Today", "Yesterday", "Previous 7 days", "February", "February 2024"]
        assert [[s.title for s in sessions] for _, sessions in groups] == \
               [["today"], ["yesterday"], ["this week"], ["last month"], ["last year"]]

    def test_grouping_orders_newest_first_within_a_day(self):
        now = datetime.datetime(2025, 3, 20, 15, 0, 0)
        store = SessionStore()
        store.add(Session(title="late", note="n", created="2025-03-20T14:00:00"))
        store.add(Session(title="early", note="n", created="2025-03-20T09:00:00"))
        assert [s.title for s in store.grouped(now=now)[0][1]] == ["late", "early"]

    def test_save_and_load(self, tmp_path):
        path = tmp_path / "notes_history.txt"
        store = SessionStore()
        store.add(Session(title="Jane", transcript="t", note="n"))
        store.new()  # an empty session must not be written
        store.save(str(path), fake_encrypt)

        raw = path.read_text(encoding="utf-8")
        assert raw.startswith("ENC:") and "Jane" not in raw

        loaded = SessionStore()
        assert loaded.load(str(path), fake_decrypt) == 1
        assert loaded.sessions[0].title == "Jane"

    def test_saving_no_sessions_leaves_an_empty_file(self, tmp_path):
        path = tmp_path / "notes_history.txt"
        store = SessionStore()
        store.new()
        store.save(str(path), fake_encrypt)
        assert path.read_text(encoding="utf-8") == ""
        assert SessionStore().load(str(path), fake_decrypt) == 0

    def test_missing_file_is_an_empty_history(self, tmp_path):
        assert SessionStore().load(str(tmp_path / "nope.txt"), fake_decrypt) == 0

    def test_failed_save_keeps_the_previous_file(self, tmp_path):
        path = tmp_path / "notes_history.txt"
        store = SessionStore()
        store.add(Session(note="first"))
        store.save(str(path), fake_encrypt)
        before = path.read_text(encoding="utf-8")

        def broken(_):
            raise RuntimeError("keyring unavailable")

        store.add(Session(note="second"))
        with pytest.raises(RuntimeError):
            store.save(str(path), broken)
        assert path.read_text(encoding="utf-8") == before
        assert os.listdir(tmp_path) == ["notes_history.txt"]  # no temp files left behind

    def test_unreadable_file_raises_so_the_caller_can_back_it_up(self, tmp_path):
        path = tmp_path / "notes_history.txt"
        path.write_text("ENC:not json", encoding="utf-8")
        with pytest.raises(ValueError):
            SessionStore().load(str(path), fake_decrypt)


class TestFormatting:
    def test_clock(self):
        assert format_clock(datetime.datetime(2025, 1, 1, 0, 5)) == "12:05 AM"
        assert format_clock(datetime.datetime(2025, 1, 1, 12, 0)) == "12:00 PM"
        assert format_clock(datetime.datetime(2025, 1, 1, 14, 41)) == "2:41 PM"

    def test_duration(self):
        assert format_duration(0) == "00:00"
        assert format_duration(75.9) == "01:15"
        assert format_duration(3725) == "1:02:05"
        assert format_duration(None) == "00:00"
        assert format_duration(-5) == "00:00"


class TestPrompts:
    def test_source_without_context_is_just_the_transcript(self):
        assert compose_source("  hello  ", "  ") == "hello"

    def test_context_is_clearly_separated(self):
        text = compose_source("hello", "BP 132/84")
        assert text.startswith("hello\n\n")
        assert CONTEXT_HEADING in text and text.endswith("BP 132/84")

    def test_revision_prompt_contains_all_parts(self):
        prompt = compose_revision_prompt("make it shorter", "NOTE BODY", "TRANSCRIPT", "CONTEXT")
        for part in ("make it shorter", "NOTE BODY", "TRANSCRIPT", "CONTEXT", "Do not invent"):
            assert part in prompt

    def test_revision_prompt_omits_missing_parts(self):
        prompt = compose_revision_prompt("fix typo", "NOTE BODY")
        assert "Visit transcript" not in prompt and CONTEXT_HEADING not in prompt
