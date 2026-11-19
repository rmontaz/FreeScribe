"""
TranslateWindow.py

Sidebar > Translate. Two people talk, each in their own language. Each side of the window
is one person's language: what they said, and what the other person said translated for them.
The text is large and can be made larger (or the window made full screen) so the person next
to you can read it.

Speaking is started with one of three buttons: one for each language, or "Auto" to let the
speech model hear which of the two languages was spoken. Speech recognition and translation
both run on this computer.

This software is released under the AGPL-3.0 license.
"""

import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

import UI.Helpers
from services import translate as tr
from UI.Theme import Theme, Palette
from UI.Widgets.Controls import Button, LevelMeter, Scrollbar

MIN_FONT, MAX_FONT, START_FONT = 14, 96, 30


class TranslateWindow:
    """
    :param parent: the main window
    :param backend: ``prepare(done)`` (``done("")`` when speech and translation are ready, otherwise the reason),
        ``auto_available()``, ``new_dictation(plan, on_utterance)`` (a plain LiveDictation; ``plan()`` gives
        ``(mode, left, right)`` where mode is "auto", "left" or "right"; ``on_utterance(text, language)``)
        and ``translate(text, source, target, done)``
    :param run_on_ui: runs a function on the window's thread (called from worker threads)
    :param ui_state: remembers the languages and the text size
    """

    def __init__(self, parent, backend, run_on_ui: Callable, ui_state=None):
        self.backend, self.run_on_ui, self.ui_state = backend, run_on_ui, ui_state
        left = self._saved("translate_left", tr.DEFAULT_LEFT)
        right = self._saved("translate_right", tr.DEFAULT_RIGHT)
        self.conversation = tr.Conversation(self._translate, on_change=self._on_change, left=left, right=right)
        self.font_size = int(self._saved("translate_font", START_FONT))
        self.mode = ""                   # "", "auto", "left" or "right": what is being listened for
        self.dictation = None
        self.preparing = False
        self.fullscreen = False

        self.window = tk.Toplevel(parent)
        self.window.title("Translate")
        self.window.configure(bg=Palette.BG)
        UI.Helpers.set_window_icon(self.window)
        self.window.minsize(Theme.px(640), Theme.px(420))
        self.window.geometry(f"{Theme.px(1100)}x{Theme.px(640)}")
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.bind("<Escape>", lambda e: self.set_fullscreen(False))
        self.window.bind("<F11>", lambda e: self.set_fullscreen(not self.fullscreen))
        self._build()
        self._refresh_buttons()
        self._tick()

    # ---------------------------------------------------------------- state saved between uses
    def _saved(self, key, default):
        if self.ui_state is None:
            return default
        value = self.ui_state.get(key, default)
        return value if value not in (None, "") else default

    def _save(self, key, value):
        if self.ui_state is not None:
            self.ui_state.set(key, value)

    # ---------------------------------------------------------------- layout
    def _build(self):
        w = self.window
        top = tk.Frame(w, bg=Palette.BG)
        top.pack(fill="x", padx=Theme.px(14), pady=(Theme.px(12), Theme.px(8)))
        names = tr.language_names()
        self.left_box = ttk.Combobox(top, values=names, state="readonly", width=16, font=Theme.font("medium"))
        self.right_box = ttk.Combobox(top, values=names, state="readonly", width=16, font=Theme.font("medium"))
        self.left_box.set(tr.language_name(self.conversation.left))
        self.right_box.set(tr.language_name(self.conversation.right))
        self.left_box.pack(side="left")
        self.swap_button = Button(top, text="⇄", variant="ghost-strong", size="md", surface=Palette.BG,
                                  command=self.swap, tooltip="Swap the two languages")
        self.swap_button.pack(side="left", padx=Theme.px(8))
        self.right_box.pack(side="left")
        self.left_box.bind("<<ComboboxSelected>>", lambda e: self._languages_changed())
        self.right_box.bind("<<ComboboxSelected>>", lambda e: self._languages_changed())

        self.full_button = Button(top, text="Full screen", variant="neutral", size="md", surface=Palette.BG,
                                  command=lambda: self.set_fullscreen(not self.fullscreen),
                                  tooltip="Fill the screen (F11). Press Esc to leave.")
        self.full_button.pack(side="right")
        self.clear_button = Button(top, text="Clear", variant="ghost-strong", size="md", surface=Palette.BG,
                                   command=self.clear, tooltip="Remove everything said so far")
        self.clear_button.pack(side="right", padx=(0, Theme.px(8)))
        self.bigger_button = Button(top, text="A+", variant="neutral", size="md", surface=Palette.BG,
                                    command=lambda: self.set_font(self.font_size + 4), tooltip="Larger text")
        self.smaller_button = Button(top, text="A−", variant="neutral", size="md", surface=Palette.BG,
                                     command=lambda: self.set_font(self.font_size - 4), tooltip="Smaller text")
        self.bigger_button.pack(side="right", padx=(0, Theme.px(8)))
        self.smaller_button.pack(side="right", padx=(0, Theme.px(4)))

        panes = tk.Frame(w, bg=Palette.BG)
        panes.pack(fill="both", expand=True, padx=Theme.px(14))
        panes.columnconfigure(0, weight=1, uniform="pane")
        panes.columnconfigure(1, weight=1, uniform="pane")
        panes.rowconfigure(0, weight=1)
        self.panes = {}
        for column, side in enumerate(("left", "right")):
            frame = tk.Frame(panes, bg=Palette.BORDER_STRONG, padx=1, pady=1)
            frame.grid(row=0, column=column, sticky="nsew", padx=(0, Theme.px(8)) if column == 0 else (0, 0))
            inner = tk.Frame(frame, bg=Palette.BG if side == "left" else Palette.RAISED)
            inner.pack(fill="both", expand=True)
            text = tk.Text(inner, wrap="word", relief="flat", bd=0, padx=Theme.px(16), pady=Theme.px(12),
                           bg=inner["bg"], fg=Palette.TEXT, highlightthickness=0, state="disabled",
                           cursor="arrow", spacing3=Theme.px(10), takefocus=0)
            bar = Scrollbar(inner, command=text.yview, surface=inner["bg"])
            text.configure(yscrollcommand=bar.set)
            bar.pack(side="right", fill="y", padx=(0, Theme.px(4)), pady=Theme.px(6))
            text.pack(side="left", fill="both", expand=True)
            text.tag_configure("said", foreground=Palette.TEXT)
            text.tag_configure("translated", foreground=Palette.PRIMARY)
            text.tag_configure("pending", foreground=Palette.TEXT_FAINT)
            text.tag_configure("failed", foreground=Palette.DANGER)
            self.panes[side] = text
        self._apply_font()

        bottom = tk.Frame(w, bg=Palette.BG)
        bottom.pack(fill="x", padx=Theme.px(14), pady=Theme.px(12))
        self.left_mic = Button(bottom, text="", variant="primary", size="lg", icon_name="mic", surface=Palette.BG,
                               command=lambda: self.toggle("left"))
        self.auto_mic = Button(bottom, text="Auto", variant="neutral", size="lg", surface=Palette.BG,
                               command=lambda: self.toggle("auto"),
                               tooltip="Listen for either language and put each thing said on the right side")
        self.right_mic = Button(bottom, text="", variant="primary", size="lg", icon_name="mic", surface=Palette.BG,
                                command=lambda: self.toggle("right"))
        self.left_mic.pack(side="left")
        self.auto_mic.pack(side="left", padx=Theme.px(8))
        self.right_mic.pack(side="left")
        self.meter = LevelMeter(bottom, surface=Palette.BG)
        self.meter.pack(side="left", padx=(Theme.px(14), 0))
        self.status = tk.Label(bottom, text="", bg=Palette.BG, fg=Palette.TEXT_MUTED, anchor="w", justify="left",
                               font=Theme.font("base"), wraplength=Theme.px(560))
        self.status.pack(side="left", fill="x", expand=True, padx=Theme.px(12))

    # ---------------------------------------------------------------- the two languages
    def _languages_changed(self):
        left = tr.language_code(self.left_box.get()) or self.conversation.left
        right = tr.language_code(self.right_box.get()) or self.conversation.right
        self.conversation.set_languages(left, right)
        self._save("translate_left", left)
        self._save("translate_right", right)
        if self.mode:
            self.stop()
        self._refresh_buttons()
        self.redraw()

    def swap(self):
        self.conversation.swap()
        self.left_box.set(tr.language_name(self.conversation.left))
        self.right_box.set(tr.language_name(self.conversation.right))
        self._languages_changed()

    def _refresh_buttons(self):
        c = self.conversation
        names = {"left": tr.language_name(c.left), "right": tr.language_name(c.right), "auto": "Auto"}
        auto = bool(self.backend.auto_available())
        for button, mode in ((self.left_mic, "left"), (self.right_mic, "right"), (self.auto_mic, "auto")):
            active = self.mode == mode
            label = "Stop" if active else (f"Speak {names[mode]}" if mode != "auto" else "Auto")
            button.set(text=label, variant="danger" if active else ("neutral" if mode == "auto" else "primary"))
            usable = not self.preparing and self.mode in ("", mode) and (mode != "auto" or auto)
            button.set_enabled(usable)

    # ---------------------------------------------------------------- text size, full screen
    def set_font(self, size):
        self.font_size = max(MIN_FONT, min(MAX_FONT, int(size)))
        self._save("translate_font", self.font_size)
        self._apply_font()

    def _apply_font(self):
        for text in self.panes.values():
            text.configure(font=("TkDefaultFont", self.font_size))
            text.tag_configure("said", font=("TkDefaultFont", self.font_size))
            text.tag_configure("translated", font=("TkDefaultFont", self.font_size, "bold"))
            text.tag_configure("pending", font=("TkDefaultFont", self.font_size, "italic"))
            text.tag_configure("failed", font=("TkDefaultFont", max(MIN_FONT, self.font_size - 8)))

    def set_fullscreen(self, on):
        on = bool(on)
        if on == self.fullscreen:
            return
        self.fullscreen = on
        try:
            self.window.attributes("-fullscreen", on)
        except tk.TclError:
            self.window.state("zoomed" if on else "normal")
        self.full_button.set_text("Leave full screen" if on else "Full screen")

    # ---------------------------------------------------------------- listening
    def toggle(self, mode):
        if self.mode == mode:
            self.stop()
            return
        if self.mode or self.preparing:
            return
        self.preparing = True
        self._refresh_buttons()
        self._say("Getting ready…")
        self.backend.prepare(lambda problem: self.run_on_ui(lambda: self._prepared(mode, problem)))

    def _prepared(self, mode, problem):
        self.preparing = False
        if problem:
            self._say(problem, warn=True)
            self._refresh_buttons()
            return
        try:
            self.dictation = self.backend.new_dictation(
                lambda: (self.mode, self.conversation.left, self.conversation.right),
                lambda text, language: self.run_on_ui(lambda: self._utterance(text, language)))
            self.mode = mode
            self.dictation.start()
        except Exception as e:
            self.mode, self.dictation = "", None
            self._say(str(e) or "The microphone could not be used.", warn=True)
            self._refresh_buttons()
            return
        self._say({"left": f"Listening for {tr.language_name(self.conversation.left)}…",
                   "right": f"Listening for {tr.language_name(self.conversation.right)}…",
                   "auto": "Listening for either language…"}[mode])
        self._refresh_buttons()

    def stop(self):
        dictation, self.dictation, self.mode = self.dictation, None, ""
        if dictation is not None:
            dictation.stop()
        self.meter.set_level(0)
        self._say("")
        self._refresh_buttons()

    def _utterance(self, text, language):
        """Something was heard: which side spoke it (the language it was heard in), then translate it."""
        c = self.conversation
        side = tr.speaker_for(language, c.left, c.right) or ("right" if self.mode == "right" else "left")
        c.said(text, side)

    def _tick(self):
        try:
            if self.dictation is not None:
                self.meter.set_level(self.dictation.level)
                if self.dictation.error:
                    self._say(self.dictation.error, warn=True)
                if not self.dictation.recording and self.mode:
                    self.stop()
            self._after = self.window.after(150, self._tick)
        except tk.TclError:
            pass

    # ---------------------------------------------------------------- translating and drawing
    def _translate(self, text, source, target, done):
        self.backend.translate(text, source, target, done)

    def _on_change(self, exchange):
        self.run_on_ui(self.redraw)

    def clear(self):
        self.conversation.clear()
        self.redraw()

    def redraw(self):
        try:
            for side, text in self.panes.items():
                text.configure(state="normal")
                text.delete("1.0", "end")
                for e in list(self.conversation.exchanges):
                    if e.speaker == side:
                        text.insert("end", e.original + "\n", "said")
                    elif e.state == "done":
                        text.insert("end", e.translation + "\n", "translated")
                    elif e.state == "failed":
                        text.insert("end", f"(not translated: {e.problem})\n", "failed")
                    else:
                        text.insert("end", "…\n", "pending")
                text.configure(state="disabled")
                text.see("end")
        except tk.TclError:
            pass

    def _say(self, message, warn=False):
        self.status.configure(text=message, fg=Palette.DANGER if warn else Palette.TEXT_MUTED)

    # ---------------------------------------------------------------- closing
    def close(self):
        self.stop()
        try:
            self.window.after_cancel(self._after)
        except Exception:
            pass
        self.window.destroy()

    @classmethod
    def show(cls, parent, backend, run_on_ui, ui_state=None):
        """One window: opening Translate again brings the open one forward."""
        existing = getattr(cls, "_open", None)
        if existing is not None:
            try:
                if existing.window.winfo_exists():
                    existing.window.deiconify()
                    existing.window.lift()
                    return existing
            except tk.TclError:
                pass
        cls._open = cls(parent, backend, run_on_ui, ui_state)
        return cls._open
