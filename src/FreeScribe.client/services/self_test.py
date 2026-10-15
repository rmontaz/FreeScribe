"""
self_test.py

An unattended check that an installed FreeScribe works end to end, used by the
installer tests:

    FreeScribe --self-test visit.wav [--self-test-report report.json]

The app starts as usual, waits for its speech and AI models, transcribes the
audio file, writes a note with the default template, saves a report and exits
(exit code 0 when every step worked, 1 otherwise). Nothing is saved to the
notes history and no patient data is involved: the audio is a scripted test
conversation.

This module holds the parts that do not need the running app (reading the
audio, the report); client.py runs the steps.

This software is released under the AGPL-3.0 license.
"""

from __future__ import annotations

import json
import os
import platform
import sys
import time
import traceback
import wave
from typing import Optional

import numpy as np

RATE = 16000
MODEL_TIMEOUT = 15 * 60          # seconds; a first start may download the speech model


def load_wav(path: str, rate: int = RATE) -> np.ndarray:
    """A WAV file as mono float32 samples in -1..1 at ``rate`` Hz."""
    with wave.open(path, "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        source_rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    if width == 2:
        samples = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 4:
        samples = np.frombuffer(frames, dtype="<i4").astype(np.float32) / 2147483648.0
    elif width == 1:
        samples = (np.frombuffer(frames, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:
        raise ValueError(f"Unsupported WAV sample width: {width} bytes")
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    if source_rate != rate:
        from math import gcd
        from scipy.signal import resample_poly
        divisor = gcd(source_rate, rate)
        samples = resample_poly(samples, rate // divisor, source_rate // divisor).astype(np.float32)
    return np.clip(samples, -1.0, 1.0).astype(np.float32)


class Report:
    """What happened at each step, written as JSON."""

    def __init__(self, audio_path: str, report_path: Optional[str] = None, version: str = ""):
        self.path = report_path or (os.path.splitext(audio_path)[0] + ".selftest.json")
        self.started = time.time()
        self.data = {
            "version": version,
            "platform": f"{platform.system()} {platform.release()} {platform.machine()}",
            "frozen": bool(getattr(sys, "frozen", False)),
            "audio": os.path.abspath(audio_path),
            "steps": {},
            "ok": False,
        }

    def step(self, name: str, ok: bool, **details):
        details["ok"] = bool(ok)
        details["seconds"] = round(time.time() - self.started, 1)
        self.data["steps"][name] = details
        self.save()

    def failure(self, name: str, error: BaseException):
        self.step(name, False, error=f"{type(error).__name__}: {error}",
                  traceback=traceback.format_exc()[-4000:])

    def finish(self) -> int:
        steps = self.data["steps"]
        self.data["ok"] = bool(steps) and all(s.get("ok") for s in steps.values()) and "note" in steps
        self.data["total_seconds"] = round(time.time() - self.started, 1)
        self.save()
        return 0 if self.data["ok"] else 1

    def save(self):
        folder = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(folder, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(self.data, handle, indent=2)
