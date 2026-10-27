"""
StartScreen.py

What a new, empty session shows: one large button to start transcribing, a
microphone check, the note type to write, and the other ways to begin
(upload audio, paste a transcript, add context first). It also holds the
recording banner shown while a visit is being transcribed.

This software is released under the AGPL-3.0 license.
"""

import tkinter as tk

from UI.Icons import icon
from UI.Theme import Theme, Palette
from UI import Shortcuts
from UI.Widgets.Controls import Button, LevelMeter, RoundedFrame, Tooltip, rounded_image

# Short descriptions for the built-in templates on the start screen
TEMPLATE_HINTS = {
    "SOAP Note": "Most visits",
    "Progress Note": "Follow-ups",
    "History & Physical": "New patients",
    "Referral Letter": "To a specialist",
    "Patient Summary": "Plain language",
    "No Note Generation": "Transcript only",
}


def short_template_name(name):
    return name.replace(" - Default", "")


class BigRecordButton(tk.Canvas):
    """The large round "start transcribing" button."""

    SIZE = 116

    def __init__(self, parent, command, surface=Palette.BG):
        size = Theme.px(self.SIZE)
        super().__init__(parent, width=size, height=size, bg=surface, highlightthickness=0, bd=0,
                         cursor="hand2", takefocus=1)
        self._command = command
        self._hover = False
        self.enabled = True
        self._ring = self.create_image(size / 2, size / 2)
        self._disc = self.create_image(size / 2, size / 2)
        self._mic = self.create_image(size / 2, size / 2)
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<Button-1>", lambda e: self.invoke())
        self.bind("<Key-Return>", lambda e: self.invoke())
        self.bind("<Key-space>", lambda e: self.invoke())
        Tooltip(self, lambda: Shortcuts.hint("Start transcribing", "record"))
        self._render()

    def invoke(self):
        if self.enabled and callable(self._command):
            self._command()

    def set_enabled(self, enabled):
        self.enabled = bool(enabled)
        self.configure(cursor="hand2" if self.enabled else "arrow")
        self._render()

    def _set_hover(self, hover):
        self._hover = hover and self.enabled
        self._render()

    def _render(self):
        size = Theme.px(self.SIZE)
        inner = size - Theme.px(22)
        fill = (Palette.PRIMARY_HOVER if self._hover else Palette.PRIMARY) if self.enabled else Palette.DISABLED_BG
        self._ring_image = rounded_image(size, size, size // 2,
                                         Palette.PRIMARY_SOFT if self.enabled else Palette.SURFACE)
        self._disc_image = rounded_image(inner, inner, inner // 2, fill)
        self.itemconfigure(self._ring, image=self._ring_image)
        self.itemconfigure(self._disc, image=self._disc_image)
        self.itemconfigure(self._mic, image=icon("mic", 40, Palette.TEXT_ON_PRIMARY if self.enabled
                                                 else Palette.DISABLED_TEXT))


class TemplateCard(tk.Canvas):
    """A note type to pick on the start screen."""

    HEIGHT = 62

    def __init__(self, parent, title, hint, command, selected=False, surface=Palette.BG):
        super().__init__(parent, height=Theme.px(self.HEIGHT), bg=surface, highlightthickness=0, bd=0,
                         cursor="hand2", takefocus=1)
        self.title, self.hint = title, hint
        self.selected = selected
        self._command = command
        self._hover = False
        self._bg = self.create_image(0, 0, anchor="nw")
        self._title = self.create_text(Theme.px(14), Theme.px(22), anchor="w", font=Theme.font("base", "semibold"))
        self._hint = self.create_text(Theme.px(14), Theme.px(42), anchor="w", font=Theme.font("small"))
        self.bind("<Configure>", lambda e: self._render())
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<Button-1>", lambda e: self.invoke())
        self.bind("<Key-Return>", lambda e: self.invoke())
        self._render()

    def invoke(self):
        if callable(self._command):
            self._command()

    def set_selected(self, selected):
        self.selected = bool(selected)
        self._render()

    def _set_hover(self, hover):
        self._hover = hover
        self._render()

    def _render(self):
        width, height = max(self.winfo_width(), 40), Theme.px(self.HEIGHT)
        if self.selected:
            image = rounded_image(width, height, Theme.px(12), Palette.BG, Palette.PRIMARY, Theme.px(2))
        else:
            image = rounded_image(width, height, Theme.px(12), Palette.SURFACE if self._hover else Palette.BG,
                                  Palette.BORDER_STRONG if self._hover else Palette.BORDER)
        self._image = image
        self.itemconfigure(self._bg, image=image)
        font = Theme.font("base", "semibold")
        title = self.title
        while len(title) > 2 and font.measure(title) > width - Theme.px(24):
            title = title.rstrip("…")[:-1].rstrip() + "…"
        self.itemconfigure(self._title, text=title, fill=Palette.TEXT)
        self.itemconfigure(self._hint, text=self.hint,
                           fill=Palette.PRIMARY_PRESSED if self.selected else Palette.TEXT_MUTED)


class StartScreen(tk.Frame):
    """
    Shown on a new, empty session.

    :param actions: callables - ``record``, ``upload``, ``paste`` (type or paste a transcript),
        ``context`` (add context first), ``choose_template(name)``, ``more_templates(anchor)``,
        ``change_mic(anchor)``
    :param templates: callable returning ``(names, current, default, notes_enabled)``
    """

    def __init__(self, parent, actions, templates):
        super().__init__(parent, bg=Palette.BG)
        self.actions = actions
        self.templates = templates
        self.cards = []

        column = tk.Frame(self, bg=Palette.BG)
        column.place(relx=0.5, rely=0.46, anchor="center", relwidth=0.86)
        self.column = column
        self.bind("<Configure>", self._on_resize)

        self.record_button = BigRecordButton(column, command=lambda: self._do("record"))
        self.record_button.pack()
        self.title = tk.Label(column, text="Start transcribing", bg=Palette.BG, fg=Palette.TEXT,
                              font=Theme.font("title", "semibold"))
        self.title.pack(pady=(Theme.px(14), Theme.px(2)))
        self.subtitle = tk.Label(column, text=self._subtitle_text(),
                                 bg=Palette.BG, fg=Palette.TEXT_MUTED, font=Theme.font("base"))
        self.subtitle.pack()

        # Microphone check
        mic = RoundedFrame(column, fill=Palette.SIDEBAR, outline=Palette.SIDEBAR, radius=14, padx=16, pady=12)
        mic.pack(fill="x", pady=(Theme.px(26), 0))
        self.mic_box = mic
        body = mic.body
        self.meter = LevelMeter(body, bars=7, height=24, surface=Palette.SIDEBAR)
        self.meter.pack(side="left")
        self.change_mic = Button(body, text="Change", variant="secondary", size="sm", surface=Palette.SIDEBAR,
                                 command=lambda: self._do("change_mic", self.change_mic))
        self.change_mic.pack(side="right")
        texts = tk.Frame(body, bg=Palette.SIDEBAR)
        texts.pack(side="left", fill="x", expand=True, padx=(Theme.px(14), Theme.px(10)))
        self.mic_state = tk.Label(texts, text="Say something to check the microphone", bg=Palette.SIDEBAR,
                                  fg=Palette.TEXT, anchor="w", font=Theme.font("base", "medium"))
        self.mic_state.pack(fill="x")
        self.mic_name = tk.Label(texts, text="", bg=Palette.SIDEBAR, fg=Palette.TEXT_MUTED, anchor="w",
                                 font=Theme.font("small"))
        self.mic_name.pack(fill="x")

        # Note type
        self.notes_frame = tk.Frame(column, bg=Palette.BG)
        self.notes_frame.pack(fill="x", pady=(Theme.px(22), 0))
        tk.Label(self.notes_frame, text="Note type", bg=Palette.BG, fg=Palette.TEXT_MUTED, anchor="w",
                 font=Theme.font("small", "semibold")).pack(fill="x", pady=(0, Theme.px(8)))
        self.card_row = tk.Frame(self.notes_frame, bg=Palette.BG)
        self.card_row.pack(fill="x")

        # Other ways to begin
        others = tk.Frame(column, bg=Palette.BG)
        others.pack(pady=(Theme.px(22), 0))
        tk.Label(others, text="Or", bg=Palette.BG, fg=Palette.TEXT_MUTED, font=Theme.font("base")).pack(
            side="left", padx=(0, Theme.px(8)))
        self.upload_button = Button(others, text="Upload an audio file", icon_name="upload", variant="neutral",
                                    size="sm", command=lambda: self._do("upload"))
        self.upload_button.pack(side="left", padx=(0, Theme.px(6)))
        self.paste_button = Button(others, text="Paste a transcript", icon_name="lines", variant="neutral",
                                   size="sm", command=lambda: self._do("paste"))
        self.paste_button.pack(side="left", padx=(0, Theme.px(6)))
        self.context_button = Button(others, text="Add context first", icon_name="clipboard", variant="neutral",
                                     size="sm", command=lambda: self._do("context"))
        self.context_button.pack(side="left")
        # Dictate the note yourself (shown once the app has dictation, see show_dictate)
        self.dictate_button = Button(others, text="Dictate a note", icon_name="mic", variant="neutral",
                                     size="sm", command=lambda: self._do("dictate"),
                                     tooltip=lambda: Shortcuts.hint("Type what you say into the note", "dictate"))

        self.refresh_templates()

    def show_dictate(self):
        self.dictate_button.pack(side="left", padx=(Theme.px(6), 0))

    def _do(self, name, *args):
        action = self.actions.get(name)
        if callable(action):
            action(*args)

    @staticmethod
    def _subtitle_text():
        key = Shortcuts.label(Shortcuts.key_of("record"))
        return f"Press {key} any time. The note is written when you stop." if key \
            else "The note is written when you stop."

    def shortcuts_changed(self):
        self.subtitle.configure(text=self._subtitle_text())

    def _on_resize(self, event):
        width = min(Theme.px(680), max(Theme.px(420), event.width - Theme.px(64)))
        self.column.place_configure(relwidth=0, width=width)

    # ------------------------------------------------------------------ content
    def refresh_templates(self):
        names, current, default, notes_enabled = self.templates()
        for card in self.cards:
            card.destroy()
        self.cards = []
        if not notes_enabled and current != "No Note Generation":
            self.notes_frame.pack_forget()
            return
        if not self.notes_frame.winfo_manager():
            self.notes_frame.pack(fill="x", pady=(Theme.px(22), 0), after=self.mic_box)
        # The default first, then the one in use, then the next built-in ones
        shown = []
        for name in [default, current] + [n for n in names if n != "No Note Generation"]:
            if name in names and name not in shown:
                shown.append(name)
        shown = shown[:3]
        for column in range(4):
            self.card_row.grid_columnconfigure(column, weight=1, uniform="cards")
        for column, name in enumerate(shown):
            short = short_template_name(name)
            hint = "Default" if name == default else TEMPLATE_HINTS.get(short, "Your template")
            card = TemplateCard(self.card_row, short, hint, command=lambda n=name: self._do("choose_template", n),
                                selected=name == current)
            card.grid(row=0, column=column, sticky="ew", padx=(0, Theme.px(8)))
            self.cards.append(card)
        more = TemplateCard(self.card_row, "More…", "All templates", command=None)
        more._command = lambda m=more: self._do("more_templates", m)
        more.grid(row=0, column=len(shown), sticky="ew")
        self.cards.append(more)

    def set_level(self, level, mic_name="", ok=True):
        self.meter.set_color(Palette.SUCCESS if level > 0.08 else Palette.TEXT_FAINT)
        self.meter.set_level(level)
        if not ok:
            self.mic_state.configure(text="The microphone is not working", fg=Palette.DANGER)
        elif level > 0.08:
            self.mic_state.configure(text="The microphone is hearing you", fg=Palette.TEXT)
        else:
            self.mic_state.configure(text="Say something to check the microphone", fg=Palette.TEXT)
        self.mic_name.configure(text=mic_name)

    def set_enabled(self, enabled):
        self.record_button.set_enabled(enabled)
        for button in (self.upload_button, self.paste_button, self.context_button, self.dictate_button):
            button.set_enabled(enabled)


class RecordingBanner(tk.Frame):
    """The bar shown while a visit is transcribed: timer, sound level, Pause and Stop."""

    def __init__(self, parent, on_pause, on_stop):
        super().__init__(parent, bg=Palette.BG)
        self.box = RoundedFrame(self, fill=Palette.DANGER_SOFT, outline=Palette.DANGER_SOFT, radius=16,
                                padx=20, pady=14)
        self.box.pack(fill="x")
        body = self.box.body
        bg = Palette.DANGER_SOFT
        self.dot = tk.Canvas(body, width=Theme.px(16), height=Theme.px(16), bg=bg, highlightthickness=0, bd=0)
        self.dot.pack(side="left", padx=(0, Theme.px(12)))
        self._dot_item = self.dot.create_image(Theme.px(8), Theme.px(8))
        labels = tk.Frame(body, bg=bg)
        labels.pack(side="left")
        self.state_label = tk.Label(labels, text="RECORDING", bg=bg, fg=Palette.DANGER_PRESSED, anchor="w",
                                    font=Theme.font("micro", "semibold"))
        self.state_label.pack(fill="x")
        self.time_label = tk.Label(labels, text="00:00", bg=bg, fg=Palette.TEXT, anchor="w",
                                   font=Theme.font("display", "semibold"))
        self.time_label.pack(fill="x")
        self.stop_button = Button(body, text="Stop and write note", icon_name="stop", variant="danger", size="lg",
                                  surface=bg, command=on_stop, tooltip=lambda: Shortcuts.hint("Stop transcribing", "record"))
        self.stop_button.pack(side="right")
        self.pause_button = Button(body, text="Pause", icon_name="pause", variant="secondary", size="lg",
                                   surface=bg, command=on_pause, tooltip=lambda: Shortcuts.hint("Pause or resume", "pause"))
        self.pause_button.pack(side="right", padx=(0, Theme.px(8)))
        self.meter = LevelMeter(body, bars=22, height=40, surface=bg)
        self.meter.pack(side="left", padx=Theme.px(24))
        self.set_state(paused=False)

    def set_state(self, paused, writes_note=True):
        color = Palette.WARNING if paused else Palette.DANGER
        self._dot_image = rounded_image(Theme.px(14), Theme.px(14), Theme.px(7), color)
        self.dot.itemconfigure(self._dot_item, image=self._dot_image)
        self.state_label.configure(text="PAUSED" if paused else "RECORDING",
                                   fg=Palette.WARNING if paused else Palette.DANGER_PRESSED)
        self.pause_button.set(text="Resume" if paused else "Pause", icon_name="play" if paused else "pause")
        self.stop_button.set(text="Stop and write note" if writes_note else "Stop transcribing")

    def set_time(self, text):
        self.time_label.configure(text=text)

    def set_level(self, level, paused=False):
        self.meter.set_color(Palette.WARNING if paused else Palette.DANGER)
        self.meter.set_level(0.0 if paused else level)
