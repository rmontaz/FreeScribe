"""
live_dictation.py

Dictation that is typed as you talk. The microphone is read continuously; each
time you pause (or after a long stretch without a pause) the audio so far is
transcribed and the text is handed on as the next *piece*. Pieces are numbered
so a caller that polls (the browser extension) can ask for "everything after
piece n".

Spoken "new line" and "new paragraph" become line breaks. Audio stays in memory
and is never written to disk.

Optionally the whole text is tidied by the AI when you stop (punctuation,
sentence breaks, obvious speech-recognition errors): see :func:`tidy_prompt`
and :func:`accept_tidy`, which refuses a tidy-up that changed any number or
dropped or added a lot of text.

This software is released under the AGPL-3.0 license.
"""

from __future__ import annotations

import queue
import re
import threading
import time
from typing import Callable, List, Optional, Tuple

import numpy as np

CHUNK = 512
RATE = 16000
PAUSE_SECONDS = 0.6        # a pause this long ends a piece
MIN_PIECE_SECONDS = 1.2    # pieces shorter than this wait for the next pause
MAX_PIECE_SECONDS = 12.0   # a piece is cut here even without a pause
MIN_SPEECH_SECONDS = 0.3   # a piece with less speech than this is silence: not transcribed
MAX_SECONDS = 15 * 60      # the recording stops itself after this


class LiveDictationError(Exception):
    """The microphone could not be used."""


def rms_silent(samples: np.ndarray, threshold: float = 0.012) -> bool:
    """Energy-based silence test (samples are float32, -1..1); used when no speech detector is given."""
    if not samples.size:
        return True
    return float(np.sqrt(np.mean(samples ** 2))) < threshold


_NEW_PARAGRAPH = re.compile(r"[\s,.;:]*\b(?:new|next) paragraph\b[\s,.;:]*", re.IGNORECASE)
_NEW_LINE = re.compile(r"[\s,.;:]*\b(?:new|next) line\b[\s,.;:]*", re.IGNORECASE)


def spoken_commands(text: str) -> str:
    """'new paragraph' and 'new line' (with the punctuation speech recognition puts around them) as line breaks."""
    text = _NEW_PARAGRAPH.sub("\n\n", text)
    text = _NEW_LINE.sub("\n", text)
    return text


def join_piece(previous: str, piece: str) -> str:
    """``piece`` with the space it needs after ``previous`` (the text typed so far)."""
    piece = piece.strip(" ")
    if not piece:
        return ""
    if not previous or previous.endswith(("\n", " ")) or piece.startswith(("\n", ",", ".", ";", ":", "?", "!")):
        return piece
    # A piece cut mid-sentence: speech recognition capitalises it as a new sentence
    if not re.search(r"[.?!:]\s*$", previous) and re.match(r"[A-Z][a-z]", piece) and not re.match(r"I\b", piece):
        piece = piece[0].lower() + piece[1:]
    return " " + piece


class LiveDictation:
    """
    Records until :meth:`stop`, transcribing piece by piece.

    :param open_stream: returns ``(stream, error)``; the stream needs ``read(n, exception_on_overflow=False)``,
        ``stop_stream()`` and ``close()`` (a PyAudio input stream of 16-bit mono at ``rate``)
    :param transcribe: float32 audio -> text (runs on a worker thread, one piece at a time)
    :param is_silent: float32 samples -> bool; the energy test when not given
    :param on_piece: called with ``(number, text)`` for every new piece (on the worker thread)
    :param on_finished: called once the last piece is done (on the worker thread)
    """

    def __init__(self, open_stream: Callable[[], Tuple[object, Optional[Exception]]],
                 transcribe: Callable[[np.ndarray], str], is_silent: Optional[Callable[[np.ndarray], bool]] = None,
                 on_piece: Optional[Callable[[int, str], None]] = None,
                 on_finished: Optional[Callable[[], None]] = None, chunk: int = CHUNK, rate: int = RATE,
                 pause_seconds: float = PAUSE_SECONDS, max_seconds: float = MAX_SECONDS):
        self.open_stream = open_stream
        self.transcribe = transcribe
        self.is_silent = is_silent or rms_silent
        self.on_piece = on_piece
        self.on_finished = on_finished
        self.chunk = chunk
        self.rate = rate
        self.pause_seconds = pause_seconds
        self.max_seconds = max_seconds
        self.level = 0.0
        self.error = ""
        self._pieces: List[str] = []
        self._lock = threading.Lock()
        self._audio: "queue.Queue[Optional[np.ndarray]]" = queue.Queue()
        self._running = threading.Event()
        self._finished = threading.Event()
        self._cancelled = False
        self._started = 0.0
        self._stream = None
        self._reader = None
        self._worker = None

    # ------------------------------------------------------------ state
    @property
    def recording(self) -> bool:
        return self._running.is_set()

    @property
    def finished(self) -> bool:
        """Recording stopped and every piece transcribed."""
        return self._finished.is_set()

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._started if self._started else 0.0

    @property
    def text(self) -> str:
        with self._lock:
            return "".join(self._pieces)

    def pieces(self, since: int = 0) -> List[Tuple[int, str]]:
        """The pieces after number ``since`` (numbers start at 1)."""
        with self._lock:
            return [(n, p) for n, p in enumerate(self._pieces, 1) if n > since]

    # ------------------------------------------------------------ control
    def start(self):
        if self.recording:
            return
        stream, error = self.open_stream()
        if stream is None:
            raise LiveDictationError(f"The microphone could not be opened: {error}")
        self._stream = stream
        self._started = time.monotonic()
        self._running.set()
        self._reader = threading.Thread(target=self._read, daemon=True, name="live-dictation-mic")
        self._worker = threading.Thread(target=self._work, daemon=True, name="live-dictation-text")
        self._reader.start()
        self._worker.start()

    def stop(self, wait: float = 0.0) -> bool:
        """Stops recording; the last piece is still transcribed. Waits up to ``wait`` seconds for that."""
        self._running.clear()
        if wait:
            return self._finished.wait(wait)
        return self.finished

    def cancel(self):
        """Stops without transcribing what is left."""
        self._cancelled = True
        self._running.clear()

    # ------------------------------------------------------------ threads
    def _read(self):
        frames: List[np.ndarray] = []
        piece_seconds = speech_seconds = silent_run = 0.0
        step = self.chunk / self.rate
        try:
            while self._running.is_set():
                if self.elapsed >= self.max_seconds:
                    break
                try:
                    data = self._stream.read(self.chunk, exception_on_overflow=False)
                except Exception as e:      # the microphone went away
                    self.error = f"The microphone stopped: {e}"
                    break
                samples = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
                if not samples.size:
                    time.sleep(0.005)
                    continue
                self.level = min(1.0, float(np.sqrt(np.mean(samples ** 2))) * 32768 / 3000.0)
                frames.append(samples)
                piece_seconds += step
                if self.is_silent(samples):
                    silent_run += step
                else:
                    silent_run = 0.0
                    speech_seconds += step
                paused = silent_run >= self.pause_seconds and piece_seconds >= MIN_PIECE_SECONDS
                if (paused and speech_seconds >= MIN_SPEECH_SECONDS) or piece_seconds >= MAX_PIECE_SECONDS:
                    if speech_seconds >= MIN_SPEECH_SECONDS:
                        self._audio.put(np.concatenate(frames))
                    # Keep a moment of the end so the next word is not cut
                    keep = max(1, int(0.1 / step))
                    frames = frames[-keep:] if not paused else []
                    piece_seconds = speech_seconds = silent_run = 0.0
                elif paused and speech_seconds < MIN_SPEECH_SECONDS and piece_seconds > 3.0:
                    frames = frames[-int(0.5 / step):]   # only silence so far: don't let it pile up
                    piece_seconds = len(frames) * step
            if frames and speech_seconds >= MIN_SPEECH_SECONDS and not self._cancelled:
                self._audio.put(np.concatenate(frames))
        finally:
            self._running.clear()
            self.level = 0.0
            for action in ("stop_stream", "close"):
                try:
                    getattr(self._stream, action)()
                except Exception:
                    pass
            self._audio.put(None)

    def _work(self):
        try:
            while True:
                audio = self._audio.get()
                if audio is None or self._cancelled:
                    break
                try:
                    text = self.transcribe(audio) or ""
                except Exception as e:
                    self.error = f"What you said could not be transcribed: {e}"
                    continue
                text = spoken_commands(text.strip())
                with self._lock:
                    piece = join_piece("".join(self._pieces), text)
                    if not piece:
                        continue
                    self._pieces.append(piece)
                    number = len(self._pieces)
                if self.on_piece is not None:
                    try:
                        self.on_piece(number, piece)
                    except Exception:
                        pass
        finally:
            self._finished.set()
            if self.on_finished is not None:
                try:
                    self.on_finished()
                except Exception:
                    pass


# ------------------------------------------------------------------ AI tidy-up
def tidy_prompt(text: str) -> str:
    return (
        "The text below was dictated by a clinician for a patient's chart and typed by speech recognition. "
        "Correct only punctuation, capital letters, sentence breaks and words that speech recognition clearly "
        "misheard (use medical spelling). Do not add, remove, summarise or reorder anything. Keep every number, "
        "dose, unit and name exactly as it is, and keep the line breaks. Reply with the corrected text only, "
        "without any introduction or quotation marks.\n\n"
        f"Dictated text:\n{text}")


_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def accept_tidy(original: str, tidied: str) -> Tuple[str, str]:
    """
    ``(text, problem)``: the tidied text when it is safe to use, otherwise the original
    and why the tidy-up was not used.
    """
    tidied = (tidied or "").strip()
    tidied = re.sub(r"^(?:here is|here's|corrected text)[^:\n]*:\s*", "", tidied, flags=re.IGNORECASE).strip()
    if len(tidied) >= 2 and tidied[0] == tidied[-1] and tidied[0] in "\"'“”":
        tidied = tidied[1:-1].strip()
    tidied = tidied.replace("“", "").replace("”", "") if '"' not in original and "“" not in original else tidied
    if not tidied:
        return original, "The AI gave no text back."
    if sorted(_NUMBER.findall(original.replace(",", ""))) != sorted(_NUMBER.findall(tidied.replace(",", ""))):
        return original, "The AI's version changed a number, so what you said is kept as it was."
    words_before, words_after = len(original.split()), len(tidied.split())
    if words_before and not (0.75 * words_before - 2 <= words_after <= 1.25 * words_before + 2):
        return original, "The AI's version added or left out too much, so what you said is kept as it was."
    return tidied, ""
