"""
extension_files.py

The copy of the Chrome extension in the home folder ("FreeScribe Chrome Extension"),
which Chrome loads it from. FreeScribe keeps that copy the same as its own, so an
update of FreeScribe updates the extension too: Chrome then only needs a reload,
which the extension's panel offers when it sees it is older than FreeScribe's.

This software is released under the AGPL-3.0 license.
"""

import filecmp
import json
import os
import shutil
from typing import Optional

FOLDER_NAME = "FreeScribe Chrome Extension"


def version_of(folder: str) -> str:
    """The extension version in ``folder``'s manifest ("" when there is none)."""
    try:
        with open(os.path.join(folder, "manifest.json"), encoding="utf-8") as f:
            return str(json.load(f).get("version", ""))
    except (OSError, ValueError):
        return ""


def default_target() -> str:
    return os.path.join(os.path.expanduser("~"), FOLDER_NAME)


def differs(source: str, target: str) -> bool:
    """Whether ``target`` is missing a file of ``source``, has another version of one, or has an extra one."""
    if not os.path.isdir(target):
        return True
    comparison = filecmp.dircmp(source, target, ignore=[".DS_Store"])

    def walk(c):
        if c.left_only or c.right_only or c.diff_files or c.funny_files:
            return True
        # dircmp compares by size and date; compare the contents of same-size files too
        _match, mismatch, errors = filecmp.cmpfiles(c.left, c.right, c.common_files, shallow=False)
        if mismatch or errors:
            return True
        return any(walk(sub) for sub in c.subdirs.values())

    return walk(comparison)


def refresh(source: str, target: Optional[str] = None, create: bool = False) -> bool:
    """
    Make ``target`` the same as ``source``. Only when ``target`` already exists (the clinician
    set the extension up from there), unless ``create``. Returns True when files were changed.
    """
    target = target or default_target()
    if not os.path.isdir(target) and not create:
        return False
    if not differs(source, target):
        return False
    os.makedirs(target, exist_ok=True)
    # Files the new version no longer has
    for root, dirs, files in os.walk(target, topdown=False):
        rel = os.path.relpath(root, target)
        for name in files:
            if name != ".DS_Store" and not os.path.exists(os.path.join(source, rel, name)):
                os.remove(os.path.join(root, name))
        for name in dirs:
            if not os.path.exists(os.path.join(source, rel, name)):
                shutil.rmtree(os.path.join(root, name), ignore_errors=True)
    shutil.copytree(source, target, dirs_exist_ok=True)
    return True
