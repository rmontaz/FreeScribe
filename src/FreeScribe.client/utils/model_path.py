"""
model_path.py

Finds the file the built-in AI model is loaded from.

The "AI Model" setting is shared by the built-in model and remote AI servers.
For a server it is a model name (the default, ``gemma2:2b-instruct-q8_0``);
for the built-in model it should be a model file. When it does not point to a
file, the model FreeScribe ships with is used, from where installers put it.

This software is released under the AGPL-3.0 license.
"""

import os

import utils.system
from utils.file_utils import get_resource_path, is_flatpak

BUNDLED_MODEL = "gemma-2-2b-it-Q8_0.gguf"


def _in_shared_resources() -> bool:
    """macOS and Flatpak keep models in the shared application data folder."""
    return utils.system.is_macos() or is_flatpak()


def bundled_model_path() -> str:
    if _in_shared_resources():
        return get_resource_path(filename=f"models/{BUNDLED_MODEL}", shared=True)
    return f"./models/{BUNDLED_MODEL}"


def local_model_path(setting: str):
    """
    The model file to load for the "AI Model" setting.

    Returns ``(path, used_fallback)``; ``used_fallback`` is True when the setting
    is not a model file (for example a remote model name) and the bundled model
    is used instead.
    """
    path = (setting or "").strip()
    if path and _in_shared_resources() and not os.path.isabs(path):
        path = get_resource_path(filename=path, shared=True)
    if path and os.path.isfile(path):
        return path, False
    return bundled_model_path(), True
