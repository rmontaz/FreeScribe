"""Live dictation: pieces cut at pauses, spoken line breaks, the AI tidy-up guard, the extension's requests."""
import os
import sys
import threading
import time

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src", "FreeScribe.client"))

from services import live_dictation as ld  # noqa: E402
from services.voice_control import VoiceControl, VoiceError  # noqa: E402

RATE, CHUNK = 16000, 512


class FakeStream:
    """Plays a script of (seconds, amplitude) as 16-bit audio; amplitude 0 is silence."""

    def __init__(self, script, realtime=False):
        samples = []
        for seconds, amp in script:
            n = int(seconds * RATE)
            t = np.arange(n) / RATE
            samples.append((np.sin(2 * np.pi * 220 * t) * amp * 32767).astype(np.int16) if amp else np.zeros(n, np.int16))
        self.data = np.concatenate(samples).tobytes()
        self.pos = 0
        self.realtime = realtime
        self.closed = False

    def read(self, n, exception_on_overflow=False):
        if self.realtime:
            time.sleep(n / RATE / 4)
        chunk = self.data[self.pos:self.pos + n * 2]
        self.pos += n * 2
        if not chunk:
            time.sleep(0.002)
            return b"\x00" * (n * 2)       # the microphone goes on (silence) until stopped
        return chunk.ljust(n * 2, b"\x00")

    def stop_stream(self):
        pass

    def close(self):
        self.closed = True


WORDS = {1: "The patient has a cough", 2: "for three days", 3: "New paragraph. Plan: rest.", 4: "fluids"}


def fake_transcribe(audio):
    # The loudest stretch tells which spoken phrase this is (amplitude 0.1 per phrase number)
    peak = float(np.max(np.abs(audio)))
    return WORDS.get(int(round(peak * 10)), "")


def run(script, **kw):
    stream = FakeStream(script)
    d = ld.LiveDictation(lambda: (stream, None), fake_transcribe, **kw)
    d.start()
    deadline = time.time() + 10
    while stream.pos < len(stream.data) and time.time() < deadline:
        time.sleep(0.01)
    time.sleep(0.2)
    d.stop(wait=5)
    return d, stream


def test_pieces_are_cut_at_pauses_and_joined_as_one_sentence():
    d, stream = run([(1.5, 0.1), (0.8, 0), (1.4, 0.2), (0.8, 0)])
    assert d.finished and stream.closed
    assert [p for _, p in d.pieces()] == ["The patient has a cough", " for three days"]
    assert d.text == "The patient has a cough for three days"


def test_new_paragraph_and_numbering():
    d, _ = run([(1.5, 0.1), (0.8, 0), (1.5, 0.3), (0.8, 0), (1.3, 0.4)])
    assert d.text == "The patient has a cough\n\nPlan: rest. fluids"
    assert [n for n, _ in d.pieces(since=1)] == [2, 3]


def test_silence_only_is_not_transcribed():
    calls = []
    stream = FakeStream([(4.0, 0)])
    d = ld.LiveDictation(lambda: (stream, None), lambda a: calls.append(1) or "Thank you.")
    d.start()
    time.sleep(0.5)
    d.stop(wait=5)
    assert calls == [] and d.text == ""


def test_long_speech_is_cut_without_a_pause():
    d, _ = run([(ld.MAX_PIECE_SECONDS + 2, 0.1), (0.8, 0)])
    assert len(d.pieces()) == 2


def test_microphone_failure():
    d = ld.LiveDictation(lambda: (None, OSError("no device")), fake_transcribe)
    with pytest.raises(ld.LiveDictationError):
        d.start()


@pytest.mark.parametrize("text,expected", [
    ("Cough, new line, fever.", "Cough\nfever."),
    ("Plan. Next paragraph. Rest", "Plan\n\nRest"),
    ("Her last period was in May.", "Her last period was in May."),
])
def test_spoken_commands(text, expected):
    assert ld.spoken_commands(text) == expected


def test_join_piece_lowercases_a_continued_sentence_only():
    assert ld.join_piece("The patient has", "Pain in the knee") == " pain in the knee"
    assert ld.join_piece("The patient has pain.", "She walks") == " She walks"
    assert ld.join_piece("He said", "I fell") == " I fell"
    assert ld.join_piece("Plan\n", "Rest") == "Rest"


def test_tidy_guard():
    original = "bp 130 over 80 metformin 500 mg twice daily"
    ok, problem = ld.accept_tidy(original, "BP 130 over 80. Metformin 500 mg twice daily.")
    assert problem == "" and ok.startswith("BP 130")
    _, problem = ld.accept_tidy(original, "BP 130/85. Metformin 500 mg twice daily.")
    assert "number" in problem
    _, problem = ld.accept_tidy(original, "BP 130 over 80, metformin 500 mg twice daily, follow up in 2 weeks with labs")
    assert problem
    text, problem = ld.accept_tidy("cough for 3 days", 'Here is the corrected text: "Cough for 3 days."')
    assert problem == "" and text == "Cough for 3 days."


# ------------------------------------------------------------------ the extension's requests
def control(script, tidy_reply="The patient has a cough for three days.", busy=""):
    stream = FakeStream(script)
    asked = {}

    def tidy(text, done):
        asked["text"] = text
        threading.Thread(target=lambda: done(tidy_reply, ""), daemon=True).start()

    def fill_form(payload, text, done):
        asked["form_text"] = text
        done({"f1": "Cough"}, {"f1": "Symptoms"}, [])

    vc = VoiceControl(lambda: ld.LiveDictation(lambda: (stream, None), fake_transcribe),
                      lambda: busy, tidy, fill_form)
    return vc, stream, asked


def poll_until(vc, did, test, since=0, seconds=10):
    deadline = time.time() + seconds
    while time.time() < deadline:
        r = vc.dictate({"action": "poll", "id": did, "since": since})
        if test(r):
            return r
        time.sleep(0.05)
    raise AssertionError(r)


def test_dictate_start_poll_stop_with_tidy():
    vc, stream, asked = control([(1.5, 0.1), (0.8, 0), (1.4, 0.2), (0.8, 0)])
    did = vc.dictate({"action": "start", "tidy": True})["id"]
    with pytest.raises(VoiceError):
        vc.dictate({"action": "start"})          # one at a time
    r = poll_until(vc, did, lambda r: len(r["pieces"]) == 2)
    assert r["recording"] and r["tidy"]["state"] == "wanted"
    vc.dictate({"action": "stop", "id": did})
    r = poll_until(vc, did, lambda r: r["tidy"]["state"] == "done")
    assert r["finished"] and asked["text"] == "The patient has a cough for three days"
    assert r["tidy"]["text"] == "The patient has a cough for three days."
    assert vc.dictate({"action": "poll", "id": did, "since": 2})["pieces"] == []


def test_tidy_that_changes_meaning_is_refused():
    vc, _, _ = control([(1.5, 0.1), (0.8, 0)], tidy_reply="The patient has a cough for 2 days and a fever.")
    did = vc.dictate({"action": "start", "tidy": True})["id"]
    poll_until(vc, did, lambda r: r["pieces"])
    vc.dictate({"action": "stop", "id": did})
    r = poll_until(vc, did, lambda r: r["tidy"]["state"] in ("done", "failed"))
    assert r["tidy"]["state"] == "failed" and r["tidy"]["problem"]


def test_microphone_busy_is_refused():
    vc, _, _ = control([(1.0, 0)], busy="The microphone is in use for the visit recording.")
    with pytest.raises(VoiceError) as e:
        vc.dictate({"action": "start"})
    assert e.value.code == "mic_busy"


def test_cancel_and_unknown_id():
    vc, _, _ = control([(1.5, 0.1), (0.8, 0)])
    did = vc.dictate({"action": "start"})["id"]
    vc.dictate({"action": "cancel", "id": did})
    with pytest.raises(VoiceError):
        vc.dictate({"action": "poll", "id": "nope"})


def test_eform_fill_uses_the_dictation_and_polls():
    vc, _, asked = control([(1.5, 0.1), (0.8, 0)])
    did = vc.dictate({"action": "start"})["id"]
    poll_until(vc, did, lambda r: r["pieces"])
    job = vc.eform({"action": "fill", "dictation_id": did, "fields": [{"id": "f1"}]})["job"]
    r = vc.eform({"action": "poll", "job": job})
    assert r["done"] and r["values"] == {"f1": "Cough"} and asked["form_text"] == "The patient has a cough"
