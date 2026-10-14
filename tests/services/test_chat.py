"""
Tests for the encounter chat prompt and chat history in sessions.

This software is released under the AGPL-3.0 license.
"""
from services.chat import compose_chat_prompt, form_event_text, SHORTENED
from services.sessions import Document, Session, SessionStore


def session(**kwargs):
    defaults = dict(title="Maria Lopez", context="Works in a warehouse.", transcript="Doctor: hello. " * 20,
                    note="S: back pain\nA: lumbar strain")
    defaults.update(kwargs)
    return Session(**defaults)


def test_prompt_carries_the_encounter_and_the_conversation():
    s = session(documents=[Document(title="Referral Letter", text="Dear colleague")])
    history = [{"role": "user", "text": "What was the diagnosis?"},
               {"role": "assistant", "text": "Lumbar strain."},
               {"role": "event", "text": "Filled the form “WSIB”"}]
    prompt, report = compose_chat_prompt(s, history, "Draft a return-to-work note")
    for part in ("Maria Lopez", "Works in a warehouse.", "A: lumbar strain", "Referral Letter:\nDear colleague",
                 "Visit transcript:", "Clinician: What was the diagnosis?", "Assistant: Lumbar strain.",
                 "FreeScribe: Filled the form", "Never invent clinical facts"):
        assert part in prompt, part
    assert prompt.rstrip().endswith("Clinician: Draft a return-to-work note\nAssistant:")
    assert report == {"transcript_shortened": False, "documents_shortened": False, "turns_dropped": 0}


def test_long_material_is_shortened_transcript_first():
    s = session(transcript="word " * 5000)
    prompt, report = compose_chat_prompt(s, [], "Summarise", budget=4000)
    assert len(prompt) <= 4000
    assert report["transcript_shortened"] and SHORTENED in prompt
    assert "A: lumbar strain" in prompt and prompt.rstrip().endswith("Clinician: Summarise\nAssistant:")


def test_oldest_turns_are_dropped_and_the_newest_kept():
    history = [{"role": "user" if i % 2 == 0 else "assistant", "text": f"message {i} " + "x" * 300}
               for i in range(20)]
    prompt, report = compose_chat_prompt(session(transcript=""), history, "Latest question", budget=3000)
    assert len(prompt) <= 3000
    assert report["turns_dropped"] > 0
    assert "message 19" in prompt and "message 0 " not in prompt
    assert "Latest question" in prompt


def test_form_event_text():
    text = form_event_text("WSIB Form 8", "WSIB Form 8 - filled.pdf",
                           [("Patient name", "Maria Lopez"), ("Diagnosis", ""), ("Restrictions", "No lifting")])
    assert "“WSIB Form 8”" in text and "- Patient name: Maria Lopez" in text
    assert "Diagnosis" not in text and "- Restrictions: No lifting" in text
    assert "No fields were filled." in form_event_text("A", "B", [("x", "")])


def test_chat_is_saved_with_the_session():
    s = session()
    s.add_chat("user", "Hello")
    s.add_chat("assistant", "Hi")
    again = Session.from_dict(s.to_dict())
    assert [(m["role"], m["text"]) for m in again.chat] == [("user", "Hello"), ("assistant", "Hi")]
    assert not Session(chat=[{"role": "user", "text": "x"}]).is_empty
    # Older files without a chat still load
    data = s.to_dict()
    del data["chat"]
    assert Session.from_dict(data).chat == []
    # Junk entries are ignored
    data["chat"] = [{"role": "system", "text": "x"}, {"role": "user", "text": ""}, "bad", {"role": "user", "text": "ok"}]
    assert [m["text"] for m in Session.from_dict(data).chat] == ["ok"]


def test_store_round_trip_keeps_chat():
    store = SessionStore()
    s = session()
    s.add_chat("user", "Question")
    store.add(s)
    restored = SessionStore()
    restored.load_json(store.to_json())
    assert restored.sessions[0].chat[0]["text"] == "Question"
