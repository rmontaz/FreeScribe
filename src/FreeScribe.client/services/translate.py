"""
translate.py

The logic behind the Translate window: two people talk, each in their own language, and
FreeScribe writes what was said and its translation, all on this computer.

- the languages offered (speech recognition and translation both cover them);
- telling who spoke: Whisper reports how likely each language is for what it heard, and
  the choice is made between the two languages on screen only, so a short phrase is not
  mistaken for a third language;
- the instruction given to the local AI model to translate (the wording TranslateGemma
  was trained on, which other models follow too);
- a conversation as a list of exchanges, each with the original and the translation;
- the speech model: the English-only models cannot do this, so the multilingual one of
  the same size is used instead.

This software is released under the AGPL-3.0 license.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Tuple

# (Whisper language code, name in English). The speech model writes all of these and
# the translation model (TranslateGemma covers 55 languages) is trained on them.
LANGUAGES: List[Tuple[str, str]] = [
    ("en", "English"), ("fr", "French"), ("es", "Spanish"), ("pt", "Portuguese"), ("de", "German"),
    ("it", "Italian"), ("nl", "Dutch"), ("pl", "Polish"), ("ru", "Russian"), ("uk", "Ukrainian"),
    ("ar", "Arabic"), ("fa", "Persian"), ("he", "Hebrew"), ("tr", "Turkish"), ("ur", "Urdu"),
    ("hi", "Hindi"), ("bn", "Bengali"), ("pa", "Punjabi"), ("gu", "Gujarati"), ("mr", "Marathi"),
    ("ta", "Tamil"), ("te", "Telugu"), ("kn", "Kannada"), ("zh", "Chinese"), ("ja", "Japanese"),
    ("ko", "Korean"), ("vi", "Vietnamese"), ("th", "Thai"), ("id", "Indonesian"), ("sw", "Swahili"),
    ("el", "Greek"), ("ro", "Romanian"), ("hu", "Hungarian"), ("cs", "Czech"), ("sk", "Slovak"),
    ("bg", "Bulgarian"), ("hr", "Croatian"), ("sr", "Serbian"), ("sl", "Slovenian"), ("sv", "Swedish"),
    ("da", "Danish"), ("no", "Norwegian"), ("fi", "Finnish"), ("et", "Estonian"), ("lv", "Latvian"),
    ("lt", "Lithuanian"), ("is", "Icelandic"), ("ca", "Catalan"),
]
_NAMES: Dict[str, str] = dict(LANGUAGES)
_CODES: Dict[str, str] = {name: code for code, name in LANGUAGES}

DEFAULT_LEFT = "en"
DEFAULT_RIGHT = "es"


def language_names() -> List[str]:
    return [name for _, name in LANGUAGES]


def language_name(code: str) -> str:
    return _NAMES.get(code, code or "")


def language_code(name: str) -> str:
    return _CODES.get(name, "")


# ---------------------------------------------------------------- who spoke
def choose_language(probabilities: Iterable[Tuple[str, float]], left: str, right: str,
                    margin: float = 0.0) -> Tuple[str, float]:
    """
    ``(language, share)``: whichever of the two languages on screen the speech model finds more likely,
    and its share of the two (0.5 to 1). ``probabilities`` is Whisper's list of ``(code, probability)``.
    Only the two languages count, so a short phrase is not taken for a third one.
    """
    table = {code: float(p) for code, p in probabilities}
    a, b = table.get(left, 0.0), table.get(right, 0.0)
    total = a + b
    if total <= 0:
        return left, 0.5
    if a >= b + margin:
        return left, a / total
    return right, b / total


def speaker_for(language: str, left: str, right: str) -> str:
    """"left" or "right": which side of the screen a language belongs to ("" when it is neither)."""
    return "left" if language == left else "right" if language == right else ""


# ---------------------------------------------------------------- the translation
def translation_prompt(text: str, source: str, target: str) -> str:
    """The instruction for the local AI model (TranslateGemma's own wording; other models follow it too)."""
    src, tgt = language_name(source), language_name(target)
    return (f"You are a professional {src} ({source}) to {tgt} ({target}) translator. Your goal is to accurately "
            f"convey the meaning and nuances of the original {src} text while adhering to {tgt} grammar, "
            f"vocabulary, and cultural sensitivities.\n"
            f"Produce only the {tgt} translation, without any additional explanations or commentary. "
            f"Please translate the following {src} text into {tgt}:\n\n\n{text.strip()}")


_PREFACE = re.compile(r"^(?:here is|here's|sure|translation|the translation)[^:\n]*:\s*", re.IGNORECASE)


def clean_translation(reply: str) -> str:
    """The translation alone: without an introduction, quotation marks or stray blank lines."""
    text = (reply or "").strip()
    text = _PREFACE.sub("", text).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'“”«»":
        text = text[1:-1].strip()
    if len(text) >= 2 and text[0] in "“«" and text[-1] in "”»":
        text = text[1:-1].strip()
    return text


# ---------------------------------------------------------------- the conversation
@dataclass
class Exchange:
    """One thing said: who said it, in which language, and the translation."""
    number: int
    speaker: str                 # "left" or "right"
    source: str                  # language code of what was said
    target: str                  # language code of the translation
    original: str
    translation: str = ""
    state: str = "translating"   # translating, done, failed
    problem: str = ""


Translate = Callable[[str, str, str, Callable[[Optional[str], str], None]], None]


class Conversation:
    """
    The exchanges of one conversation, with the two languages on screen.

    :param translate: ``(text, source, target, done)`` asks the AI to translate; calls ``done(reply, problem)``
        later (from any thread)
    :param on_change: called (from the thread that changed it) after an exchange is added or updated
    """

    def __init__(self, translate: Translate, on_change: Optional[Callable[[Exchange], None]] = None,
                 left: str = DEFAULT_LEFT, right: str = DEFAULT_RIGHT):
        self.translate = translate
        self.on_change = on_change
        self.left, self.right = left, right
        self.exchanges: List[Exchange] = []
        self._lock = threading.Lock()

    def set_languages(self, left: Optional[str] = None, right: Optional[str] = None):
        if left:
            self.left = left
        if right:
            self.right = right

    def swap(self):
        self.left, self.right = self.right, self.left

    def clear(self):
        with self._lock:
            self.exchanges = []

    def said(self, text: str, speaker: str) -> Optional[Exchange]:
        """Something was said by ``speaker`` ("left" or "right"): add it and start its translation."""
        text = (text or "").strip()
        if not text or speaker not in ("left", "right"):
            return None
        source, target = (self.left, self.right) if speaker == "left" else (self.right, self.left)
        with self._lock:
            exchange = Exchange(len(self.exchanges) + 1, speaker, source, target, text)
            self.exchanges.append(exchange)
        self._changed(exchange)
        if source == target:
            exchange.translation, exchange.state = text, "done"
            self._changed(exchange)
            return exchange

        def done(reply: Optional[str], problem: str = ""):
            translation = clean_translation(reply or "")
            if translation:
                exchange.translation, exchange.state = translation, "done"
            else:
                exchange.state, exchange.problem = "failed", problem or "The AI gave no translation."
            self._changed(exchange)

        try:
            self.translate(text, source, target, done)
        except Exception as e:      # the AI could not be reached
            done(None, f"The translation could not be made: {e}")
        return exchange

    def retry(self, exchange: Exchange):
        """Translate an exchange again after it failed."""
        exchange.state, exchange.problem, exchange.translation = "translating", "", ""
        self._changed(exchange)

        def done(reply: Optional[str], problem: str = ""):
            translation = clean_translation(reply or "")
            if translation:
                exchange.translation, exchange.state = translation, "done"
            else:
                exchange.state, exchange.problem = "failed", problem or "The AI gave no translation."
            self._changed(exchange)

        try:
            self.translate(exchange.original, exchange.source, exchange.target, done)
        except Exception as e:
            done(None, f"The translation could not be made: {e}")

    def text_for(self, side: str) -> str:
        """Everything on one side of the screen, in that side's language, as plain text."""
        lines = []
        for e in list(self.exchanges):
            if e.speaker == side:
                lines.append(e.original)
            elif e.translation:
                lines.append(e.translation)
        return "\n\n".join(lines)

    def _changed(self, exchange: Exchange):
        if self.on_change is not None:
            try:
                self.on_change(exchange)
            except Exception:
                pass


# ---------------------------------------------------------------- the speech model
_ENGLISH_ONLY = re.compile(r"\s*\(English Only\)\s*$")


def is_english_only_label(label: str) -> bool:
    return bool(_ENGLISH_ONLY.search(label or ""))


def multilingual_label(label: str) -> str:
    """The multilingual model of the same size ("Small (English Only)" -> "Small"); other labels are unchanged."""
    return _ENGLISH_ONLY.sub("", label or "").strip()
