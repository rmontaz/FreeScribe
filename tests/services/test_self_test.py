"""
Tests for the --self-test helpers (reading the test audio, the report).

This software is released under the AGPL-3.0 license.
"""
import json
import wave

import numpy as np

from services import self_test


def write_wav(path, samples, rate, channels=1):
    data = (np.clip(samples, -1, 1) * 32767).astype("<i2")
    if channels == 2:
        data = np.stack([data, data], axis=1).reshape(-1)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(data.tobytes())


def test_wav_is_read_as_16k_mono(tmp_path):
    rate = 22050
    tone = 0.5 * np.sin(2 * np.pi * 440 * np.arange(rate * 2) / rate)
    write_wav(tmp_path / "a.wav", tone, rate, channels=2)
    audio = self_test.load_wav(str(tmp_path / "a.wav"))
    assert audio.dtype == np.float32
    assert abs(len(audio) - 2 * 16000) <= 2
    assert 0.4 < float(np.abs(audio).max()) <= 0.51


def test_wav_at_16k_is_unchanged(tmp_path):
    samples = np.linspace(-0.5, 0.5, 16000)
    write_wav(tmp_path / "b.wav", samples, 16000)
    audio = self_test.load_wav(str(tmp_path / "b.wav"))
    assert len(audio) == 16000 and abs(float(audio[-1]) - 0.5) < 1e-3


def test_report_passes_only_when_every_step_did(tmp_path):
    report = self_test.Report(str(tmp_path / "visit.wav"), version="v1")
    report.step("models", True)
    report.step("transcript", True, text="chest pain")
    assert report.finish() == 1                          # no note yet
    report.step("note", True, text="S: chest pain")
    assert report.finish() == 0
    saved = json.loads((tmp_path / "visit.selftest.json").read_text())
    assert saved["ok"] is True and saved["steps"]["transcript"]["text"] == "chest pain"


def test_report_records_failures(tmp_path):
    report = self_test.Report(str(tmp_path / "visit.wav"), str(tmp_path / "out" / "r.json"))
    try:
        raise RuntimeError("model missing")
    except RuntimeError as error:
        report.failure("error", error)
    assert report.finish() == 1
    saved = json.loads((tmp_path / "out" / "r.json").read_text())
    assert "model missing" in saved["steps"]["error"]["error"]
