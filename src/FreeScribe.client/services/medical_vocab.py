"""
medical_vocab.py

Medical vocabulary for speech-to-text, and a check for misheard drug names.

* :func:`build_speech_prompt` writes the short text Whisper is given before each
  piece of audio (its "initial prompt"). Whisper reads only about 200 tokens of
  it, so it is built per visit: a few common clinical terms, the clinician's own
  words, and the drug names found in the session's Context (for example the
  patient's medication list). Only drug names are taken from the Context, never
  other details such as names.
* :class:`DrugLexicon` holds generic and brand drug names and finds words in a
  transcript or note that look like a misheard drug name ("amlodapine" ->
  "amlodipine"). It only suggests; nothing is changed automatically.

The drug names come from Health Canada's Drug Product Database (Open
Government Licence - Canada) and the U.S. National Library of Medicine's RxNorm
Current Prescribable Content; see assets/medical/drug_names.txt.

This software is released under the AGPL-3.0 license.
"""

from __future__ import annotations

import difflib
import os
import re
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# Whisper keeps the last ~223 tokens of a prompt; ~600 characters stays well inside that
PROMPT_CHAR_LIMIT = 600
PROMPT_LEAD = "Clinical visit between a clinician and a patient."
# Common terms that generic speech models get wrong or write inconsistently
BASE_TERMS = (
    "shortness of breath", "chest pain", "blood pressure", "mmHg", "SpO2", "ECG", "CBC", "HbA1c", "eGFR",
    "TSH", "INR", "COPD", "CHF", "GERD", "UTI", "PRN", "b.i.d.", "t.i.d.", "mg", "mcg",
)

# Words that sit close to a drug name but are ordinary words (checked with an English dictionary)
_WORD = re.compile(r"[A-Za-z][A-Za-z\-]{4,}")
MIN_LENGTH = 6
SIMILARITY = 0.84


def _default_path():
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "medical",
                        "drug_names.txt")


def _default_common_path():
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "medical",
                        "common_lookalikes.txt")


class DrugLexicon:
    """Generic and brand drug names, with lookup and near-miss suggestions."""

    def __init__(self, generics: Iterable[str] = (), brands: Iterable[str] = (), common: Iterable[str] = ()):
        self.display: Dict[str, str] = {}
        for name in generics:
            self.display.setdefault(name.lower(), name)
        for name in brands:
            self.display.setdefault(name.lower(), name)
        self.common = {word.lower() for word in common}
        # Single words indexed by three-letter pieces, for a fast near-miss search
        self._index: Dict[str, List[str]] = {}
        for key in self.display:
            if " " not in key and len(key) >= MIN_LENGTH:
                for piece in _trigrams(key):
                    self._index.setdefault(piece, []).append(key)
        self._phrases = sorted((k for k in self.display if " " in k), key=len, reverse=True)
        self._cache: Dict[str, List[str]] = {}

    @classmethod
    def load(cls, path: Optional[str] = None, common_path: Optional[str] = None) -> "DrugLexicon":
        generics, brands, common = [], [], []
        path = path or _default_path()
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as handle:
                for line in handle:
                    line = line.rstrip("\n")
                    if not line or line.startswith("#"):
                        continue
                    if line.startswith("B "):
                        brands.append(line[2:].strip())
                    else:
                        generics.append(line.strip())
        common_path = common_path or _default_common_path()
        if os.path.isfile(common_path):
            with open(common_path, encoding="utf-8") as handle:
                common = [line.strip() for line in handle if line.strip() and not line.startswith("#")]
        return cls(generics, brands, common)

    def __len__(self):
        return len(self.display)

    def __contains__(self, name: str) -> bool:
        return name.lower() in self.display

    # ------------------------------------------------------------------ lookup
    def find_in_text(self, text: str, limit: int = 40) -> List[str]:
        """Drug names mentioned in ``text``, in order of first appearance."""
        found, seen = [], set()
        lowered = (text or "").lower()
        positions, covered = [], []
        for phrase in self._phrases:
            index = lowered.find(phrase)
            if index >= 0 and _is_word_boundary(lowered, index, len(phrase)):
                positions.append((index, phrase))
                covered.append((index, index + len(phrase)))
        for match in re.finditer(r"[A-Za-z][A-Za-z\-]{2,}", text or ""):
            key = match.group(0).lower()
            inside = any(start <= match.start() < end for start, end in covered)
            if key in self.display and not inside:
                positions.append((match.start(), key))
        for _index, key in sorted(positions):
            if key not in seen:
                seen.add(key)
                found.append(self.display[key])
                if len(found) >= limit:
                    break
        return found

    def suggestions(self, word: str, count: int = 3) -> List[str]:
        """Drug names that ``word`` may be a mishearing of (empty when it is a known word)."""
        key = word.lower().strip("-")
        if key in self._cache:
            return self._cache[key]
        result: List[str] = []
        if len(key) >= MIN_LENGTH and key not in self.display and key not in self.common:
            pieces = _trigrams(key)
            shared: Dict[str, int] = {}
            for piece in pieces:
                for name in self._index.get(piece, ()):
                    shared[name] = shared.get(name, 0) + 1
            # A close spelling shares most of its three-letter pieces
            need = max(3, int(len(pieces) * 0.5))
            pool = [name for name, count in shared.items()
                    if count >= need and name[0] == key[0] and abs(len(name) - len(key)) <= 2]
            scored = []
            for name in pool:
                matcher = difflib.SequenceMatcher(None, key, name)
                if matcher.real_quick_ratio() < SIMILARITY or matcher.quick_ratio() < SIMILARITY:
                    continue
                ratio = matcher.ratio()
                if ratio >= SIMILARITY:
                    scored.append((ratio, name))
            scored.sort(reverse=True)
            result = [self.display[name] for _ratio, name in scored[:count]]
        self._cache[key] = result
        return result

    def flags(self, text: str, ignore: Iterable[str] = ()) -> List[Tuple[int, int, str, List[str]]]:
        """Words in ``text`` that look like misheard drug names: (start, end, word, suggestions)."""
        ignored = {word.lower() for word in ignore}
        found = []
        for match in _WORD.finditer(text or ""):
            word = match.group(0)
            if word.lower() in ignored:
                continue
            options = self.suggestions(word)
            if options:
                found.append((match.start(), match.end(), word, options))
        return found


def _trigrams(word: str):
    padded = f"^{word}$"
    return {padded[i:i + 3] for i in range(len(padded) - 2)}


def _is_word_boundary(text: str, index: int, length: int) -> bool:
    before = text[index - 1] if index > 0 else " "
    after = text[index + length] if index + length < len(text) else " "
    return not before.isalnum() and not after.isalnum()


def split_terms(value: str) -> List[str]:
    """The clinician's own words: one per line or separated by commas."""
    if not value or value.strip().lower() == "none":
        return []
    parts = re.split(r"[,\n;]+", value)
    return [part.strip() for part in parts if part.strip()]


def build_speech_prompt(context: str = "", own_terms: Sequence[str] = (), lexicon: Optional[DrugLexicon] = None,
                        limit: int = PROMPT_CHAR_LIMIT) -> str:
    """
    The Whisper prompt for one visit. Whisper keeps the end of a long prompt, so the
    most specific words (this patient's drugs, then the clinician's own words) go last,
    and the general terms are dropped first when there is not enough room.
    """
    drugs = lexicon.find_in_text(context) if (lexicon is not None and context) else []
    specific = _unique(list(own_terms) + drugs)
    general = [term for term in BASE_TERMS if term.lower() not in {s.lower() for s in specific}]

    def compose(general_terms, specific_terms):
        terms = list(general_terms) + list(specific_terms)
        return f"{PROMPT_LEAD} Terms: {', '.join(terms)}." if terms else PROMPT_LEAD

    while general and len(compose(general, specific)) > limit:
        general.pop()
    while specific and len(compose(general, specific)) > limit:
        specific.pop(0)
    return compose(general, specific)


def _unique(items):
    seen, result = set(), []
    for item in items:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def is_prompt_echo(text: str, prompt: str) -> bool:
    """
    Whisper sometimes "hears" its prompt in silence or noise. True when ``text`` is a
    run of the prompt itself (three words or more, in the prompt's order), so the piece
    can be dropped. Real speech that merely uses the same terms is kept.
    """
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    if len(words) < 3 or not prompt:
        return False
    prompt_words = re.findall(r"[a-z0-9]+", prompt.lower())
    return f" {' '.join(words)} " in f" {' '.join(prompt_words)} "


class VisitLanguage:
    """
    Keeps one language for a whole visit when the setting is Auto-detect.

    Whisper guesses the language again for every piece of audio; short pieces are
    easy to misjudge, which makes a visit switch language half way. The first
    confident guess from a long enough piece is kept for the rest of the visit.
    """

    MIN_SECONDS = 6.0
    MIN_PROBABILITY = 0.75

    def __init__(self):
        self.language: Optional[str] = None

    def reset(self):
        self.language = None

    def observe(self, language: Optional[str], probability: float, seconds: float):
        if self.language is None and language and probability >= self.MIN_PROBABILITY \
                and seconds >= self.MIN_SECONDS:
            self.language = language
        return self.language
