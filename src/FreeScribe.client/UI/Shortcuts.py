"""
Shortcuts.py

The app's keyboard shortcuts (start/stop transcribing, pause, dictate), which the
clinician can change or turn off under Help > Keyboard shortcuts.

They work while a FreeScribe window is in front. A shortcut is written like
"Alt+R", "Ctrl+Shift+D", "Cmd+Shift+D" (Mac) or "F8"; "" turns it off.

Matching is done on every key press (one handler for the whole app) rather than
with a Tk event pattern per shortcut, because on a Mac Option+letter arrives as
the special character it types ("∂" for Option+D) instead of the letter. When a
shortcut that types a character (Option on a Mac) is pressed in a text box, that
character is taken out again, so it never lands in the note.

This software is released under the AGPL-3.0 license.
"""

import re
import tkinter as tk
from typing import Callable, Dict, Optional

ACTIONS = ("record", "pause", "dictate")
DEFAULTS = {"record": "Alt+R", "pause": "Alt+P", "dictate": "Alt+D"}
NAMES = {"record": "Start or stop transcribing", "pause": "Pause or resume", "dictate": "Dictate at the cursor"}
MODIFIERS = ("Ctrl", "Cmd", "Alt", "Shift")     # in this order in a shortcut's name

# What Option+letter types on a Mac (US layout), without and with Shift
_MAC_OPTION = dict(zip("åß∂ƒ©˙∆˚¬µøπœ®†√∑≈¥Ω∫ç", "ASDFGHJKLMOPQRTVWXYZBC"))
_MAC_OPTION_SHIFT = {"Å": "A", "Í": "S", "Î": "D", "Ï": "F", "˝": "G", "Ó": "H", "Ô": "J", "\uf8ff": "K", "Ò": "L",
                     "Â": "M", "Ø": "O", "∏": "P", "Œ": "Q", "‰": "R", "ˇ": "T", "◊": "V", "„": "W", "˛": "X",
                     "Á": "Y", "¸": "Z", "ı": "B", "Ç": "C"}
_MODIFIER_KEYS = re.compile(r"^(Shift|Control|Alt|Meta|Super|Option|Command|Caps|Num|Mode|ISO|Hyper)", re.I)

_current: Dict[str, str] = dict(DEFAULTS)
_handlers: Dict[str, Callable[[], None]] = {}
_system = ""
_installed = False
paused = False         # True while a shortcut is being recorded (the dialog takes the keys)


def system(root=None) -> str:
    """'aqua' (Mac), 'win32' or 'x11'."""
    global _system
    if not _system and root is not None:
        try:
            _system = str(root.tk.call("tk", "windowingsystem"))
        except tk.TclError:
            _system = "x11"
    return _system or "x11"


# ------------------------------------------------------------------ names
def normalize(spec: str) -> str:
    """A shortcut in its standard form ("alt+shift+d" -> "Alt+Shift+D"); "" when it is not a valid one."""
    parts = [p.strip() for p in str(spec or "").replace("-", "+").split("+") if p.strip()]
    if not parts:
        return ""
    aliases = {"control": "Ctrl", "ctrl": "Ctrl", "cmd": "Cmd", "command": "Cmd", "⌘": "Cmd", "alt": "Alt",
               "option": "Alt", "opt": "Alt", "⌥": "Alt", "shift": "Shift", "⇧": "Shift", "⌃": "Ctrl"}
    mods = set()
    for p in parts[:-1]:
        m = aliases.get(p.lower())
        if m is None:
            return ""
        mods.add(m)
    key = parts[-1]
    if re.fullmatch(r"[A-Za-z0-9]", key):
        key = key.upper()
    elif re.fullmatch(r"[Ff]([1-9]|1[0-9]|2[0-4])", key):
        key = key.upper()
    else:
        return ""
    if not (mods - {"Shift"}) and not (len(key) > 1 and key.startswith("F")):
        return ""      # a plain letter (or Shift+letter) would stop typing
    return "+".join([m for m in MODIFIERS if m in mods] + [key])


def label(spec: str, mac: Optional[bool] = None) -> str:
    """How a shortcut is written for the clinician: "Alt+R", or "⌥R" on a Mac."""
    spec = normalize(spec)
    if not spec:
        return ""
    if mac is None:
        mac = system() == "aqua"
    if not mac:
        return spec
    symbols = {"Ctrl": "⌃", "Alt": "⌥", "Shift": "⇧", "Cmd": "⌘"}
    *mods, key = spec.split("+")
    return "".join(symbols[m] for m in ("Ctrl", "Alt", "Shift", "Cmd") if m in mods) + key


def key_of(action: str) -> str:
    return _current.get(action, "")


def hint(text: str, action: str) -> str:
    """``text`` with the shortcut in brackets ("Pause or resume (Alt+P)"), or just ``text`` when it is off."""
    shown = label(_current.get(action, ""))
    return f"{text} ({shown})" if shown else text


def problem(mapping: Dict[str, str]) -> str:
    """Why these shortcuts can't be used together ("" when they can)."""
    seen = {}
    for action in ACTIONS:
        spec = normalize(mapping.get(action, ""))
        if not spec:
            continue
        if spec in seen:
            return f"{label(spec)} is used for both “{NAMES[seen[spec]]}” and “{NAMES[action]}”."
        seen[spec] = action
    return ""


# ------------------------------------------------------------------ keys pressed
def _modifiers(state: int, windowing: str) -> set:
    mods = set()
    if state & 0x1:
        mods.add("Shift")
    if state & 0x4:
        mods.add("Ctrl")
    if windowing == "aqua":
        if state & 0x8:
            mods.add("Cmd")
        if state & 0x10:
            mods.add("Alt")
    elif windowing == "win32":
        if state & 0x20000:
            mods.add("Alt")
    else:
        if state & 0x8:
            mods.add("Alt")
    return mods


def from_event(event, windowing: Optional[str] = None) -> str:
    """The shortcut a key press makes ("" for a lone modifier key or a key that can't be a shortcut)."""
    windowing = windowing or system()
    keysym = str(getattr(event, "keysym", "") or "")
    if not keysym or _MODIFIER_KEYS.match(keysym):
        return ""
    mods = _modifiers(int(getattr(event, "state", 0) or 0), windowing)
    key = ""
    if re.fullmatch(r"[A-Za-z0-9]", keysym) or re.fullmatch(r"F([1-9]|1[0-9]|2[0-4])", keysym):
        key = keysym.upper()
    else:
        char = str(getattr(event, "char", "") or "")
        if windowing == "aqua" and "Alt" in mods and char:
            # Option+letter arrives as the character it types
            key = (_MAC_OPTION_SHIFT if "Shift" in mods else _MAC_OPTION).get(char, "")
        if not key and len(char) == 1 and char.isascii() and char.isalnum():
            key = char.upper()
        if not key and len(char) == 1 and 1 <= ord(char) <= 26 and "Ctrl" in mods:
            key = chr(ord("A") + ord(char) - 1)      # Ctrl+letter as a control character
    if not key:
        return ""
    return normalize("+".join([m for m in MODIFIERS if m in mods] + [key]))


# ------------------------------------------------------------------ the app's bindings
def install(root, handlers: Dict[str, Callable[[], None]], saved: Optional[dict] = None):
    """Listen for the shortcuts in every FreeScribe window. ``handlers``: {action: function}."""
    system(root)
    _handlers.clear()
    _handlers.update(handlers)
    apply(saved)
    global _installed
    if _installed:
        return
    _installed = True
    root.bind_all("<KeyPress>", _on_key, add="+")
    # Tk has its own Alt+key handler for menus on the same level, which would win over the one
    # above for Alt shortcuts: take it over, and hand it every Alt key that is not a shortcut
    for sequence in ("<Alt-KeyPress>", "<Option-KeyPress>"):
        try:
            previous = root.bind_all(sequence)
            root.bind_all(sequence, lambda e, p=previous: _on_alt_key(root, e, p))
        except tk.TclError:
            pass      # "Option" exists only on a Mac


def apply(saved: Optional[dict]):
    """Use ``saved`` ({action: shortcut}; a missing action keeps its default, "" turns it off)."""
    _current.clear()
    for action in ACTIONS:
        if saved and action in saved:
            _current[action] = normalize(saved[action])
        else:
            _current[action] = DEFAULTS[action]


def current() -> Dict[str, str]:
    return dict(_current)


def _on_key(event):
    if paused:
        return None
    spec = from_event(event)
    if not spec:
        return None
    action = next((a for a, s in _current.items() if s and s == spec), None)
    if action is None or action not in _handlers:
        return None
    _take_back_typed_character(event)
    try:
        _handlers[action]()
    except Exception:
        pass
    return "break"


def _on_alt_key(root, event, previous):
    if _on_key(event) == "break":
        return "break"
    if previous and "TraverseToMenu" in previous:
        try:
            root.tk.call("tk::TraverseToMenu", event.widget, event.char)
        except tk.TclError:
            pass
    return None


def _take_back_typed_character(event):
    """Option+letter on a Mac types a character before the shortcut is seen: remove it again."""
    char = str(getattr(event, "char", "") or "")
    widget = event.widget
    if not char or not char.isprintable() or char.isascii():
        return
    try:
        if isinstance(widget, tk.Text):
            if widget.get("insert -1c", "insert") == char:
                widget.delete("insert -1c", "insert")
        elif isinstance(widget, tk.Entry):
            at = widget.index("insert")
            if at and widget.get()[at - 1:at] == char:
                widget.delete(at - 1)
    except (tk.TclError, AttributeError, TypeError):
        pass
