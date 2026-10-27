"""
ShortcutsWindow.py

Help > Keyboard shortcuts: change or turn off the app's shortcuts (UI/Shortcuts.py).
Press "Change", then the keys you want.

This software is released under the AGPL-3.0 license.
"""

import tkinter as tk
from typing import Callable, Dict

import UI.Helpers
from UI import Shortcuts
from UI.Theme import Theme, Palette
from UI.Widgets.Controls import Button

# Keys the system or every text box already uses: never taken by FreeScribe
RESERVED = {"Ctrl+C", "Ctrl+V", "Ctrl+X", "Ctrl+Z", "Ctrl+Y", "Ctrl+A", "Ctrl+S", "Ctrl+Shift+Z", "Alt+F4",
            "Cmd+C", "Cmd+V", "Cmd+X", "Cmd+Z", "Cmd+A", "Cmd+Q", "Cmd+W", "Cmd+H", "Cmd+M", "Cmd+S",
            "Cmd+Shift+Z", "Cmd+Shift+3", "Cmd+Shift+4", "Cmd+Shift+5"}


class ShortcutsWindow:
    """
    :param parent: the main window
    :param on_save: called with {action: shortcut} ("" for off) when the clinician saves
    """

    def __init__(self, parent, on_save: Callable[[Dict[str, str]], None]):
        self.on_save = on_save
        self.values = Shortcuts.current()
        self.listening = None          # the action waiting for its new keys
        self.rows = {}

        self.window = tk.Toplevel(parent)
        self.window.title("Keyboard shortcuts")
        self.window.configure(bg=Palette.BG)
        UI.Helpers.set_window_icon(self.window)
        self.window.transient(parent)
        self.window.resizable(False, False)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.bind("<KeyPress>", self._on_key)
        self._build()
        self._refresh()
        parent.update_idletasks()
        w, h = self.window.winfo_reqwidth(), self.window.winfo_reqheight()
        x = parent.winfo_rootx() + max(0, (parent.winfo_width() - w) // 2)
        y = parent.winfo_rooty() + max(0, (parent.winfo_height() - h) // 3)
        self.window.geometry(f"+{x}+{y}")
        self.window.focus_set()

    def _build(self):
        pad = Theme.px(22)
        body = tk.Frame(self.window, bg=Palette.BG)
        body.pack(fill="both", expand=True, padx=pad, pady=(Theme.px(18), Theme.px(8)))
        tk.Label(body, text="Keyboard shortcuts", bg=Palette.BG, fg=Palette.TEXT, font=Theme.font("large", "semibold"),
                 anchor="w").grid(row=0, column=0, columnspan=4, sticky="w")
        tk.Label(body, text="They work while a FreeScribe window is in front. If one clashes with a shortcut of your "
                            "computer or another program, change it or turn it off. The Chrome extension's shortcuts "
                            "are changed in Chrome (the panel's … menu > Change keyboard shortcuts).",
                 bg=Palette.BG, fg=Palette.TEXT_MUTED, font=Theme.font("small"), justify="left", anchor="w",
                 wraplength=Theme.px(470)).grid(row=1, column=0, columnspan=4, sticky="w", pady=(Theme.px(4), Theme.px(12)))
        for n, action in enumerate(Shortcuts.ACTIONS, start=2):
            tk.Label(body, text=Shortcuts.NAMES[action], bg=Palette.BG, fg=Palette.TEXT, font=Theme.font("base"),
                     anchor="w").grid(row=n, column=0, sticky="w", pady=Theme.px(5))
            keys = tk.Label(body, bg=Palette.SURFACE, fg=Palette.TEXT, font=Theme.font("base", "semibold"),
                            width=16, padx=Theme.px(8), pady=Theme.px(3))
            keys.grid(row=n, column=1, padx=(Theme.px(16), Theme.px(8)))
            change = Button(body, text="Change", variant="neutral", size="sm",
                            command=lambda a=action: self._listen(a))
            change.grid(row=n, column=2, padx=(0, Theme.px(6)))
            off = Button(body, text="Turn off", variant="ghost", size="sm", command=lambda a=action: self._set(a, ""))
            off.grid(row=n, column=3)
            self.rows[action] = (keys, change, off)
        self.message = tk.Label(body, bg=Palette.BG, fg=Palette.DANGER, font=Theme.font("small"), justify="left",
                                anchor="w", wraplength=Theme.px(470))
        self.message.grid(row=10, column=0, columnspan=4, sticky="w", pady=(Theme.px(8), 0))

        footer = tk.Frame(self.window, bg=Palette.BG)
        footer.pack(fill="x", padx=pad, pady=(Theme.px(4), Theme.px(16)))
        Button(footer, text="Reset to defaults", variant="ghost", command=self._reset).pack(side="left")
        self.save_button = Button(footer, text="Save", variant="primary", command=self.save)
        self.save_button.pack(side="right")
        Button(footer, text="Cancel", variant="neutral", command=self.close).pack(side="right", padx=(0, Theme.px(8)))

    def _refresh(self):
        for action, (keys, change, _off) in self.rows.items():
            if self.listening == action:
                keys.configure(text="Press the keys…", fg=Palette.PRIMARY)
                change.set(text="Cancel")
            else:
                keys.configure(text=Shortcuts.label(self.values.get(action, "")) or "Off",
                               fg=Palette.TEXT if self.values.get(action) else Palette.TEXT_MUTED)
                change.set(text="Change")
        clash = Shortcuts.problem(self.values)
        if clash:
            self.message.configure(text=clash, fg=Palette.DANGER)
        self.save_button.set_enabled(not clash and self.listening is None)

    def _say(self, text, error=True):
        self.message.configure(text=text, fg=Palette.DANGER if error else Palette.TEXT_MUTED)

    def _listen(self, action):
        if self.listening == action:
            self._stop_listening()
            return
        self.listening = action
        Shortcuts.paused = True
        mac = Shortcuts.system() == "aqua"
        self._say(f"Press the keys for “{Shortcuts.NAMES[action]}”: {'⌘, ⌃ or ⌥' if mac else 'Ctrl or Alt'} with a "
                  "letter or number (Shift is allowed), or a function key such as F8. Esc cancels.", error=False)
        self.window.focus_set()
        self._refresh()

    def _stop_listening(self):
        self.listening = None
        Shortcuts.paused = False
        self._refresh()

    def _on_key(self, event):
        if self.listening is None:
            if event.keysym == "Escape":
                self.close()
            return None
        if event.keysym == "Escape":
            self._say("")
            self._stop_listening()
            return "break"
        spec = Shortcuts.from_event(event)
        if not spec:
            if event.keysym and not Shortcuts._MODIFIER_KEYS.match(event.keysym):
                self._say("That can't be a shortcut: it would stop you typing. Hold Ctrl, Alt or Cmd with a letter, "
                          "or use a function key.")
            return "break"
        if spec in RESERVED:
            self._say(f"{Shortcuts.label(spec)} is already used by your computer or for editing text. Pick another.")
            return "break"
        self._set(self.listening, spec)
        return "break"

    def _set(self, action, spec):
        self.values[action] = spec
        self.listening = None
        Shortcuts.paused = False
        self._say("" if spec else f"“{Shortcuts.NAMES[action]}” has no shortcut now.", error=False)
        self._refresh()

    def _reset(self):
        self.values = dict(Shortcuts.DEFAULTS)
        self.listening = None
        Shortcuts.paused = False
        self._say("Back to the defaults.", error=False)
        self._refresh()

    def save(self):
        if Shortcuts.problem(self.values):
            return
        self.on_save(dict(self.values))
        self.close()

    def close(self):
        Shortcuts.paused = False
        if self.window.winfo_exists():
            self.window.destroy()
