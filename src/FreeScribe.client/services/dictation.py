"""
dictation.py

Short recordings for talking through a form. Audio is kept in memory only
(never written to disk) and handed back as 16 kHz mono float samples, the
shape Whisper takes.

This software is released under the AGPL-3.0 license.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional, Tuple

import numpy as np

# Long enough for any form; the recording stops itself after this
MAX_SECONDS = 10 * 60


class DictationError(Exception):
    """The microphone could not be used."""


class Dictation:
    """
    Records from a microphone stream until :meth:`stop`.

    :param open_stream: returns ``(stream, error)``; the stream needs ``read(n, exception_on_overflow=False)``,
        ``stop_stream()`` and ``close()`` (a PyAudio input stream)
    :param chunk: frames read at a time
    :param rate: sample rate of the stream
    """

    def __init__(self, open_stream: Callable[[], Tuple[object, Optional[Exception]]], chunk: int = 512,
                 rate: int = 16000, max_seconds: int = MAX_SECONDS):
        self.open_stream = open_stream
        self.chunk = chunk
        self.rate = rate
        self.max_seconds = max_seconds
        self._frames = []
        self._stream = None
        self._thread = None
        self._running = threading.Event()
        self._started = 0.0
        self.level = 0.0          # 0.0 - 1.0, for a meter

    @property
    def recording(self) -> bool:
        return self._running.is_set()

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._started if self.recording else 0.0

    def start(self):
        if self.recording:
            return
        stream, error = self.open_stream()
        if stream is None:
            raise DictationError(f"The microphone could not be opened: {error}")
        self._stream = stream
        self._frames = []
        self._started = time.monotonic()
        self._running.set()
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()

    def _read(self):
        limit = self.max_seconds * self.rate / self.chunk
        while self._running.is_set() and len(self._frames) < limit:
            try:
                data = self._stream.read(self.chunk, exception_on_overflow=False)
            except Exception:
                break
            self._frames.append(data)
            samples = np.frombuffer(data, dtype=np.int16)
            if samples.size:
                rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))
                self.level = min(1.0, rms / 3000.0)
        self._running.clear()

    def stop(self) -> np.ndarray:
        """Stops recording and returns the audio as float32 samples in -1..1."""
        self._running.clear()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        if self._stream is not None:
            for action in ("stop_stream", "close"):
                try:
                    getattr(self._stream, action)()
                except Exception:
                    pass
            self._stream = None
        self.level = 0.0
        audio = b"".join(self._frames)
        self._frames = []
        return np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0

    def cancel(self):
        self.stop()


def to_wav_bytes(audio: np.ndarray, rate: int = 16000) -> bytes:
    """``audio`` (float32, -1..1) as a 16-bit mono WAV file."""
    import io
    import wave
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm)
    return buffer.getvalue()
