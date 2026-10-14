"""
ui_state.py

Small persistent store for interface preferences that are not application
settings: the chosen level of detail for notes, the last export folder, the
window size, and so on.

Values live in ``ui_state.json`` next to the other user data files. The file is
purely cosmetic state, so any problem reading or writing it is logged and
otherwise ignored - the interface just falls back to its defaults.

This software is released under the AGPL-3.0 license.
"""

import json
import os
import tempfile

from utils.file_utils import get_resource_path
from utils.log_config import logger

_FILE_NAME = "ui_state.json"


class UIState:
    """Dictionary-like preferences that save themselves when changed."""

    def __init__(self, path: str = None):
        self.path = path or get_resource_path(_FILE_NAME)
        self._data = {}
        self.load()

    def load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as file:
                data = json.load(file)
            self._data = data if isinstance(data, dict) else {}
        except FileNotFoundError:
            self._data = {}
        except (OSError, ValueError) as error:
            logger.warning(f"Could not read interface preferences, using defaults: {error}")
            self._data = {}

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value) -> None:
        if self._data.get(key) == value:
            return
        self._data[key] = value
        self.save()

    def save(self) -> None:
        try:
            directory = os.path.dirname(os.path.abspath(self.path))
            handle, temp_path = tempfile.mkstemp(prefix=".ui_state_", suffix=".tmp", dir=directory)
            with os.fdopen(handle, "w", encoding="utf-8") as file:
                json.dump(self._data, file, indent=2)
            os.replace(temp_path, self.path)
        except OSError as error:
            logger.warning(f"Could not save interface preferences: {error}")
