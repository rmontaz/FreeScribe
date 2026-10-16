"""
ExtensionWindow.py

The "Chrome extension" window: turns the browser extension's connection on or
off, shows the pairing code to paste into the extension, and explains the
one-time setup. The connection itself is in services/extension_server.py.

This software is released under the AGPL-3.0 license.
"""

import tkinter as tk

from UI.Theme import Theme, Palette
from UI.Widgets.Controls import Button

STEPS = (
    ("Add the extension to Chrome",
     "Click “Show extension folder” below. In Chrome, open chrome://extensions, turn on "
     "“Developer mode” (top right), click “Load unpacked” and choose that folder."),
    ("Pin it",
     "Click the puzzle icon in Chrome’s toolbar and pin FreeScribe, so its button is always there."),
    ("Pair it",
     "Click the FreeScribe button in Chrome and paste the code above. You only do this once."),
    ("Turn on the floating panel",
     "Open OSCAR, click the FreeScribe button and switch on “Floating panel on this site”. The panel "
     "then also appears in OSCAR’s pop-up chart windows, which have no toolbar."),
)
USE = ("During a visit, use the floating panel (or Alt+Shift+R) to start, pause and stop. Drag it "
       "anywhere, stick it to a side, or shrink it to a small button. When the note is ready, click "
       "in the OSCAR note box and press “Insert into chart” (or Alt+Shift+N).")


class ExtensionWindow:
    """
    :param root: the main window
    :param state: function returning {"enabled", "code", "port", "running", "error"}
    :param on_enable: called with True/False when the switch changes
    :param on_new_code: called to make a new pairing code (the old one stops working)
    :param on_open_folder: called to show the extension folder
    """

    _open = None

    @classmethod
    def show(cls, root, **kwargs):
        if cls._open is not None and cls._open.window.winfo_exists():
            cls._open.window.deiconify()
            cls._open.window.lift()
            cls._open.refresh()
            return cls._open
        cls._open = cls(root, **kwargs)
        return cls._open

    def __init__(self, root, state, on_enable, on_new_code, on_open_folder):
        self.state = state
        self.on_enable, self.on_new_code, self.on_open_folder = on_enable, on_new_code, on_open_folder
        bg = Palette.BG
        self.window = tk.Toplevel(root, bg=bg)
        self.window.title("Chrome extension")
        self.window.resizable(False, False)
        self.window.transient(root)
        pad = Theme.px(24)
        body = tk.Frame(self.window, bg=bg)
        body.pack(fill="both", expand=True, padx=pad, pady=pad)
        wrap = Theme.px(460)

        tk.Label(body, text="Chrome extension", bg=bg, fg=Palette.TEXT, anchor="w",
                 font=Theme.font("large", "semibold")).pack(fill="x")
        tk.Label(body, bg=bg, fg=Palette.TEXT_MUTED, anchor="w", justify="left", wraplength=wrap,
                 font=Theme.font("base"),
                 text="Control FreeScribe from Chrome while you work in OSCAR, and put the finished note "
                      "into the patient’s chart with one click. The extension talks only to FreeScribe "
                      "on this computer.").pack(fill="x", pady=(Theme.px(4), Theme.px(14)))

        self.enabled = tk.BooleanVar(value=False)
        tk.Checkbutton(body, text="Let the FreeScribe Chrome extension control FreeScribe", variable=self.enabled,
                       command=self._toggle, bg=bg, fg=Palette.TEXT, activebackground=bg, anchor="w",
                       font=Theme.font("base", "medium"), selectcolor=bg,
                       highlightthickness=0).pack(fill="x")

        self.code_box = tk.Frame(body, bg=Palette.SURFACE, highlightthickness=1,
                                 highlightbackground=Palette.BORDER)
        self.code_box.pack(fill="x", pady=(Theme.px(12), Theme.px(4)))
        inner = tk.Frame(self.code_box, bg=Palette.SURFACE)
        inner.pack(fill="x", padx=Theme.px(14), pady=Theme.px(12))
        tk.Label(inner, text="Pairing code", bg=Palette.SURFACE, fg=Palette.TEXT_MUTED, anchor="w",
                 font=Theme.font("small")).pack(fill="x")
        row = tk.Frame(inner, bg=Palette.SURFACE)
        row.pack(fill="x", pady=(Theme.px(2), 0))
        import tkinter.font as tkfont
        mono = tkfont.nametofont("TkFixedFont").actual()["family"]
        self.code_label = tk.Label(row, bg=Palette.SURFACE, fg=Palette.TEXT, anchor="w",
                                   font=(mono, Theme.font_size("title"), "bold"))
        self.code_label.pack(side="left")
        Button(row, text="New code", icon_name="refresh", variant="ghost", size="sm", surface=Palette.SURFACE,
               command=self._new_code, tooltip="Make a new code (the extension must be paired again)").pack(side="right")
        self.copy_button = Button(row, text="Copy", icon_name="copy", variant="primary", size="sm",
                                  surface=Palette.SURFACE, command=self._copy)
        self.copy_button.pack(side="right", padx=(0, Theme.px(6)))
        self.status = tk.Label(body, bg=bg, fg=Palette.TEXT_MUTED, anchor="w", justify="left", wraplength=wrap,
                               font=Theme.font("small"))
        self.status.pack(fill="x")

        tk.Label(body, text="One-time setup", bg=bg, fg=Palette.TEXT, anchor="w",
                 font=Theme.font("base", "semibold")).pack(fill="x", pady=(Theme.px(16), Theme.px(4)))
        for number, (title, text) in enumerate(STEPS, 1):
            step = tk.Frame(body, bg=bg)
            step.pack(fill="x", pady=(Theme.px(4), 0))
            tk.Label(step, text=str(number), bg=Palette.PRIMARY_SOFT, fg=Palette.PRIMARY, width=2,
                     font=Theme.font("small", "semibold")).pack(side="left", anchor="n", padx=(0, Theme.px(10)))
            words = tk.Frame(step, bg=bg)
            words.pack(side="left", fill="x", expand=True)
            tk.Label(words, text=title, bg=bg, fg=Palette.TEXT, anchor="w",
                     font=Theme.font("base", "medium")).pack(fill="x")
            tk.Label(words, text=text, bg=bg, fg=Palette.TEXT_MUTED, anchor="w", justify="left",
                     wraplength=wrap - Theme.px(34), font=Theme.font("small")).pack(fill="x")
        tk.Label(body, text=USE, bg=bg, fg=Palette.TEXT_MUTED, anchor="w", justify="left", wraplength=wrap,
                 font=Theme.font("small")).pack(fill="x", pady=(Theme.px(14), 0))

        buttons = tk.Frame(body, bg=bg)
        buttons.pack(fill="x", pady=(Theme.px(18), 0))
        Button(buttons, text="Close", variant="neutral", command=self.window.destroy).pack(side="right")
        Button(buttons, text="Show extension folder", icon_name="folder", variant="neutral",
               command=self.on_open_folder).pack(side="left")
        self.window.bind("<Escape>", lambda e: self.window.destroy())
        self.refresh()

    # ------------------------------------------------------------------ actions
    def _toggle(self):
        self.on_enable(bool(self.enabled.get()))
        self.refresh()

    def _new_code(self):
        self.on_new_code()
        self.refresh()

    def _copy(self):
        code = self.state().get("code") or ""
        if not code:
            return
        self.window.clipboard_clear()
        self.window.clipboard_append(code)
        self.copy_button.set(text="Copied")
        self.window.after(1500, lambda: self.window.winfo_exists() and self.copy_button.set(text="Copy"))

    def refresh(self):
        info = self.state()
        self.enabled.set(bool(info.get("enabled")))
        self.code_label.configure(text=info.get("code") or "—")
        if not info.get("enabled"):
            text, colour = "Off. Turn it on to use the extension.", Palette.TEXT_MUTED
        elif info.get("error"):
            text, colour = info["error"], Palette.DANGER
        elif info.get("running"):
            text, colour = f"On. Waiting for the extension on this computer (port {info.get('port')}).", Palette.SUCCESS
        else:
            text, colour = "Starting…", Palette.TEXT_MUTED
        self.status.configure(text=text, fg=colour)
