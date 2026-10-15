"""
Tests for choosing the built-in AI model file.

This software is released under the AGPL-3.0 license.
"""
import os

import pytest

from utils import model_path


@pytest.fixture
def windows_or_linux(monkeypatch):
    monkeypatch.setattr(model_path, "_in_shared_resources", lambda: False)


def test_a_model_file_is_used_as_is(tmp_path, windows_or_linux):
    model = tmp_path / "my-model.gguf"
    model.write_bytes(b"x")
    assert model_path.local_model_path(str(model)) == (str(model), False)


def test_a_remote_model_name_falls_back_to_the_bundled_model(windows_or_linux):
    # The setting's default is a model name for AI servers, not a file
    assert model_path.local_model_path("gemma2:2b-instruct-q8_0") == ("./models/gemma-2-2b-it-Q8_0.gguf", True)
    assert model_path.local_model_path("") == ("./models/gemma-2-2b-it-Q8_0.gguf", True)
    assert model_path.local_model_path("./models/missing.gguf")[1] is True


def test_relative_paths_on_macos_use_the_shared_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(model_path, "_in_shared_resources", lambda: True)
    monkeypatch.setattr(model_path, "get_resource_path",
                        lambda filename, shared=False: os.path.join(str(tmp_path), filename))
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "custom.gguf").write_bytes(b"x")
    path, downloadable = model_path.local_model_path("models/custom.gguf")
    assert os.path.normpath(path) == str(tmp_path / "models" / "custom.gguf") and downloadable is False
    assert model_path.local_model_path("gemma2:2b-instruct-q8_0") == (
        str(tmp_path / "models" / "gemma-2-2b-it-Q8_0.gguf"), True)
