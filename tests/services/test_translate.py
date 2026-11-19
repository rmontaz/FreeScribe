"""
Tests for the logic behind the Translate window.

This software is released under the AGPL-3.0 license.
"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src", "FreeScribe.client"))

from services import translate as tr  # noqa: E402


def test_languages_are_unique_and_named():
    codes = [code for code, _ in tr.LANGUAGES]
    assert len(codes) == len(set(codes))
    assert tr.language_name("fr") == "French" and tr.language_code("French") == "fr"
    assert tr.language_name("xx") == "xx"
    assert "English" in tr.language_names()


def test_the_speaker_is_chosen_between_the_two_languages_on_screen_only():
    # Whisper thinks Italian is the most likely, but only English and Spanish are on screen
    probabilities = [("it", 0.5), ("es", 0.3), ("en", 0.05), ("fr", 0.1)]
    assert tr.choose_language(probabilities, "en", "es")[0] == "es"
    language, share = tr.choose_language([("en", 0.9), ("es", 0.1)], "en", "es")
    assert language == "en" and share == pytest.approx(0.9)
    assert tr.choose_language([("de", 1.0)], "en", "es") == ("en", 0.5)     # neither: stay on the left
    assert tr.speaker_for("es", "en", "es") == "right" and tr.speaker_for("de", "en", "es") == ""


def test_prompt_and_reply_cleaning():
    prompt = tr.translation_prompt("Where does it hurt?", "en", "fr")
    assert "English (en) to French (fr) translator" in prompt and prompt.rstrip().endswith("Where does it hurt?")
    assert tr.clean_translation('Here is the translation: "Où avez-vous mal ?"') == "Où avez-vous mal ?"
    assert tr.clean_translation("  «Où avez-vous mal ?»  ") == "Où avez-vous mal ?"
    assert tr.clean_translation("It's “fine”") == "It's “fine”"
    assert tr.clean_translation(None) == ""


def make(replies, **kw):
    seen, changes = [], []

    def translate(text, source, target, done):
        seen.append((text, source, target))
        reply = replies.get(text, "")
        done(reply or None, "" if reply else "no reply")

    conversation = tr.Conversation(translate, on_change=lambda e: changes.append((e.number, e.state)), **kw)
    return conversation, seen, changes


def test_each_speaker_is_translated_into_the_other_language():
    c, seen, changes = make({"Where does it hurt?": "¿Dónde le duele?", "En la rodilla.": "In the knee."})
    first = c.said("Where does it hurt?", "left")
    second = c.said("En la rodilla.", "right")
    assert seen == [("Where does it hurt?", "en", "es"), ("En la rodilla.", "es", "en")]
    assert (first.original, first.translation, first.state) == ("Where does it hurt?", "¿Dónde le duele?", "done")
    assert (second.speaker, second.translation) == ("right", "In the knee.")
    assert [n for n, _ in changes] == [1, 1, 2, 2]                   # added, then translated
    # each side of the screen reads as one conversation in its own language
    assert c.text_for("left") == "Where does it hurt?\n\nIn the knee."
    assert c.text_for("right") == "¿Dónde le duele?\n\nEn la rodilla."


def test_changing_and_swapping_languages():
    c, seen, _ = make({"Hello": "Bonjour"})
    c.set_languages(right="fr")
    c.said("Hello", "left")
    assert seen[-1] == ("Hello", "en", "fr")
    c.swap()
    assert (c.left, c.right) == ("fr", "en")


def test_same_language_on_both_sides_is_not_sent_to_the_ai():
    c, seen, _ = make({}, left="en", right="en")
    e = c.said("Hello", "left")
    assert seen == [] and e.translation == "Hello" and e.state == "done"


def test_failure_can_be_tried_again():
    replies = {}
    c, seen, _ = make(replies)
    e = c.said("Hello", "left")
    assert e.state == "failed" and e.problem == "no reply"
    replies["Hello"] = "Hola"
    c.retry(e)
    assert e.state == "done" and e.translation == "Hola" and len(seen) == 2


def test_an_ai_that_raises_is_reported_not_raised():
    def translate(text, source, target, done):
        raise ConnectionError("down")

    c = tr.Conversation(translate)
    e = c.said("Hello", "left")
    assert e.state == "failed" and "down" in e.problem


def test_empty_speech_and_unknown_speaker_are_ignored():
    c, seen, _ = make({})
    assert c.said("   ", "left") is None and c.said("Hello", "middle") is None and seen == []


def test_clear():
    c, _, _ = make({"Hi": "Hola"})
    c.said("Hi", "left")
    c.clear()
    assert c.exchanges == [] and c.text_for("left") == ""


def test_the_english_only_speech_model_is_replaced_by_the_multilingual_one():
    assert tr.is_english_only_label("Small (English Only)") and not tr.is_english_only_label("Small")
    assert tr.multilingual_label("Small (English Only)") == "Small"
    assert tr.multilingual_label("Turbo") == "Turbo"


def test_live_dictation_plain_mode_keeps_each_piece_as_said():
    import numpy as np
    from services import live_dictation as ld

    rate = 16000

    def stream_for(script):
        samples = []
        for seconds, amp in script:
            n = int(seconds * rate)
            t = np.arange(n) / rate
            samples.append((np.sin(2 * np.pi * 220 * t) * amp * 32767).astype(np.int16) if amp else np.zeros(n, np.int16))
        data = np.concatenate(samples).tobytes()

        class S:
            pos = 0

            def read(self, n, exception_on_overflow=False):
                chunk = data[self.pos:self.pos + n * 2]
                self.pos += n * 2
                if not chunk:
                    time.sleep(0.002)
                    return b"\x00" * (n * 2)
                return chunk.ljust(n * 2, b"\x00")

            def stop_stream(self):
                pass

            def close(self):
                pass
        return S()

    words = {1: "Where does it hurt", 2: "New line. In the knee"}
    transcribe = lambda audio: words.get(int(round(float(np.max(np.abs(audio))) * 10)), "")
    heard = []
    d = ld.LiveDictation(lambda: (stream_for([(1.5, 0.1), (0.8, 0), (1.5, 0.2), (0.8, 0)]), None), transcribe,
                         plain=True, on_piece=lambda n, t: heard.append(t))
    d.start()
    deadline = time.time() + 10
    while len(heard) < 2 and time.time() < deadline:
        time.sleep(0.02)
    d.stop(wait=5)
    assert heard == ["Where does it hurt", "New line. In the knee"]     # no line breaks, no lower-casing
