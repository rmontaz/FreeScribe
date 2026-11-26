"""
WelcomeWindow.py

The first window a clinician sees: a short, illustrated welcome with the four things FreeScribe does,
a link to the quick tour and the interface guide, and the "don't show this again" choice.

This software is released under the AGPL-3.0 license.
"""

import tkinter as tk
import webbrowser

import UI.Helpers
from UI.Art import Artwork
from UI.ImageWindow import ImageWindow
from UI.Icons import icon
from UI.QuickTour import show_tour
from UI.Theme import Theme, Palette
from UI.Widgets.Controls import Button, RoundedFrame
from utils.file_utils import get_file_path
from utils.utils import get_application_version

DISCORD_URL = "https://discord.gg/3JamHuHxvD"

FEATURES = (
    ("mic", "Record the visit", "Start transcribing, upload audio or paste a transcript."),
    ("doc", "A note in your format", "SOAP, H&P, progress notes, letters and your own templates."),
    ("sparkle", "Ask for edits", "Describe a change in plain words. Undo it if you change your mind."),
    ("download", "Take it with you", "Copy the note, or save it as a Word, PDF or text file."),
)


class WelcomeWindow:
    """:param callback: called with True when "Don't show this again" is ticked as the window closes"""

    def __init__(self, parent, callback=None):
        self.parent = parent
        self.callback = callback
        self.window = tk.Toplevel(parent, bg=Palette.BG)
        self.window.title("Welcome to FreeScribe")
        UI.Helpers.set_window_icon(self.window)
        self.window.minsize(Theme.px(640), Theme.px(600))
        width, height = Theme.px(760), Theme.px(700)
        x = parent.winfo_rootx() + max(0, (parent.winfo_width() - width) // 2)
        y = parent.winfo_rooty() + max(0, (parent.winfo_height() - height) // 3)
        self.window.geometry(f"{width}x{height}+{x}+{y}")
        self.window.transient(parent)
        self.dont_show = tk.BooleanVar(value=False)
        self._build()
        self.window.protocol("WM_DELETE_WINDOW", self._close)
        self.window.bind("<Escape>", lambda e: self._close())
        self.window.focus_set()

    def _build(self):
        w = self.window
        # footer first, so it is never pushed off the bottom
        footer = tk.Frame(w, bg=Palette.SIDEBAR)
        footer.pack(side="bottom", fill="x")
        tk.Frame(footer, bg=Palette.BORDER, height=1).pack(fill="x")
        row = tk.Frame(footer, bg=Palette.SIDEBAR)
        row.pack(fill="x", padx=Theme.px(28), pady=Theme.px(14))
        tk.Checkbutton(row, text="Don't show this again", variable=self.dont_show, bg=Palette.SIDEBAR,
                       fg=Palette.TEXT_MUTED, activebackground=Palette.SIDEBAR, selectcolor=Palette.BG,
                       font=Theme.font("small"), highlightthickness=0, bd=0, cursor="hand2", padx=Theme.px(4)).pack(side="left")
        tk.Label(row, text=f"Version {get_application_version()}", bg=Palette.SIDEBAR, fg=Palette.TEXT_FAINT,
                 font=Theme.font("micro")).pack(side="right")

        Artwork(w, "hero-start", height=210, themed=True, cover=True, fade=True).pack(fill="x")

        body = tk.Frame(w, bg=Palette.BG)
        body.pack(fill="both", expand=True, padx=Theme.px(36))
        tk.Label(body, text="Welcome to FreeScribe", bg=Palette.BG, fg=Palette.TEXT,
                 font=Theme.font("display", "semibold")).pack(anchor="w", pady=(Theme.px(14), 0))
        tk.Label(body, text="An AI medical scribe that listens to the visit and writes the note, "
                            "on your computer.", bg=Palette.BG, fg=Palette.TEXT_MUTED, font=Theme.font("medium"),
                 justify="left", wraplength=Theme.px(680)).pack(anchor="w", pady=(Theme.px(4), Theme.px(16)))

        grid = tk.Frame(body, bg=Palette.BG)
        grid.pack(fill="x")
        for column in (0, 1):
            grid.grid_columnconfigure(column, weight=1, uniform="cards")
        self._icons = []
        for index, (name, title, text) in enumerate(FEATURES):
            card = RoundedFrame(grid, fill=Palette.SURFACE, outline=Palette.BORDER, radius=12, padx=2, pady=2)
            card.grid(row=index // 2, column=index % 2, sticky="nsew", padx=(0, Theme.px(10)) if index % 2 == 0 else 0,
                      pady=(0, Theme.px(10)))
            inner = card.body if hasattr(card, "body") else card
            picture = icon(name, 22, self._accent())
            self._icons.append(picture)
            tk.Label(inner, image=picture, bg=Palette.SURFACE).grid(row=0, column=0, rowspan=2, padx=(Theme.px(14), Theme.px(10)),
                                                                    pady=Theme.px(12), sticky="n")
            tk.Label(inner, text=title, bg=Palette.SURFACE, fg=Palette.TEXT, font=Theme.font("medium", "semibold"),
                     anchor="w").grid(row=0, column=1, sticky="w", pady=(Theme.px(12), 0))
            tk.Label(inner, text=text, bg=Palette.SURFACE, fg=Palette.TEXT_MUTED, font=Theme.font("small"),
                     justify="left", anchor="w", wraplength=Theme.px(255)).grid(row=1, column=1, sticky="w",
                                                                                 padx=(0, Theme.px(12)), pady=(0, Theme.px(12)))
            inner.grid_columnconfigure(1, weight=1)

        actions = tk.Frame(body, bg=Palette.BG)
        actions.pack(fill="x", pady=(Theme.px(8), 0))
        Button(actions, text="Get started", variant="primary", size="lg", command=self._close).pack(side="right")
        Button(actions, text="Take the quick tour", variant="secondary", size="lg", icon_name="sparkle",
               command=lambda: show_tour(self.parent)).pack(side="right", padx=(0, Theme.px(10)))
        Button(actions, text="Interface guide", variant="ghost", size="lg", command=self._guide).pack(side="left")

        link = tk.Label(body, text="Questions or ideas? Join the community on Discord", bg=Palette.BG,
                        fg=self._accent(), font=Theme.font("small", "medium"), cursor="hand2")
        link.pack(anchor="w", pady=(Theme.px(14), 0))
        link.bind("<Button-1>", lambda e: webbrowser.open(DISCORD_URL))

    @staticmethod
    def _accent():
        """Teal for icons and links, lighter on the dark background so it stays readable."""
        return Palette.PRIMARY if Theme.mode == "light" else Palette.MINT

    def _guide(self):
        guide = ImageWindow(self.parent, "Interface guide", get_file_path("assets", "help.png"), width=1250, height=858)
        guide.window.lift()

    def _close(self):
        if self.callback:
            self.callback(self.dont_show.get())
        self.window.destroy()
