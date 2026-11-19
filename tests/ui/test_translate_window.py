"""The Translate window, driven with a fake speech engine and a fake translator.

This software is released under the AGPL-3.0 license.
"""
import time

import pytest

tk = pytest.importorskip("tkinter")


class FakeState(dict):
    def set(self, key, value):
        self[key] = value


class FakeDictation:
    def __init__(self, plan, on_utterance):
        self.plan, self.on_utterance = plan, on_utterance
        self.recording, self.level, self.error = False, 0.3, ""
        self.stopped = False

    def start(self):
        self.recording = True

    def stop(self):
        self.recording, self.stopped = False, True

    def hear(self, text, language):
        self.on_utterance(text, language)


class FakeBackend:
    def __init__(self, problem="", auto=True):
        self.problem, self.auto = problem, auto
        self.dictations, self.asked = [], []

    def prepare(self, done):
        done(self.problem)

    def auto_available(self):
        return self.auto

    def new_dictation(self, plan, on_utterance):
        d = FakeDictation(plan, on_utterance)
        self.dictations.append(d)
        return d

    def translate(self, text, source, target, done):
        self.asked.append((text, source, target))
        done(f"[{target}] {text}", "")


@pytest.fixture(scope="module")
def root():
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


def pump(root, seconds=0.2):
    end = time.time() + seconds
    while time.time() < end:
        root.update()
        time.sleep(0.01)


def make(root, backend=None, state=None):
    from UI.TranslateWindow import TranslateWindow
    TranslateWindow._open = None
    backend = backend or FakeBackend()
    window = TranslateWindow(root, backend, lambda f: f(), state if state is not None else FakeState())
    pump(root)
    return window, backend


def text_of(window, side):
    return window.panes[side].get("1.0", "end").strip()


def test_two_sides_show_what_was_said_and_the_translation(root):
    window, backend = make(root)
    window.toggle("auto")
    pump(root)
    d = backend.dictations[0]
    d.hear("Where does it hurt?", "en")
    d.hear("En la rodilla.", "es")
    assert backend.asked == [("Where does it hurt?", "en", "es"), ("En la rodilla.", "es", "en")]
    assert text_of(window, "left") == "Where does it hurt?\n[en] En la rodilla."
    assert text_of(window, "right") == "[es] Where does it hurt?\nEn la rodilla."
    window.close()


def test_a_language_that_is_neither_goes_to_the_side_being_listened_for(root):
    window, backend = make(root)
    window.toggle("right")
    pump(root)
    backend.dictations[0].hear("Ça fait mal", "fr")
    assert backend.asked[-1][1:] == ("es", "en")                 # taken as the right-hand language
    window.close()


def test_buttons_follow_the_languages_and_the_state(root):
    window, backend = make(root)
    assert window.left_mic.text == "Speak English" and window.right_mic.text == "Speak Spanish"
    window.right_box.set("French")
    window._languages_changed()
    assert window.right_mic.text == "Speak French"
    window.toggle("left")
    pump(root)
    assert window.mode == "left" and window.left_mic.text == "Stop"
    assert not window.right_mic.enabled and not window.auto_mic.enabled     # one at a time
    window.toggle("left")
    assert window.mode == "" and backend.dictations[0].stopped and window.right_mic.enabled
    window.close()


def test_changing_a_language_stops_listening_and_is_remembered(root):
    state = FakeState()
    window, backend = make(root, state=state)
    window.toggle("auto")
    pump(root)
    window.left_box.set("German")
    window._languages_changed()
    assert window.mode == "" and backend.dictations[0].stopped
    assert state["translate_left"] == "de" and state["translate_right"] == "es"
    window.close()
    again, _ = make(root, state=state)
    assert again.left_box.get() == "German"
    again.close()


def test_swap_exchanges_the_languages(root):
    window, backend = make(root)
    window.swap()
    assert (window.left_box.get(), window.right_box.get()) == ("Spanish", "English")
    assert (window.conversation.left, window.conversation.right) == ("es", "en")
    window.close()


def test_a_problem_is_shown_and_nothing_starts(root):
    window, backend = make(root, FakeBackend(problem="The microphone is in use for the visit recording."))
    window.toggle("left")
    pump(root)
    assert backend.dictations == [] and window.mode == ""
    assert "visit recording" in window.status.cget("text")
    window.close()


def test_auto_is_unavailable_when_the_speech_engine_cannot_tell_the_language(root):
    window, backend = make(root, FakeBackend(auto=False))
    assert not window.auto_mic.enabled and window.left_mic.enabled
    window.close()


def test_text_size_has_limits_and_is_remembered(root):
    state = FakeState()
    window, _ = make(root, state=state)
    window.set_font(1000)
    assert window.font_size == 96 and state["translate_font"] == 96
    window.set_font(1)
    assert window.font_size == 14
    window.close()


def test_clear_and_failed_translation(root):
    window, backend = make(root)
    backend.translate = lambda text, s, t, done: done(None, "The AI could not be reached")
    window.toggle("left")
    pump(root)
    backend.dictations[0].hear("Hello", "en")
    assert "not translated: The AI could not be reached" in text_of(window, "right")
    window.clear()
    assert text_of(window, "left") == "" and text_of(window, "right") == ""
    window.close()


def test_opening_translate_twice_gives_one_window(root):
    from UI.TranslateWindow import TranslateWindow
    TranslateWindow._open = None
    backend = FakeBackend()
    first = TranslateWindow.show(root, backend, lambda f: f(), FakeState())
    second = TranslateWindow.show(root, backend, lambda f: f(), FakeState())
    assert first is second
    first.close()
