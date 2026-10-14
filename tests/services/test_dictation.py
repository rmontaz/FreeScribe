"""
Tests for in-memory dictation and the form source it feeds.

This software is released under the AGPL-3.0 license.
"""
import io
import time
import wave

import numpy as np
import pytest

from services.dictation import Dictation, DictationError, to_wav_bytes
from services.form_filler import compose_source


class FakeStream:
    def __init__(self, value=1000):
        self.value = value
        self.closed = False

    def read(self, n, exception_on_overflow=False):
        time.sleep(0.001)
        return (np.full(n, self.value, dtype=np.int16)).tobytes()

    def stop_stream(self):
        pass

    def close(self):
        self.closed = True


def test_records_until_stopped_and_returns_float_audio():
    stream = FakeStream(16384)
    dictation = Dictation(lambda: (stream, None), chunk=256)
    dictation.start()
    time.sleep(0.05)
    assert dictation.recording and dictation.level > 0
    audio = dictation.stop()
    assert not dictation.recording and stream.closed
    assert audio.dtype == np.float32 and audio.size > 0 and abs(audio[0] - 0.5) < 1e-3


def test_microphone_failure_is_reported():
    dictation = Dictation(lambda: (None, OSError("no device")))
    with pytest.raises(DictationError, match="no device"):
        dictation.start()


def test_wav_bytes():
    data = to_wav_bytes(np.zeros(1600, dtype=np.float32))
    with wave.open(io.BytesIO(data)) as wav:
        assert wav.getframerate() == 16000 and wav.getnframes() == 1600 and wav.getnchannels() == 1


def test_dictation_comes_first_in_the_form_source():
    source = compose_source(title="Maria", note="A: strain", dictation="Off work March 3 to 10.")
    assert source.index("What the clinician said for this form") < source.index("Clinician's note")
    assert "Off work March 3 to 10." in source
    assert "clinician said" not in compose_source(note="x")
