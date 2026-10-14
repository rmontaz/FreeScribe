"""
Workspace.py

The main FreeScribe window: a session sidebar on the left and, on the right, the
current session with its recorder, Context / Transcript / Note tabs, any extra
documents, and an assistant bar for asking for edits.

The workspace owns layout and presentation only. Everything that records audio
or talks to a model stays in ``client.py``; the two sides meet through

* ``actions`` - a dict of callables the workspace invokes when the user asks for
  something (start recording, create a note, open settings ...), and
* the public methods on :class:`Workspace` that ``client.py`` calls to reflect
  what is happening (``set_recording_state``, ``apply_note``, ``show_warning`` ...).

This software is released under the AGPL-3.0 license.
"""

import datetime
import os
import time
import tkinter as tk
import tkinter.messagebox as messagebox
from tkinter import filedialog

from UI.ChatWindow import ChatPanel
from UI.Icons import icon, logo
from UI.NoteStyleSelector import NoteStyleSelector
from UI.StartScreen import RecordingBanner, StartScreen
from UI.Theme import Palette, Theme
from UI.Widgets.Controls import (Button, Chip, LevelMeter, PlaceholderEntry, RoundedFrame, ScrollFrame,
                                 Tabs, TextArea, Toast, Tooltip, popup_menu, rounded_image)
from services import export as exporter
from services.sessions import (DEFAULT_DETAIL_LEVEL, DETAIL_LEVELS, Document, Session, SessionStore,
                               add_denials_to_note, format_clock, format_duration, missing_denials)
from utils.log_config import logger

DISCLAIMER = "Review every note before use. AI-generated documentation can contain errors."
ASK_PLACEHOLDER = "Ask FreeScribe to edit this note, e.g. “make the plan more concise”"
ASK_PLACEHOLDER_EMPTY = "Create a note first, then ask for edits here"
EDIT_SUGGESTIONS = ("Make it more concise", "Use full sentences", "Add safety-netting advice",
                    "Write it for the patient")
TRANSCRIPT_PLACEHOLDER = ("Start transcribing and the conversation will appear here.\n"
                          "You can also paste or type a transcript, then choose Create note.")
CONTEXT_PLACEHOLDER = ("Add anything that was not said aloud and should inform the note: relevant history, "
                       "medications, vitals, results, or your own observations.")
NOTE_PLACEHOLDER = "Your note will appear here once the session has been transcribed."
NOTE_PLACEHOLDER_NO_NOTE = ("Notes are not created while the \u201cNo Note Generation\u201d template is chosen. "
                            "Pick another template to create one.")
NOTE_PLACEHOLDER_TRANSCRIBE_ONLY = ("Transcribe Only Mode is on, so FreeScribe records and transcribes without "
                                    "writing notes. Turn it off in Settings to create notes.")


def _ellipsize(text, font, max_width):
    """Shorten ``text`` with an ellipsis so it fits ``max_width`` pixels."""
    if max_width <= 0 or font.measure(text) <= max_width:
        return text
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if font.measure(text[:middle] + "…") <= max_width:
            low = middle
        else:
            high = middle - 1
    return text[:low].rstrip() + "…"


def session_status(session, recording=False):
    """The short status shown on a session in the sidebar: (text, background, text colour) or None."""
    if recording:
        return "Recording", Palette.DANGER_SOFT, Palette.DANGER
    if session.note.strip():
        return "Note ready", Palette.PRIMARY_SOFT, Palette.PRIMARY_PRESSED
    if session.transcript.strip():
        return "Transcript", Palette.RAISED, Palette.TEXT_MUTED
    if session.context.strip() or session.documents:
        return "Draft", Palette.RAISED, Palette.TEXT_MUTED
    return None


class ToolRow(tk.Canvas):
    """A sidebar tool: a coloured icon tile and a label, the whole row clickable."""

    HEIGHT = 40

    def __init__(self, parent, text, icon_name, tint, tile, command, tooltip=None, surface=Palette.SIDEBAR):
        super().__init__(parent, bg=surface, highlightthickness=0, bd=0, height=Theme.px(self.HEIGHT),
                         cursor="hand2", takefocus=1)
        self.text, self.icon_name, self.tint, self.tile = text, icon_name, tint, tile
        self._command = command
        self.enabled = True
        self._hover = False
        self._font = Theme.font("base", "medium")
        self._bg_item = self.create_image(0, 0, anchor="nw")
        self._tile_item = self.create_image(Theme.px(8), Theme.px(self.HEIGHT) / 2, anchor="w")
        self._icon_item = self.create_image(Theme.px(8 + 14), Theme.px(self.HEIGHT) / 2, anchor="center")
        self._text_item = self.create_text(Theme.px(8 + 28 + 10), Theme.px(self.HEIGHT) / 2, anchor="w",
                                           font=self._font, text=text)
        self.bind("<Configure>", lambda e: self.refresh())
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<Button-1>", lambda e: self.invoke())
        self.bind("<Key-Return>", lambda e: self.invoke())
        self.bind("<Key-space>", lambda e: self.invoke())
        if tooltip:
            Tooltip(self, tooltip)
        self.refresh()

    def invoke(self):
        if self.enabled and callable(self._command):
            self._command()

    def set_enabled(self, enabled):
        enabled = bool(enabled)
        if enabled != self.enabled:
            self.enabled = enabled
            self.configure(cursor="hand2" if enabled else "arrow")
            self.refresh()

    def _set_hover(self, hover):
        self._hover = hover and self.enabled
        self.refresh()

    def refresh(self):
        width, height = max(self.winfo_width(), 2), Theme.px(self.HEIGHT)
        self._image = rounded_image(width, height, Theme.px(8), Palette.HOVER) if self._hover else ""
        self.itemconfigure(self._bg_item, image=self._image)
        tile = Theme.px(28)
        self._tile = rounded_image(tile, tile, Theme.px(7), self.tile if self.enabled else Palette.DISABLED_BG)
        self.itemconfigure(self._tile_item, image=self._tile)
        self.itemconfigure(self._icon_item, image=icon(self.icon_name, 15,
                                                       self.tint if self.enabled else Palette.DISABLED_TEXT))
        self.itemconfigure(self._text_item, fill=Palette.TEXT if self.enabled else Palette.DISABLED_TEXT)


class SessionRow(tk.Canvas):
    """One session in the sidebar list."""

    HEIGHT = 54

    def __init__(self, parent, session, on_select, on_menu, selected=False, surface=Palette.SIDEBAR,
                 is_recording=None):
        super().__init__(parent, bg=surface, highlightthickness=0, bd=0, height=Theme.px(self.HEIGHT), cursor="hand2")
        self.session = session
        self._on_select, self._on_menu = on_select, on_menu
        self._is_recording = is_recording or (lambda session: False)
        self.selected = selected
        self._hover = False
        self._enabled = True
        self._title_font = Theme.font("base", "medium")
        self._title_font_selected = Theme.font("base", "semibold")
        self._sub_font = Theme.font("small")
        self._bg_item = self.create_image(0, 0, anchor="nw")
        self._title_item = self.create_text(Theme.px(12), Theme.px(19), anchor="w", font=self._title_font)
        self._sub_item = self.create_text(Theme.px(12), Theme.px(37), anchor="w", font=self._sub_font)
        self._pill_item = self.create_image(0, 0, anchor="e")
        self._pill_text = self.create_text(0, 0, anchor="center", font=Theme.font("micro", "semibold"))
        self._menu_item = self.create_image(0, 0, anchor="e")
        self.bind("<Configure>", lambda e: self.refresh())
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<Button-1>", self._on_click)
        self.bind("<Button-3>", self._on_right_click)
        self.bind("<Button-2>", self._on_right_click)

    def set_enabled(self, enabled):
        self._enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow")
        self.refresh()

    def set_selected(self, selected):
        if selected != self.selected:
            self.selected = selected
            self.refresh()

    def _set_hover(self, hover):
        self._hover = hover and self._enabled
        self.refresh()

    def _menu_zone(self, x):
        return x >= self.winfo_width() - Theme.px(34)

    def _on_click(self, event):
        if not self._enabled:
            return
        if self._hover and self._menu_zone(event.x):
            self._on_menu(self.session, self)
        else:
            self._on_select(self.session)

    def _on_right_click(self, event):
        if self._enabled:
            self._on_menu(self.session, self)

    def refresh(self):
        width, height = self.winfo_width(), Theme.px(self.HEIGHT)
        if width <= 1:
            return
        session = self.session
        if self.selected:
            # The open session reads as a card lifted off the sidebar
            image = rounded_image(width, height - Theme.px(4), Theme.px(10), Palette.BG, outline=Palette.BORDER)
        elif self._hover:
            image = rounded_image(width, height - Theme.px(4), Theme.px(10), Palette.HOVER)
        else:
            image = ""
        self._image = image
        self.itemconfigure(self._bg_item, image=image)
        self.coords(self._bg_item, 0, Theme.px(2))

        reserve = Theme.px(34) if self._hover else Theme.px(12)
        status = None if self._hover else session_status(session, self._is_recording(session))
        if status:
            text, pill_bg, pill_fg = status
            pill_font = Theme.font("micro", "semibold")
            pill_w, pill_h = pill_font.measure(text) + Theme.px(14), Theme.px(18)
            self._pill = rounded_image(pill_w, pill_h, pill_h // 2, pill_bg)
            right = width - Theme.px(10)
            self.itemconfigure(self._pill_item, image=self._pill, state="normal")
            self.coords(self._pill_item, right, Theme.px(19))
            self.itemconfigure(self._pill_text, text=text, fill=pill_fg, state="normal")
            self.coords(self._pill_text, right - pill_w / 2, Theme.px(19))
            title_width = width - Theme.px(12) - pill_w - Theme.px(18)
        else:
            self.itemconfigure(self._pill_item, state="hidden")
            self.itemconfigure(self._pill_text, state="hidden")
            title_width = width - Theme.px(12) - reserve
        text_width = width - Theme.px(12) - reserve
        untitled_blank = session.is_empty
        title = "New session" if untitled_blank else session.display_title
        title_color = Palette.TEXT if self._enabled else Palette.TEXT_MUTED
        font = self._title_font_selected if self.selected else self._title_font
        self.itemconfigure(self._title_item, text=_ellipsize(title, font, title_width), fill=title_color, font=font)
        subtitle = session.subtitle()
        if session.template and session.note.strip():
            subtitle += f"  ·  {session.template.replace(' - Default', '')}"
        self.itemconfigure(self._sub_item, text=_ellipsize(subtitle, self._sub_font, text_width),
                           fill=Palette.TEXT_MUTED if self._enabled else Palette.TEXT_FAINT)
        if self._hover:
            self.itemconfigure(self._menu_item, image=icon("dots", 16, Palette.TEXT_MUTED), state="normal")
            self.coords(self._menu_item, width - Theme.px(10), height / 2)
        else:
            self.itemconfigure(self._menu_item, state="hidden")


class Workspace:
    """Builds and manages the main window content."""

    SIDEBAR_WIDTH = 272
    MIN_SIZE = (980, 600)
    DEFAULT_SIZE = (1200, 760)
    COMPACT_SIZE = (292, 56)
    LIST_PAGE = 150          # sessions drawn at once; older ones appear on request

    def __init__(self, root, store: SessionStore, actions: dict, ui_state=None, mic=None, version=""):
        self.root = root
        self.store = store
        self.actions = actions or {}
        self.ui_state = ui_state
        self.mic = mic
        self.version = version

        self.current: Session = None
        self.transcribe_only = False   # the "Transcribe Only Mode" setting: no notes, no AI tools
        self.is_recording = False
        self.is_paused = False
        self.locked = False
        self.busy_text = None
        self.settings_enabled = True
        self.storing_locally = False
        self.compact = False
        self.warning_text = None

        self._rows = {}
        self._pages = {}
        self._doc_areas = {}
        self._list_job = None
        self._search_query = ""
        self._list_limit = self.LIST_PAGE
        self._capture_prefix = ""
        self._rec_base = 0.0
        self._rec_mark = None
        self._busy_phase = 0
        self._status_rows = []
        self.chat_panel = None
        self._ignored_words = set()      # words the clinician confirmed are right

        self._build()
        self.select_session(self._blank_session())
        self._rebuild_session_list()
        self._bind_shortcuts()
        if mic is not None:
            mic.add_listener(self._refresh_mic)
        self._refresh_mic()
        self._tick()
        self._meter_tick()
        self._status_tick()
        self._apply_note_mode()

    # ================================================================ plumbing
    def _act(self, name, *args, default=None):
        """Invoke one of the callbacks supplied by the application."""
        callback = self.actions.get(name)
        if callback is None:
            return default
        try:
            return callback(*args)
        except Exception as error:  # a UI callback must never take the window down
            logger.exception(f"Workspace action '{name}' failed: {error}")
            return default

    def toast(self, message, icon_name="check", **kwargs):
        if self.compact:
            return
        Toast.show(self.main, message, icon_name=icon_name, **kwargs)

    # ================================================================== build
    def _build(self):
        self.frame = tk.Frame(self.root, bg=Palette.BG)
        self.frame.pack(fill="both", expand=True)

        self.full = tk.Frame(self.frame, bg=Palette.BG)
        self.full.pack(fill="both", expand=True)

        self._build_sidebar(self.full)
        self._build_rail(self.full)
        self._sidebar_border = tk.Frame(self.full, bg=Palette.BORDER, width=1)
        self._sidebar_border.pack(side="left", fill="y")

        self.main = tk.Frame(self.full, bg=Palette.BG)
        self.main.pack(side="left", fill="both", expand=True)

        # Host for optional status bars owned by MainWindowUI (Docker status).
        self.status_host = tk.Frame(self.main, bg=Palette.BG)
        self.status_host.pack(side="bottom", fill="x")

        self._build_banner(self.main)
        self._build_header(self.main)
        self.rec_banner = RecordingBanner(self.main, on_pause=lambda: self._act("toggle_pause"),
                                          on_stop=lambda: self._act("toggle_recording"))
        self._build_tabs(self.main)
        self._build_footer(self.main)
        self._build_pages(self.main)
        self._build_compact(self.frame)

    # ---------------------------------------------------------------- sidebar
    def _build_sidebar(self, parent):
        bg = Palette.SIDEBAR
        side = tk.Frame(parent, bg=bg, width=Theme.px(self.SIDEBAR_WIDTH))
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        self.sidebar = side

        brand = tk.Frame(side, bg=bg)
        brand.pack(fill="x", padx=(Theme.px(16), Theme.px(10)), pady=(Theme.px(16), Theme.px(14)))
        logo_image = logo(26)
        if logo_image is not None:
            tk.Label(brand, image=logo_image, bg=bg).pack(side="left")
        tk.Label(brand, text="FreeScribe", bg=bg, fg=Palette.TEXT, font=Theme.font("large", "semibold")).pack(
            side="left", padx=(Theme.px(9), 0))
        self.compact_button = Button(brand, icon_name="compact", variant="ghost", size="sm", surface=bg,
                                     command=lambda: self._act("toggle_view"),
                                     tooltip="Compact recorder that stays on top")
        self.compact_button.pack(side="right")

        self.new_button = Button(side, text="New session", icon_name="plus", variant="primary", size="lg",
                                 stretch=True, surface=bg, command=self.new_session,
                                 tooltip=Theme.keys("Start a new session (Ctrl+N)"))
        self.new_button.pack(fill="x", padx=Theme.px(14))

        search = RoundedFrame(side, fill=Palette.BG, outline=Palette.BORDER, radius=8, padx=10, pady=6, surface=bg)
        search.pack(fill="x", padx=Theme.px(14), pady=(Theme.px(10), Theme.px(4)))
        tk.Label(search.body, image=icon("search", 15, Palette.TEXT_FAINT), bg=Palette.BG).pack(side="left")
        self.search_entry = PlaceholderEntry(search.body, placeholder="Search sessions", bg=Palette.BG,
                                             on_change=self._on_search, font=Theme.font("base"))
        self.search_entry.pack(side="left", fill="x", expand=True, padx=(Theme.px(8), 0))
        self.search_entry.bind("<FocusIn>", lambda e: search.set_outline(Palette.PRIMARY), add="+")
        self.search_entry.bind("<FocusOut>", lambda e: search.set_outline(Palette.BORDER), add="+")
        self.search_entry.bind("<Escape>", lambda e: (self.search_entry.set_value(""), self._on_search("")))

        # Tools sit at the top where they are easy to find
        tools = tk.Frame(side, bg=bg)
        tools.pack(fill="x", padx=Theme.px(8), pady=(Theme.px(8), 0))
        self.tools_frame = tools
        if any(name in self.actions for name in ("open_chat", "fill_form", "open_eform_mapper")):
            tk.Label(tools, text="TOOLS", bg=bg, fg=Palette.TEXT_FAINT, font=Theme.font("micro", "semibold"),
                     anchor="w").pack(fill="x", padx=Theme.px(4), pady=(Theme.px(4), Theme.px(2)))
        if "open_chat" in self.actions:
            self.chat_button = ToolRow(tools, "Chat about this visit", "chat", Palette.PRIMARY, Palette.PRIMARY_SOFT,
                                       command=lambda: self._act("open_chat"),
                                       tooltip="Ask the AI about the open session")
            self.chat_button.pack(fill="x")
        if "fill_form" in self.actions:
            self.fill_form_button = ToolRow(tools, "Fill PDF form", "form", "#8A4B12", "#FFE3CF",
                                            command=lambda: self._act("fill_form") if not self.locked else None,
                                            tooltip="Fill a PDF form from the session, or by talking it through")
            self.fill_form_button.pack(fill="x")
        if "open_eform_mapper" in self.actions:
            self.mapper_button = ToolRow(tools, "eForm mapper", "mapper", Palette.INFO, Palette.INFO_SOFT,
                                         command=lambda: self._act("open_eform_mapper"),
                                         tooltip="Map an OSCAR eForm from a PDF, in your web browser")
            self.mapper_button.pack(fill="x")

        # Footer is packed before the list so the list takes whatever height is left.
        footer = tk.Frame(side, bg=bg)
        footer.pack(side="bottom", fill="x")
        tk.Frame(footer, bg=Palette.BORDER, height=1).pack(fill="x")

        # Details (model status, storage, version), shown when the status chip is clicked
        self.details_frame = tk.Frame(footer, bg=bg)
        self.status_frame = tk.Frame(self.details_frame, bg=bg)
        self.status_frame.pack(fill="x", padx=Theme.px(18), pady=(Theme.px(10), Theme.px(2)))
        self.storage_label = tk.Label(self.details_frame, bg=bg, fg=Palette.TEXT_MUTED, font=Theme.font("micro"),
                                      justify="left", anchor="w",
                                      wraplength=Theme.px(self.SIDEBAR_WIDTH - 36))
        self.storage_label.pack(fill="x", padx=Theme.px(18), pady=(Theme.px(6), 0))
        tk.Label(self.details_frame, text=f"FreeScribe {self.version}".strip(), bg=bg, fg=Palette.TEXT_FAINT,
                 font=Theme.font("micro"), anchor="w").pack(fill="x", padx=Theme.px(18), pady=(Theme.px(2), 0))

        nav = tk.Frame(footer, bg=bg)
        nav.pack(fill="x", padx=Theme.px(10), pady=Theme.px(10))
        self._sidebar_nav = nav
        self.help_button = Button(nav, icon_name="help", variant="ghost-strong", size="sm", surface=bg,
                                  command=self._open_help_menu, tooltip="Help")
        self.help_button.pack(side="right")
        self.settings_button = Button(nav, icon_name="settings", variant="ghost-strong", size="sm", surface=bg,
                                      command=lambda: self._act("open_settings"), tooltip="Settings")
        self.settings_button.pack(side="right")
        self.data_button = Button(nav, icon_name="folder", variant="ghost-strong", size="sm", surface=bg,
                                  command=self._open_data_menu, tooltip="Manage data")
        self.data_button.pack(side="right")
        self.status_chip = Chip(nav, text="Checking…", tone="neutral", dot=Palette.TEXT_FAINT, surface=bg,
                                command=self.toggle_details, tooltip="Where speech and notes are processed")
        self.status_chip.pack(side="left")

        self.session_list = ScrollFrame(side, bg=bg)
        self.session_list.pack(fill="both", expand=True, padx=(Theme.px(8), Theme.px(3)), pady=(Theme.px(4), 0))
        self.set_storage_mode(self.storing_locally)

    def toggle_details(self):
        """Show or hide the model status, storage note and version under the sidebar."""
        if self.details_frame.winfo_manager():
            self.details_frame.pack_forget()
        else:
            self.details_frame.pack(fill="x", before=self._sidebar_nav)

    def _build_rail(self, parent):
        """The narrow strip that stands in for the sidebar while a visit is transcribed."""
        bg = Palette.SIDEBAR
        rail = tk.Frame(parent, bg=bg, width=Theme.px(64))
        rail.pack_propagate(False)
        logo_image = logo(28)
        if logo_image is not None:
            tk.Label(rail, image=logo_image, bg=bg).pack(pady=(Theme.px(18), Theme.px(12)))
        self.rail_button = Button(rail, icon_name="panel", variant="ghost-strong", size="md", surface=bg,
                                  command=self.expand_sidebar, tooltip="Show sessions")
        self.rail_button.pack()
        self.rail = rail

    def collapse_sidebar(self):
        """Give the visit the room while it is transcribed."""
        if self.rail.winfo_manager() or self.compact:
            return
        self.sidebar.pack_forget()
        self.rail.pack(side="left", fill="y", before=self._sidebar_border)

    def expand_sidebar(self):
        if not self.rail.winfo_manager():
            return
        self.rail.pack_forget()
        self.sidebar.pack(side="left", fill="y", before=self._sidebar_border)

    # ----------------------------------------------------------------- banner
    def _build_banner(self, parent):
        self.banner = tk.Frame(parent, bg=Palette.WARNING_SOFT)
        inner = tk.Frame(self.banner, bg=Palette.WARNING_SOFT)
        inner.pack(fill="x", padx=Theme.px(32), pady=Theme.px(8))
        tk.Label(inner, image=icon("warning", 16, Palette.WARNING), bg=Palette.WARNING_SOFT).pack(side="left")
        self.banner_label = tk.Label(inner, bg=Palette.WARNING_SOFT, fg=Palette.WARNING, justify="left",
                                     anchor="w", font=Theme.font("base", "medium"))
        self.banner_label.pack(side="left", padx=(Theme.px(8), 0), fill="x", expand=True)
        self.banner_close = Button(inner, icon_name="close", variant="ghost", size="xs",
                                   surface=Palette.WARNING_SOFT, command=self.hide_warning)
        self.banner_action = Button(inner, text="", variant="secondary", size="xs", surface=Palette.WARNING_SOFT)
        tk.Frame(self.banner, bg=Palette.WARNING_BORDER, height=1).pack(fill="x", side="bottom")
        self.banner_label.bind("<Configure>", lambda e: self.banner_label.configure(wraplength=max(200, e.width)))

    # ----------------------------------------------------------------- header
    def _build_header(self, parent):
        bg = Palette.BG
        self.header = tk.Frame(parent, bg=bg)
        self.header.pack(fill="x", padx=Theme.px(32), pady=(Theme.px(22), Theme.px(10)))

        right = tk.Frame(self.header, bg=bg)
        right.pack(side="right", anchor="n", padx=(Theme.px(16), 0))
        self.header_actions = right
        self.record_menu_button = Button(right, icon_name="chevron-down", variant="primary", size="lg",
                                         command=self._open_record_menu, tooltip="More ways to start")
        self.record_menu_button.pack(side="right", padx=(Theme.px(2), 0))
        self.record_button = Button(right, text="Start transcribing", icon_name="mic", variant="primary", size="lg",
                                    command=lambda: self._act("toggle_recording"),
                                    tooltip=Theme.keys("Start or stop transcribing (Alt+R)"))
        self.record_button.pack(side="right")
        self.pause_button = Button(right, text="Pause", icon_name="pause", variant="secondary", size="lg",
                                   command=lambda: self._act("toggle_pause"), tooltip=Theme.keys("Pause or resume (Alt+P)"))

        left = tk.Frame(self.header, bg=bg)
        left.pack(side="left", fill="x", expand=True)
        self._header_left = left
        self.title_entry = PlaceholderEntry(left, placeholder="Add patient details", bg=bg,
                                            font=Theme.font("display", "semibold"),
                                            on_change=self._on_title_change,
                                            on_submit=lambda value: self.root.focus_set())
        self.title_entry.pack(fill="x")
        Tooltip(self.title_entry, "Name this session, for example with the patient's name or initials")

        meta = tk.Frame(left, bg=bg)
        meta.pack(fill="x", pady=(Theme.px(7), 0))
        gap = (0, Theme.px(16))
        self.date_chip = Chip(meta, tone="plain", icon_name="calendar", surface=bg)
        self.date_chip.pack(side="left", padx=gap)
        self.time_chip = Chip(meta, text="00:00", tone="plain", icon_name="clock", surface=bg)
        self.time_chip.pack(side="left", padx=gap)
        self.lang_chip = Chip(meta, tone="plain", icon_name="globe", surface=bg,
                              tooltip="Transcription language. Change it in Settings → Speech-to-Text.")
        self.lang_chip.pack(side="left", padx=gap)
        # The level meter sits in front of the microphone name so it is never the part that gets cut off.
        self.meter = LevelMeter(meta, surface=bg)
        self.meter.pack(side="left", padx=(0, Theme.px(1)))
        self.mic_chip = Chip(meta, tone="plain", surface=bg, command=self._open_mic_menu,
                             tooltip="Choose microphone")
        self.mic_chip.pack(side="left")
        self.busy_chip = Chip(meta, tone="info", surface=bg)
        self._meta = meta
        self._mic_name_width = 210
        meta.bind("<Configure>", self._on_meta_configure)

    # ------------------------------------------------------------------- tabs
    def _build_tabs(self, parent):
        bg = Palette.BG
        self.tabs = Tabs(parent, on_select=self._on_tab_clicked, on_close=self._on_document_close, bg=bg)
        self.tabs.pack(fill="x", padx=(Theme.px(21), Theme.px(32)))
        self.tabs.add("context", "Context", icon_name="clipboard")
        self.tabs.add("transcript", "Transcript", icon_name="lines")
        self.tabs.add("note", "Note", icon_name="doc")

        self.add_doc_button = Button(self.tabs.trailing, icon_name="plus", variant="ghost", size="sm", surface=bg,
                                     command=self._open_document_menu,
                                     tooltip="Create another document from this session")
        self.add_doc_button.pack(side="left", pady=Theme.px(3))

        actions = self.tabs.actions
        self.export_button = Button(actions, text="Export", icon_name="download", variant="ghost-strong", size="sm",
                                    surface=bg, command=self._open_export_menu,
                                    tooltip="Save as Word, PDF or text")
        self.copy_button = Button(actions, text="Copy", icon_name="copy", variant="ghost-strong", size="sm",
                                  surface=bg, command=self.copy_current, tooltip="Copy this tab to the clipboard")
        self.generate_button = Button(actions, text="Create note", icon_name="sparkle", variant="soft", size="sm",
                                      surface=bg, command=lambda: self._act("generate"),
                                      tooltip=Theme.keys("Create the note from the transcript (Ctrl+Enter)"))
        self.template_button = Button(actions, text="Template", icon_name="doc", trailing_icon="chevron-down",
                                      variant="secondary", size="sm", surface=bg,
                                      command=self._open_template_menu, tooltip="Note template and level of detail")
        self.more_button = Button(actions, icon_name="dots", variant="ghost-strong", size="sm", surface=bg,
                                  command=self._open_more_menu, tooltip="Copy or export")
        self._toolbar_level = 0
        self._layout_actions()
        self.tabs.bind("<Configure>", lambda e: self._fit_toolbar())

    def _layout_actions(self):
        for button in (self.export_button, self.copy_button, self.more_button, self.generate_button,
                       self.template_button):
            button.pack_forget()
        pad = dict(pady=Theme.px(3))
        if self._toolbar_level >= 3:
            # Tight on room (for example with the chat open): Copy and Export move into a menu
            self.more_button.pack(side="right", **pad)
        else:
            self.export_button.pack(side="right", **pad)
            self.copy_button.pack(side="right", padx=(0, Theme.px(2)), **pad)
        for button in self._note_buttons():
            button.pack(side="right", padx=(0, Theme.px(8 if button is self.generate_button else 6)), **pad)

    def _note_buttons(self):
        """Create note and Template, as far as they apply to the current tab and mode."""
        if str(self.tabs.current or "").startswith("doc:") or self.transcribe_only:
            return []
        if NoteStyleSelector.current_style == NoteStyleSelector.NO_NOTE_STYLE:
            return [self.template_button]
        return [self.generate_button, self.template_button]

    def _toolbar_buttons(self):
        if self._toolbar_level >= 3:
            return [self.more_button] + self._note_buttons()
        return [self.export_button, self.copy_button] + self._note_buttons()

    def _open_more_menu(self):
        has_text = bool(self.current_page().get_text().strip())
        popup_menu(self.more_button, [
            {"label": "Copy", "command": self.copy_current, "enabled": has_text},
            None,
            {"label": "Export as Word (.docx)", "command": lambda: self.export_current(".docx"), "enabled": has_text},
            {"label": "Export as PDF (.pdf)", "command": lambda: self.export_current(".pdf"), "enabled": has_text},
            {"label": "Export as text (.txt)", "command": lambda: self.export_current(".txt"), "enabled": has_text},
        ], align="right")

    # ------------------------------------------------------- transcribe only
    def notes_enabled(self) -> bool:
        """False in Transcribe Only Mode or while the "No Note Generation" template is chosen."""
        return not self.transcribe_only and NoteStyleSelector.current_style != NoteStyleSelector.NO_NOTE_STYLE

    def set_transcribe_only(self, enabled: bool):
        """
        Switch Transcribe Only Mode: sessions are recorded and transcribed, and the
        note and AI tools (Create note, templates, documents, the edit bar) are hidden.
        """
        self.transcribe_only = bool(enabled)
        self._apply_note_mode()

    def _apply_note_mode(self):
        if not hasattr(self, "footer"):
            return
        self._refresh_footer()
        if self.transcribe_only:
            self.add_doc_button.pack_forget()
            self.note.set_placeholder(NOTE_PLACEHOLDER_TRANSCRIBE_ONLY)
        else:
            if not self.add_doc_button.winfo_manager():
                self.add_doc_button.pack(side="left", pady=Theme.px(3))
            no_note = NoteStyleSelector.current_style == NoteStyleSelector.NO_NOTE_STYLE
            self.note.set_placeholder(NOTE_PLACEHOLDER_NO_NOTE if no_note else NOTE_PLACEHOLDER)
        self._layout_actions()
        self._refresh_toolbar()

    def _refresh_footer(self):
        """The edit bar shows only when there can be a note to edit."""
        if not hasattr(self, "footer"):
            return
        start_screen = hasattr(self, "start_screen") and bool(self.start_screen.winfo_manager())
        show = not self.transcribe_only and not self.is_recording and not start_screen
        if show and not self.footer.winfo_manager():
            self.footer.pack(side="bottom", fill="x", padx=Theme.px(32), pady=(Theme.px(6), Theme.px(12)),
                             before=self.pages)
        elif not show and self.footer.winfo_manager():
            self.footer.pack_forget()

    def _apply_toolbar_level(self, level):
        """Set the toolbar labels: 0 = all labels, 1 = Copy/Export as icons, 2 = icons only."""
        self._toolbar_level = level
        has_note = bool(self.current and self.current.note.strip())
        self.copy_button.set(text="Copy" if level < 1 else "")
        self.export_button.set(text="Export" if level < 1 else "")
        self.generate_button.set(text=("Regenerate" if has_note else "Create note") if level < 2 else "",
                                 icon_name="refresh" if has_note else "sparkle")
        template = NoteStyleSelector.current_style.replace(" - Default", "")
        self.template_button.set(text=_ellipsize(template, Theme.font("small", "medium"), Theme.px(190))
                                 if level < 2 else "")

    def _fit_toolbar(self):
        """Shorten the toolbar step by step when the window is too narrow for the tabs and all labels."""
        if not hasattr(self, "template_button"):
            return
        available = (self.tabs.winfo_width() - self.tabs.strip.winfo_reqwidth()
                     - self.tabs.trailing.winfo_reqwidth() - Theme.px(10))
        if self.tabs.winfo_width() <= 1:
            self._set_toolbar_level(0)
            return
        self.tabs.set_compact(False)
        for level in (0, 1, 2, 3, 4):
            self._set_toolbar_level(min(level, 3))
            if level == 4:
                self.tabs.set_compact(True)
                break
            needed = sum(button.winfo_reqwidth() for button in self._toolbar_buttons()) + Theme.px(20)
            if needed <= available:
                break

    def _set_toolbar_level(self, level):
        moved = (level >= 3) != (self._toolbar_level >= 3)
        self._apply_toolbar_level(level)
        if moved:
            self._layout_actions()

    # ------------------------------------------------------------------ pages
    def _build_pages(self, parent):
        self.pages = tk.Frame(parent, bg=Palette.BG)
        self.pages.pack(fill="both", expand=True)
        self.pages.grid_rowconfigure(0, weight=1)
        self.pages.grid_columnconfigure(0, weight=1)

        self.context = TextArea(self.pages, placeholder=CONTEXT_PLACEHOLDER, padx=32, comfortable=True,
                                on_change=lambda value: self._update_field("context", value))
        self.transcript = TextArea(self.pages, placeholder=TRANSCRIPT_PLACEHOLDER, padx=32, comfortable=True,
                                   on_change=lambda value: (self._update_field("transcript", value),
                                                            self._schedule_drug_check(self.transcript)))
        self.note = TextArea(self.pages, placeholder=NOTE_PLACEHOLDER, padx=32, reading=True,
                             on_section_copy=self._section_copied,
                             on_change=lambda value: (self._update_field("note", value),
                                                      self._schedule_drug_check(self.note)))
        for area in (self.transcript, self.note):
            area.on_flag_ignore = self._ignore_word
        self._pages = {"context": self.context, "transcript": self.transcript, "note": self.note}
        for page in self._pages.values():
            page.grid(row=0, column=0, sticky="nsew")

        self._start_dismissed = None     # the session whose start screen was put aside
        self.start_screen = StartScreen(self.pages, actions={
            "record": lambda: self._act("toggle_recording"),
            "upload": lambda: self._act("upload"),
            "paste": lambda: self._leave_start_screen("transcript"),
            "context": lambda: self._leave_start_screen("context"),
            "choose_template": self._choose_template_from_start,
            "more_templates": lambda anchor: self._open_template_menu(anchor),
            "change_mic": lambda anchor: self._open_mic_menu(anchor),
        }, templates=lambda: (NoteStyleSelector.template_names(), NoteStyleSelector.current_style,
                              NoteStyleSelector.default_style, self.notes_enabled()))
        self._build_capture_side(self.pages)
        self.show_tab("transcript")

    def _build_capture_side(self, parent):
        """Beside the live transcript: context typed while listening, and what happens on stop."""
        bg = Palette.BG
        side = tk.Frame(parent, bg=bg, width=Theme.px(360))
        side.grid_propagate(False)
        side.pack_propagate(False)
        self.capture_side = side
        box = RoundedFrame(side, fill=Palette.SIDEBAR, outline=Palette.SIDEBAR, radius=14, padx=14, pady=12)
        box.pack(fill="x", padx=(0, Theme.px(28)), pady=(Theme.px(16), 0))
        tk.Label(box.body, text="Add what isn't said aloud", bg=Palette.SIDEBAR, fg=Palette.TEXT, anchor="w",
                 font=Theme.font("base", "semibold")).pack(fill="x")
        tk.Label(box.body, text="Vitals, exam findings, history. It goes into the note with the transcript.",
                 bg=Palette.SIDEBAR, fg=Palette.TEXT_MUTED, anchor="w", justify="left", font=Theme.font("small"),
                 wraplength=Theme.px(290)).pack(fill="x", pady=(Theme.px(2), Theme.px(8)))
        border = tk.Frame(box.body, bg=Palette.BORDER, padx=1, pady=1)
        border.pack(fill="x")
        self.capture_context = tk.Text(border, height=7, wrap="word", relief="flat", bd=0, font=Theme.font("base"),
                                       padx=Theme.px(10), pady=Theme.px(8), bg=Palette.BG, fg=Palette.TEXT,
                                       insertbackground=Palette.TEXT, highlightthickness=0, undo=True)
        self.capture_context.pack(fill="x")
        self.capture_context.bind("<<Modified>>", self._on_capture_context)
        self._capture_job = None

        info = RoundedFrame(side, fill=bg, outline=Palette.BORDER, radius=14, padx=14, pady=12)
        info.pack(fill="x", padx=(0, Theme.px(28)), pady=(Theme.px(12), 0))
        tk.Label(info.body, text="When you stop", bg=bg, fg=Palette.TEXT, anchor="w",
                 font=Theme.font("base", "semibold")).pack(fill="x")
        self.capture_note_label = tk.Label(info.body, bg=bg, fg=Palette.TEXT_MUTED, anchor="w", justify="left",
                                           font=Theme.font("small"), wraplength=Theme.px(290))
        self.capture_note_label.pack(fill="x", pady=(Theme.px(2), 0))
        self.capture_privacy = tk.Label(side, text="Audio stays on this computer", bg=bg, fg=Palette.TEXT_MUTED,
                                        anchor="w", font=Theme.font("small"))
        self.capture_privacy.pack(fill="x", padx=Theme.px(6), pady=(Theme.px(12), 0))

    def _on_capture_context(self, _event=None):
        if not self.capture_context.edit_modified():
            return
        self.capture_context.edit_modified(False)
        if self._capture_job is not None:
            self.root.after_cancel(self._capture_job)
        self._capture_job = self.root.after(300, self._store_capture_context)

    def _store_capture_context(self):
        self._capture_job = None
        value = self.capture_context.get("1.0", "end-1c")
        if self.current is not None and value != self.current.context:
            self.context.set_text(value)
            self.context.flush_change()

    def _refresh_capture_side(self):
        show = self.is_recording and not self.compact
        if show and not self.capture_side.winfo_manager():
            self.capture_context.delete("1.0", "end")
            self.capture_context.insert("1.0", self.current.context if self.current else "")
            self.capture_context.edit_modified(False)
            self.capture_side.grid(row=0, column=1, sticky="ns")
        elif not show and self.capture_side.winfo_manager():
            if self._capture_job is not None:
                self.root.after_cancel(self._capture_job)
                self._store_capture_context()
            self.capture_side.grid_remove()
        if show:
            if self.notes_enabled():
                style = NoteStyleSelector.current_style.replace(" - Default", "")
                text = f"FreeScribe writes a {style} on this computer. You can edit it before you use it."
            else:
                text = "FreeScribe keeps the transcript. No note is written."
            self.capture_note_label.configure(text=text)

    # ------------------------------------------------------------ start screen
    def _start_screen_wanted(self):
        session = self.current
        return (session is not None and session.is_empty and not self.is_recording and not self.locked
                and self._start_dismissed != session.id)

    def _refresh_start_screen(self):
        if not hasattr(self, "start_screen"):
            return
        if self._start_screen_wanted():
            if not self.start_screen.winfo_manager():
                self.start_screen.refresh_templates()
                self.start_screen.grid(row=0, column=0, columnspan=2, sticky="nsew")
            self.start_screen.tkraise()
        elif self.start_screen.winfo_manager():
            self.start_screen.grid_remove()
        # The start screen has its own note type cards; the toolbar has nothing to act on yet
        showing = bool(self.start_screen.winfo_manager())
        if showing == bool(self.tabs.actions.winfo_manager()):
            if showing:
                self.tabs.actions.pack_forget()
            else:
                self.tabs.actions.pack(side="right", fill="y")
        self._refresh_footer()

    def _leave_start_screen(self, tab_id):
        if self.current is not None:
            self._start_dismissed = self.current.id
        self.show_tab(tab_id)
        self._refresh_start_screen()
        self.current_page().focus_editor()

    def _choose_template_from_start(self, name):
        self._choose_template(name)
        self.start_screen.refresh_templates()

    def _section_copied(self, heading):
        self.toast(f"{heading or 'Section'} copied to clipboard")

    # ----------------------------------------------------------------- footer
    def _build_footer(self, parent):
        bg = Palette.BG
        footer = tk.Frame(parent, bg=bg)
        footer.pack(side="bottom", fill="x", padx=Theme.px(32), pady=(Theme.px(6), Theme.px(12)))
        self.footer = footer
        self.suggestions = tk.Frame(footer, bg=bg)
        self.suggestions.pack(fill="x", pady=(0, Theme.px(8)))
        for text in EDIT_SUGGESTIONS:
            chip = Chip(self.suggestions, text=text, tone="neutral", surface=bg,
                        command=lambda t=text: self._use_suggestion(t))
            chip.pack(side="left", padx=(0, Theme.px(6)))
        self.ask_frame = RoundedFrame(footer, fill=bg, outline=Palette.BORDER_STRONG, radius=14, padx=14, pady=8,
                                      surface=bg)
        self.ask_frame.pack(fill="x")
        body = self.ask_frame.body
        tk.Label(body, image=icon("sparkle", 17, Palette.PRIMARY), bg=bg).pack(side="left")
        self.ask_send = Button(body, icon_name="send", variant="primary", size="sm", pill=True, surface=bg,
                               command=self._submit_ask, tooltip="Send (Enter)")
        self.ask_send.pack(side="right")
        self.ask_entry = PlaceholderEntry(body, placeholder=ASK_PLACEHOLDER, bg=bg, font=Theme.font("medium"),
                                          on_submit=lambda value: self._submit_ask())
        self.ask_entry.pack(side="left", fill="x", expand=True, padx=Theme.px(10))
        self.ask_entry.bind("<FocusIn>", lambda e: self.ask_frame.set_outline(Palette.PRIMARY), add="+")
        self.ask_entry.bind("<FocusOut>", lambda e: self.ask_frame.set_outline(Palette.BORDER_STRONG), add="+")
        tk.Label(footer, text=DISCLAIMER, bg=bg, fg=Palette.TEXT_FAINT, font=Theme.font("micro")).pack(
            pady=(Theme.px(7), 0))

    # ---------------------------------------------------------- compact view
    def _build_compact(self, parent):
        bg = Palette.BG
        bar = tk.Frame(parent, bg=bg)
        inner = tk.Frame(bar, bg=bg)
        inner.pack(expand=True, padx=Theme.px(10), pady=Theme.px(8))
        self.compact_record = Button(inner, icon_name="mic", variant="primary", size="md", pill=True, surface=bg,
                                     command=lambda: self._act("toggle_recording"),
                                     tooltip=Theme.keys("Start or stop transcribing (Alt+R)"))
        self.compact_record.pack(side="left")
        self.compact_pause = Button(inner, icon_name="pause", variant="neutral", size="md", pill=True, surface=bg,
                                    command=lambda: self._act("toggle_pause"), tooltip=Theme.keys("Pause or resume (Alt+P)"))
        self.compact_pause.pack(side="left", padx=(Theme.px(6), 0))
        self.compact_time = tk.Label(inner, text="00:00", bg=bg, fg=Palette.TEXT, font=Theme.font("medium", "semibold"),
                                     width=6)
        self.compact_time.pack(side="left", padx=(Theme.px(8), Theme.px(4)))
        self.compact_meter = LevelMeter(inner, surface=bg)
        self.compact_meter.pack(side="left", padx=(0, Theme.px(8)))
        self.compact_expand = Button(inner, icon_name="expand", variant="ghost", size="md", surface=bg,
                                     command=lambda: self._act("toggle_view"), tooltip="Back to the full window")
        self.compact_expand.pack(side="left")
        self.compact_bar = bar
        self.compact_pause.set_enabled(False)

    # ------------------------------------------------------------------ chat
    def chat_visible(self) -> bool:
        return self.chat_panel is not None and bool(self.chat_panel.frame.winfo_manager())

    def toggle_chat(self, on_send, on_clear=None):
        """Show the chat panel for the open session, or hide it if it is showing."""
        if self.chat_visible():
            self.hide_chat()
            return None
        return self.show_chat(on_send, on_clear)

    def show_chat(self, on_send, on_clear=None):
        if self.chat_panel is None:
            self.chat_panel = ChatPanel(self.full, on_send, on_clear, on_close=self.hide_chat)
            self._chat_border = tk.Frame(self.full, bg=Palette.BORDER, width=1)
        if not self.chat_visible():
            self.chat_panel.frame.pack(side="right", fill="y", before=self.main)
            self._chat_border.pack(side="right", fill="y", before=self.main)
        self.chat_panel.set_session(self.current)
        self.chat_panel.focus()
        return self.chat_panel

    def hide_chat(self):
        if self.chat_visible():
            self.chat_panel.frame.pack_forget()
            self._chat_border.pack_forget()

    def refresh_chat(self, session_id=None):
        if self.chat_panel is not None:
            self.chat_panel.refresh(session_id)

    def show_compact(self):
        """Swap the full workspace for the small always-on-top recorder."""
        if self.compact:
            return
        self.flush_edits()
        self.compact = True
        Toast.dismiss(self.main)
        self.full.pack_forget()
        self.compact_bar.pack(fill="both", expand=True)

    def show_full(self):
        if not self.compact:
            return
        self.compact = False
        self.compact_bar.pack_forget()
        self.full.pack(fill="both", expand=True)

    # ============================================================== shortcuts
    def _bind_shortcuts(self):
        modifier = "Command" if self.root.tk.call("tk", "windowingsystem") == "aqua" else "Control"
        self.root.bind(f"<{modifier}-n>", lambda e: (self.new_session(), "break")[1])
        self.root.bind(f"<{modifier}-Return>", lambda e: (self._shortcut_generate(), "break")[1])
        self.root.bind(f"<{modifier}-f>", lambda e: (self.search_entry.focus_set(), "break")[1])

    def _shortcut_generate(self):
        if self.generate_button.enabled and not self.compact:
            self._act("generate")

    # =============================================================== sessions
    def _blank_session(self) -> Session:
        for session in self.store.sessions:
            if session.is_empty:
                return session
        return self.store.new()

    def new_session(self):
        """Switch to a fresh, empty session."""
        if self.locked or self.compact:
            return None
        self.flush_edits()
        if self.current is not None and self.current.is_empty:
            self.title_entry.focus_set()
            return self.current
        self.store.prune_empty()
        session = self.store.new()
        self.select_session(session)
        self._start_with_default_template()
        self._rebuild_session_list()
        self.session_list.scroll_to_top()
        return session

    def select_session(self, session: Session):
        """Show ``session`` in the editor."""
        if session is None:
            return
        self.flush_edits()
        self.current = session
        self.title_entry.set_value(session.title)
        self.context.set_text(session.context)
        self.transcript.set_text(session.transcript)
        self.note.set_text(session.note)
        self._rebuild_document_tabs()
        self._refresh_meta()
        self.hide_warning(kind="negatives")
        if self.chat_visible():
            self.chat_panel.set_session(session)
        for session_id, row in self._rows.items():
            row.set_selected(session_id == session.id)
        if session.note.strip():
            self.show_tab("note")
        else:
            self.show_tab("transcript")
        self._refresh_toolbar()

    def reload_sessions(self):
        """Call after the store was replaced or reloaded from disk."""
        current_id = self.current.id if self.current else None
        target = self.store.get(current_id) if current_id else None
        if target is None:
            target = self._blank_session()
        self.current = None
        self.select_session(target)
        self._rebuild_session_list()

    def delete_session(self, session: Session, confirm=True):
        if self.locked:
            return
        if confirm and not session.is_empty:
            if not messagebox.askyesno(
                    "Delete session",
                    f"Delete “{session.display_title}”?\n\n"
                    "The transcript, note and documents in this session will be permanently removed."):
                return
        was_current = session is self.current
        self.store.remove(session.id)
        if was_current:
            self.current = None
            self.select_session(self._blank_session())
        self._rebuild_session_list()
        self._act("sessions_changed")

    def delete_all_sessions(self):
        if self.locked:
            return
        if not messagebox.askyesno(
                "Delete all sessions",
                "Are you sure you want to delete all sessions? You will not be able to undo or recover them."):
            return
        self.store.clear()
        self.current = None
        self.select_session(self._blank_session())
        self._rebuild_session_list()
        self._act("sessions_cleared")
        self.toast("All sessions deleted", icon_name="trash")

    def flush_edits(self):
        """Make sure edits still waiting on the debounce timer reach the session."""
        for page in list(self._pages.values()):
            page.flush_change()

    # ------------------------------------------------------------ drug names
    def check_drug_names(self, area=None):
        """Underline words that look like a misheard drug name in ``area`` (default: every page)."""
        areas = [area] if area is not None else list(self._pages.values())
        for page in areas:
            if page is self.context:
                continue
            flags = self._act("find_drug_flags", page.get_text(), default=None)
            if flags is not None:
                page.set_flags([f for f in flags if f[2].lower() not in self._ignored_words])

    def _schedule_drug_check(self, area):
        """Check a page a moment after typing stops (live transcription arrives in many small pieces)."""
        jobs = self.__dict__.setdefault("_drug_jobs", {})
        job = jobs.pop(id(area), None)
        if job is not None:
            try:
                self.root.after_cancel(job)
            except tk.TclError:
                pass
        jobs[id(area)] = self.root.after(700, lambda: (jobs.pop(id(area), None),
                                                       area.winfo_exists() and self.check_drug_names(area)))

    def _ignore_word(self, word):
        self._ignored_words.add(word.lower())
        self.check_drug_names()

    def _update_field(self, field, value):
        session = self.current
        if session is None or getattr(session, field) == value:
            return
        was_empty = session.is_empty
        setattr(session, field, value)
        self._touch(session, was_empty)

    def _touch(self, session, was_empty=False):
        if was_empty and not session.is_empty:
            # The session really starts when its first content arrives.
            session.created = datetime.datetime.now().replace(microsecond=0).isoformat()
            self._refresh_meta()
        session.touch()
        row = self._rows.get(session.id)
        if row is not None and was_empty == session.is_empty:
            row.refresh()
        else:
            self._schedule_list_rebuild()
        self._refresh_toolbar()
        self._act("sessions_changed")

    def _on_title_change(self, value):
        session = self.current
        if session is None or session.title == value:
            return
        was_empty = session.is_empty
        session.title = value
        self._touch(session, was_empty)

    # ----------------------------------------------------------- session list
    def _on_search(self, value):
        self._search_query = (value or "").strip()
        self._list_limit = self.LIST_PAGE
        self._schedule_list_rebuild(delay=120)

    def _show_more_sessions(self):
        self._list_limit += self.LIST_PAGE
        self._rebuild_session_list()

    def _schedule_list_rebuild(self, delay=250):
        if self._list_job is not None:
            try:
                self.root.after_cancel(self._list_job)
            except tk.TclError:
                pass
        self._list_job = self.root.after(delay, self._rebuild_session_list)

    def _rebuild_session_list(self):
        if self._list_job is not None:
            try:
                self.root.after_cancel(self._list_job)
            except tk.TclError:
                pass
            self._list_job = None
        body = self.session_list.body
        for child in body.winfo_children():
            child.destroy()
        self._rows = {}
        bg = Palette.SIDEBAR
        groups = self.store.grouped(self._search_query, current=None if self._search_query else self.current)
        if not groups:
            message = ("No sessions match your search." if self._search_query
                       else "Your sessions will appear here.")
            tk.Label(body, text=message, bg=bg, fg=Palette.TEXT_FAINT, font=Theme.font("small"), justify="left",
                     anchor="w", wraplength=Theme.px(self.SIDEBAR_WIDTH - 48)).pack(
                fill="x", padx=Theme.px(12), pady=Theme.px(14))
            return
        shown, total = 0, sum(len(sessions) for _, sessions in groups)
        for label, sessions in groups:
            if shown >= self._list_limit:
                break
            tk.Label(body, text=label.upper(), bg=bg, fg=Palette.TEXT_FAINT, font=Theme.font("micro", "semibold"),
                     anchor="w").pack(fill="x", padx=Theme.px(12), pady=(Theme.px(10), Theme.px(3)))
            for session in sessions:
                if shown >= self._list_limit:
                    break
                row = SessionRow(body, session, on_select=self._on_row_select, on_menu=self._open_session_menu,
                                 selected=session is self.current,
                                 is_recording=lambda s: self.is_recording and s is self.current)
                row.pack(fill="x", padx=(0, Theme.px(5)))
                row.set_enabled(not self.locked)
                self._rows[session.id] = row
                shown += 1
        if shown < total:
            # Long histories are drawn a page at a time so the window stays quick to open and scroll.
            more = Button(body, text=f"Show older sessions ({total - shown} more)", variant="ghost", size="sm",
                          surface=bg, stretch=True, command=self._show_more_sessions)
            more.pack(fill="x", padx=(0, Theme.px(5)), pady=(Theme.px(8), Theme.px(10)))

    def _on_row_select(self, session):
        if self.locked or session is self.current:
            return
        previous = self.current
        self.select_session(session)
        if previous is not None and previous.is_empty:
            self.store.remove(previous.id)
            self._rebuild_session_list()

    def _open_session_menu(self, session, anchor):
        if self.locked:
            return
        popup_menu(anchor, [
            {"label": "Rename", "command": lambda: self._rename_session(session)},
            {"label": "Delete…", "command": lambda: self.delete_session(session)},
        ])

    def _rename_session(self, session):
        if session is not self.current:
            self._on_row_select(session)
        self.title_entry.focus_set()
        self.title_entry.select_range(0, "end")
        self.title_entry.icursor("end")

    # ================================================================= header
    def _refresh_meta(self):
        session = self.current
        if session is None:
            return
        moment = session.created_at
        today = datetime.date.today()
        if moment.date() == today:
            day = "Today"
        elif (today - moment.date()).days == 1:
            day = "Yesterday"
        else:
            day = f"{moment.strftime('%b')} {moment.day}" + (f", {moment.year}" if moment.year != today.year else "")
        self.date_chip.set(text=f"{day}  {format_clock(moment)}")
        if not self.is_recording:
            self.time_chip.set(text=format_duration(session.duration), tone="plain", icon_name="clock", dot=None)
        self.lang_chip.set(text=self._act("get_language", default="Auto-detect") or "Auto-detect")

    def _on_meta_configure(self, event):
        """Give the microphone name whatever room is left on the row; hide the language when it is tight."""
        fixed = sum(chip.winfo_reqwidth() + Theme.px(16) for chip in (self.date_chip, self.time_chip))
        fixed += self.meter.winfo_reqwidth() + Theme.px(24)
        if self.busy_text:
            fixed += self.busy_chip.winfo_reqwidth() + Theme.px(8)
        language = self.lang_chip.winfo_reqwidth() + Theme.px(16)
        room = event.width - fixed
        show_language = room - language >= Theme.px(110)
        if show_language != bool(self.lang_chip.winfo_manager()):
            if show_language:
                self.lang_chip.pack(side="left", padx=(0, Theme.px(16)), before=self.meter)
            else:
                self.lang_chip.pack_forget()
        if show_language:
            room -= language
        width = int(max(Theme.px(60), min(Theme.px(230), room)) / max(Theme.scale, 0.1))
        if abs(width - self._mic_name_width) >= 6:
            self._mic_name_width = width
            self._refresh_mic()

    def _refresh_mic(self):
        mic = self.mic
        if mic is None:
            self.mic_chip.set(text="No microphone")
            return
        name = mic.selected_name or "No microphone"
        font = Theme.font("small", "medium")
        width = Theme.px(self._mic_name_width)
        if not mic.status_ok:
            self.mic_chip.set(text=_ellipsize(mic.status_text.replace("Error: ", ""), font, width),
                              tone="danger", icon_name="warning")
        else:
            self.mic_chip.set(text=_ellipsize(name, font, width), tone="plain", icon_name=None)

    def _open_mic_menu(self, anchor=None):
        mic = self.mic
        if mic is None:
            return
        if not mic.enabled:
            self.toast("The microphone can't be changed while transcribing", icon_name="info")
            return
        mic.refresh_devices()
        names = mic.microphone_names()
        items = [{"label": "Microphone", "header": True}]
        if names:
            for name in names:
                items.append({"label": name, "checked": name == mic.selected_name,
                              "command": lambda n=name: mic.select_microphone(n)})
        else:
            items.append({"label": "No microphones found", "enabled": False})
        popup_menu(anchor or self.mic_chip, items)

    def _open_record_menu(self):
        if self.is_recording:
            return
        can_resume = bool(self.current and self.current.has_output) and not self.locked
        popup_menu(self.record_menu_button, [
            {"label": "Upload an audio file…", "command": lambda: self._act("upload"), "enabled": not self.locked},
            {"label": "Resume this session", "command": lambda: self._act("resume_recording"), "enabled": can_resume},
        ], align="right")

    # ----------------------------------------------------------- record state
    def set_recording_state(self, recording: bool, paused: bool = False):
        """Reflect whether audio is being captured (and whether capture is paused)."""
        recording, paused = bool(recording), bool(paused) and bool(recording)
        started = recording and not self.is_recording
        stopped = self.is_recording and not recording
        now = time.monotonic()

        if started:
            self._rec_base = float(self.current.duration or 0) if self.current else 0.0
            self._rec_mark = None if paused else now
        elif recording and paused != self.is_paused:
            if paused and self._rec_mark is not None:
                self._rec_base += now - self._rec_mark
                self._rec_mark = None
            elif not paused:
                self._rec_mark = now
        elif stopped:
            if self._rec_mark is not None:
                self._rec_base += now - self._rec_mark
            self._rec_mark = None
            if self.current is not None and abs(self.current.duration - self._rec_base) >= 0.5:
                self.current.duration = self._rec_base
                self._touch(self.current)

        self.is_recording, self.is_paused = recording, paused
        if (started or stopped) and self.current is not None and self.current.id in self._rows:
            self._rows[self.current.id].refresh()

        if started:
            self.collapse_sidebar()
            self.header_actions.pack_forget()
            self.rec_banner.pack(fill="x", padx=Theme.px(32), pady=(0, Theme.px(12)), before=self.tabs)
        elif stopped:
            self.rec_banner.pack_forget()
            self.header_actions.pack(side="right", anchor="n", padx=(Theme.px(16), 0), before=self._header_left)
            self.expand_sidebar()
        self.rec_banner.set_state(paused, writes_note=self.notes_enabled())
        if recording:
            self.record_button.set(text="Stop transcribing", icon_name="stop", variant="danger")
            self.record_menu_button.pack_forget()
            self.pause_button.set(text="Resume" if paused else "Pause", icon_name="play" if paused else "pause")
            self.pause_button.pack(side="right", padx=(0, Theme.px(8)))
            self.compact_record.set(icon_name="stop", variant="danger")
            self.compact_pause.set(icon_name="play" if paused else "pause")
            self.compact_pause.set_enabled(True)
        else:
            self.record_button.set(text="Start transcribing", icon_name="mic", variant="primary")
            self.pause_button.pack_forget()
            self.record_menu_button.pack(side="right", padx=(Theme.px(2), 0), before=self.record_button)
            self.compact_record.set(icon_name="mic", variant="primary")
            self.compact_pause.set(icon_name="pause")
            self.compact_pause.set_enabled(False)
        self._update_timer()
        self._refresh_toolbar()
        self._refresh_capture_side()
        self._refresh_footer()

    def recording_seconds(self) -> float:
        if not self.is_recording:
            return float(self.current.duration or 0) if self.current else 0.0
        if self._rec_mark is None:
            return self._rec_base
        return self._rec_base + (time.monotonic() - self._rec_mark)

    def _update_timer(self):
        text = format_duration(self.recording_seconds())
        if self.is_recording:
            if self.is_paused:
                self.time_chip.set(text=f"{text}  Paused", tone="warning", icon_name="pause", dot=None)
            else:
                self.time_chip.set(text=text, tone="danger", icon_name=None, dot=Palette.DANGER)
        else:
            self.time_chip.set(text=text, tone="plain", icon_name="clock", dot=None)
        self.compact_time.configure(text=text, fg=Palette.DANGER if self.is_recording and not self.is_paused
                                    else Palette.TEXT)
        self.rec_banner.set_time(text)

    def _tick(self):
        if not self.frame.winfo_exists():
            return
        if self.is_recording:
            self._update_timer()
        if self.busy_text:
            self._busy_phase = (self._busy_phase + 1) % 4
            self.busy_chip.set(text=self.busy_text + "." * self._busy_phase + " " * (3 - self._busy_phase))
        self.root.after(400, self._tick)

    def _meter_tick(self):
        if not self.frame.winfo_exists():
            return
        level = self.mic.level if self.mic is not None else 0.0
        live = self.is_recording and not self.is_paused
        color = Palette.PRIMARY if live else Palette.TEXT_FAINT
        meter = self.compact_meter if self.compact else self.meter
        meter.set_color(Palette.WARNING if (self.warning_text and self.compact) else color)
        meter.set_level(0.0 if self.is_paused else level)
        if self.is_recording and not self.compact:
            self.rec_banner.set_level(level, paused=self.is_paused)
        elif self.start_screen.winfo_manager():
            mic = self.mic
            self.start_screen.set_level(level, mic.selected_name if mic is not None else "",
                                        ok=mic.status_ok if mic is not None else True)
        self.root.after(70, self._meter_tick)

    # ----------------------------------------------------------------- status
    def set_busy(self, text=None):
        """Show (or clear, with ``None``) a small "working" indicator in the header."""
        self.busy_text = text or None
        if self.busy_text:
            self.busy_chip.set(text=self.busy_text + "   ")
            self.busy_chip.pack(side="left")
        else:
            self.busy_chip.pack_forget()

    def show_warning(self, text, closable=False, kind="notice", action_text=None, action=None):
        """Show a banner above the session. ``kind`` lets a caller clear only its own warning."""
        self.warning_text = text
        self.warning_kind = kind
        self.banner_label.configure(text=text)
        if closable:
            self.banner_close.pack(side="right")
        else:
            self.banner_close.pack_forget()
        if action_text and callable(action):
            self.banner_action.set(text=action_text, command=action)
            self.banner_action.pack(side="right", padx=(Theme.px(8), Theme.px(4)), before=self.banner_label)
        else:
            self.banner_action.pack_forget()
        self.banner.pack(fill="x", before=self.header)

    def hide_warning(self, kind=None):
        if kind is not None and getattr(self, "warning_kind", None) != kind:
            return
        self.warning_text = None
        self.warning_kind = None
        self.banner.pack_forget()

    def set_locked(self, locked: bool):
        """Lock the parts of the window that must not change while audio is captured."""
        self.locked = bool(locked)
        self.new_button.set_enabled(not self.locked)
        self.data_button.set_enabled(not self.locked)
        self.transcript.set_readonly(self.locked)
        self.note.set_readonly(self.locked)
        for area in self._doc_areas.values():
            area.set_readonly(self.locked)
        for row in self._rows.values():
            row.set_enabled(not self.locked)
        self._refresh_toolbar()

    def set_settings_enabled(self, enabled: bool):
        self.settings_enabled = bool(enabled)
        self.settings_button.set_enabled(self.settings_enabled)

    def set_storage_mode(self, storing_locally: bool):
        self.storing_locally = bool(storing_locally)
        if self.storing_locally:
            text = "Sessions are saved on this computer, encrypted."
        else:
            text = "Sessions are cleared when FreeScribe closes. Turn on local storage in Settings to keep them."
        self.storage_label.configure(text=text)

    def _status_tick(self):
        if not self.frame.winfo_exists():
            return
        self.refresh_status()
        self.root.after(2000, self._status_tick)

    def refresh_status(self):
        """Update the model status lines at the bottom of the sidebar."""
        rows = self._act("get_status", default=[]) or []
        signature = [(r.get("label"), r.get("detail"), r.get("state")) for r in rows]
        if signature == getattr(self, "_status_signature", None):
            return
        self._status_signature = signature
        self._refresh_status_chip(rows)
        for child in self.status_frame.winfo_children():
            child.destroy()
        colors = {"ok": Palette.SUCCESS, "loading": "#D08A00", "error": Palette.DANGER,
                  "remote": Palette.INFO, "off": Palette.TEXT_FAINT}
        bg = Palette.SIDEBAR
        for row in rows:
            line = tk.Frame(self.status_frame, bg=bg)
            line.pack(fill="x", pady=Theme.px(1))
            size = Theme.px(8)
            dot_image = rounded_image(size, size, size // 2, colors.get(row.get("state"), Palette.TEXT_FAINT))
            dot = tk.Label(line, image=dot_image, bg=bg)
            dot.image = dot_image
            dot.pack(side="left", padx=(0, Theme.px(7)))
            font = Theme.font("small")
            text = f"{row.get('label', '')}  ·  {row.get('detail', '')}"
            label = tk.Label(line, text=_ellipsize(text, font, Theme.px(self.SIDEBAR_WIDTH - 56)), bg=bg,
                             fg=Palette.TEXT_MUTED, font=font, anchor="w")
            label.pack(side="left", fill="x")
            if row.get("tooltip"):
                Tooltip(label, row["tooltip"])

    def _refresh_status_chip(self, rows):
        states = [r.get("state") for r in rows]
        if not rows:
            text, dot = "Status", Palette.TEXT_FAINT
        elif "error" in states:
            text, dot = "Needs attention", Palette.DANGER
        elif "loading" in states:
            text, dot = "Loading models…", "#D08A00"
        elif "remote" in states:
            text, dot = "Private · uses your server", Palette.INFO
        else:
            text, dot = "Private · on this computer", Palette.SUCCESS
        self.status_chip.set(text=text, dot=dot)

    def _open_help_menu(self):
        popup_menu(self.help_button, [
            {"label": "Help guide", "command": lambda: self._act("help_guide")},
            {"label": "About FreeScribe", "command": lambda: self._act("about")},
            {"label": "Third-party licences", "command": lambda: self._act("third_party_notices")},
            None,
            {"label": "Debug window", "command": lambda: self._act("debug_window")},
        ])

    def _open_data_menu(self):
        options = self._act("get_data_options", default={}) or {}
        items = []
        if options.get("recordings"):
            items.append({"label": "Manage recordings…", "command": lambda: self._act("manage_recordings")})
        if options.get("logs"):
            items.append({"label": "Manage logs…", "command": lambda: self._act("manage_logs")})
        if items:
            items.append(None)
        has_sessions = any(not session.is_empty for session in self.store.sessions)
        items.append({"label": "Delete all sessions…", "command": self.delete_all_sessions,
                      "enabled": has_sessions and not self.locked})
        popup_menu(self.data_button, items)

    # =================================================================== tabs
    def show_tab(self, tab_id):
        if not self.tabs.has(tab_id):
            tab_id = "note"
        self.tabs.select(tab_id)
        self._on_tab_selected(tab_id)

    def _on_tab_clicked(self, tab_id):
        if self.current is not None and self.current.is_empty:
            self._start_dismissed = self.current.id
        self._on_tab_selected(tab_id)
        self._refresh_start_screen()

    def _on_tab_selected(self, tab_id):
        page = self._pages.get(tab_id)
        if page is not None:
            page.tkraise()
        self._layout_actions()
        self._refresh_toolbar()

    def current_tab(self):
        return self.tabs.current or "transcript"

    def current_page(self) -> TextArea:
        return self._pages.get(self.current_tab(), self.transcript)

    def _refresh_toolbar(self):
        session = self.current
        if session is None or not hasattr(self, "ask_entry"):
            return
        self._refresh_start_screen()
        has_transcript = bool(session.transcript.strip())
        idle = not self.locked and not self.is_recording
        self._fit_toolbar()
        self.generate_button.set_enabled(idle and has_transcript)
        self.add_doc_button.set_enabled(idle and has_transcript)
        if hasattr(self, "fill_form_button"):
            # Always available when idle: an empty session opens the form to fill by hand
            self.fill_form_button.set_enabled(idle)
        self.template_button.set_enabled(idle)
        has_text = bool(self.current_page().get_text().strip())
        self.copy_button.set_enabled(has_text)
        self.export_button.set_enabled(has_text)
        for tab_id, value in (("context", session.context), ("transcript", session.transcript),
                              ("note", session.note)):
            self.tabs.set_done(tab_id, bool(value.strip()))
        target = self.ask_target()
        can_ask = idle and target is not None
        on_document = self.current_tab() == "note" or self.current_tab().startswith("doc:")
        show_chips = can_ask and on_document
        if show_chips != bool(self.suggestions.winfo_manager()):
            if show_chips:
                self.suggestions.pack(fill="x", pady=(0, Theme.px(8)), before=self.ask_frame)
            else:
                self.suggestions.pack_forget()
        self.ask_send.set_enabled(can_ask)
        self.ask_entry.configure(state="normal" if can_ask else "disabled")
        self.ask_entry.set_placeholder(ASK_PLACEHOLDER if (target is not None or not idle) else ASK_PLACEHOLDER_EMPTY)

    def copy_current(self):
        page = self.current_page()
        if not page.get_text().strip():
            return
        page.copy_all()
        label = {"context": "Context", "transcript": "Transcript", "note": "Note"}.get(self.current_tab(), "Document")
        self.toast(f"{label} copied to clipboard")

    # -------------------------------------------------------------- templates
    def _template_selector(self):
        """Hidden selector widget, reused for its add / edit / delete dialogs."""
        selector = getattr(self, "_selector", None)
        if selector is None:
            holder = tk.Frame(self.root, bg=Palette.BG)
            selector = NoteStyleSelector(self.root, holder)
            self._selector = selector
        selector.style_var.set(NoteStyleSelector.current_style)
        return selector

    def _open_template_menu(self, anchor=None):
        if self.locked:
            return
        current = NoteStyleSelector.current_style
        default = NoteStyleSelector.default_style
        items = [{"label": "Note template", "header": True}]
        for name in NoteStyleSelector.template_names():
            shown = name.replace(" - Default", "")
            items.append({"label": f"{shown}   (default)" if name == default else shown,
                          "checked": name == current, "command": lambda n=name: self._choose_template(n)})
        # Unticked, so it lines up with the template names
        items.append({"label": f"Make \u201c{current.replace(' - Default', '')}\u201d the default",
                      "checked": False, "command": self._make_default_template, "enabled": current != default})
        items.append(None)
        items.append({"label": "New template…", "command": self._add_template})
        protected = NoteStyleSelector.is_protected(current)
        items.append({"label": "View current template…" if protected else "Edit current template…",
                      "command": self._edit_template})
        items.append({"label": "Delete current template…", "command": self._delete_template,
                      "enabled": not protected})
        items.append(None)
        items.append({"label": "Level of detail", "header": True})
        level = self.detail_level()
        for name in DETAIL_LEVELS:
            items.append({"label": name, "checked": name == level,
                          "command": lambda n=name: self._choose_detail(n)})
        popup_menu(anchor or self.template_button, items, align="right")

    def detail_level(self) -> str:
        level = DEFAULT_DETAIL_LEVEL
        if self.ui_state is not None:
            level = self.ui_state.get("detail_level", DEFAULT_DETAIL_LEVEL)
        return level if level in DETAIL_LEVELS else DEFAULT_DETAIL_LEVEL

    def _choose_detail(self, level):
        if self.ui_state is not None:
            self.ui_state.set("detail_level", level)
        self.toast(f"Level of detail: {level}")

    def _choose_template(self, name):
        if NoteStyleSelector.select_style(name):
            self._apply_note_mode()
            if hasattr(self, "start_screen") and self.start_screen.winfo_manager():
                self.start_screen.refresh_templates()
            if name == NoteStyleSelector.NO_NOTE_STYLE:
                self.toast("Sessions will be transcribed without creating a note", icon_name="info")
            elif self.current is not None and self.current.note.strip():
                self.toast(f"Template set to {name}. Regenerate to apply it.", icon_name="doc")

    def _make_default_template(self):
        name = NoteStyleSelector.current_style
        if NoteStyleSelector.set_default(name):
            self.toast(f"New sessions will use {name.replace(' - Default', '')}")

    def _start_with_default_template(self):
        """A new session starts with the default template."""
        NoteStyleSelector.use_default()
        self._apply_note_mode()

    def _add_template(self):
        selector = self._template_selector()
        name = selector.add_style()
        if name:
            NoteStyleSelector.select_style(name)
            self.toast(f"Template “{name}” added")
        self._refresh_toolbar()

    def _edit_template(self):
        self._template_selector().edit_style()
        self._refresh_toolbar()

    def _delete_template(self):
        self._template_selector().delete_style()
        self._refresh_toolbar()

    # -------------------------------------------------------------- documents
    def _open_document_menu(self):
        if self.locked or self.current is None:
            return
        items = [{"label": "Create a document from this session", "header": True}]
        for name in NoteStyleSelector.template_names():
            items.append({"label": name.replace(" - Default", ""),
                          "command": lambda n=name: self._act("generate_document", n)})
        popup_menu(self.add_doc_button, items)

    def _rebuild_document_tabs(self):
        for tab_id in [t for t in self.tabs.ids() if t.startswith("doc:")]:
            self.tabs.remove(tab_id)
        for area in self._doc_areas.values():
            area.destroy()
        self._doc_areas = {}
        self._pages = {"context": self.context, "transcript": self.transcript, "note": self.note}
        if self.current is not None:
            for document in self.current.documents:
                self._add_document_tab(document)

    def _add_document_tab(self, document: Document, select=False):
        tab_id = f"doc:{document.id}"
        area = TextArea(self.pages, placeholder="", padx=32, reading=True, on_section_copy=self._section_copied,
                        on_change=lambda value, d=document: self._update_document(d, value))
        area._on_change = (lambda value, d=document, a=area: (self._update_document(d, value),
                                                              self._schedule_drug_check(a)))
        area.on_flag_ignore = self._ignore_word
        area.grid(row=0, column=0, sticky="nsew")
        area.set_text(document.text)
        area.set_readonly(self.locked)
        self._doc_areas[document.id] = area
        self._pages[tab_id] = area
        self.tabs.add(tab_id, _ellipsize(document.title, Theme.font("base", "medium"), Theme.px(150)),
                      icon_name="doc", closable=True)
        if select:
            self.show_tab(tab_id)
        else:
            current_page = self._pages.get(self.current_tab())
            if current_page is not None:
                current_page.tkraise()

    def _update_document(self, document, value):
        if document.text == value or self.current is None or document not in self.current.documents:
            return
        document.text = value
        self._touch(self.current)

    def _on_document_close(self, tab_id):
        if self.locked or self.current is None:
            return
        document = self.current.find_document(tab_id.split(":", 1)[1])
        if document is None:
            return
        if document.text.strip() and not messagebox.askyesno(
                "Remove document", f"Remove “{document.title}” from this session?"):
            return
        self.current.documents.remove(document)
        area = self._doc_areas.pop(document.id, None)
        self._pages.pop(tab_id, None)
        was_selected = self.tabs.current == tab_id
        self.tabs.remove(tab_id)
        if area is not None:
            area.destroy()
        if was_selected:
            self.show_tab("note")
        self._touch(self.current)

    def add_document(self, session_id, title, template, text):
        """Attach a generated document to a session and show it."""
        session = self.store.get(session_id) or self.current
        if session is None:
            return None
        titles = {d.title for d in session.documents}
        unique, counter = title, 2
        while unique in titles:
            unique = f"{title} ({counter})"
            counter += 1
        document = Document(title=unique, text=text, template=template)
        session.documents.append(document)
        if session is self.current:
            self._add_document_tab(document, select=True)
        self._touch(session)
        return document

    # ------------------------------------------------------------------ export
    def _open_export_menu(self):
        popup_menu(self.export_button, [
            {"label": "Word document (.docx)", "command": lambda: self.export_current(".docx")},
            {"label": "PDF (.pdf)", "command": lambda: self.export_current(".pdf")},
            {"label": "Plain text (.txt)", "command": lambda: self.export_current(".txt")},
        ], align="right")

    def export_current(self, extension, path=None):
        """Save the visible tab to a file. ``path`` skips the save dialog (used by tests)."""
        session = self.current
        self.flush_edits()
        body = self.current_page().get_text()
        if session is None or not body.strip():
            return None
        tab_id = self.current_tab()
        if tab_id.startswith("doc:"):
            document = session.find_document(tab_id.split(":", 1)[1])
            kind = document.title if document else "Document"
        else:
            kind = {"context": "Context", "transcript": "Transcript"}.get(
                tab_id, (session.template or "Note").replace(" - Default", ""))
        if path is None:
            directory = (self.ui_state.get("export_dir") if self.ui_state is not None else None) or os.path.expanduser("~")
            if not os.path.isdir(directory):
                directory = os.path.expanduser("~")
            path = filedialog.asksaveasfilename(
                parent=self.root, title="Export", defaultextension=extension, initialdir=directory,
                initialfile=exporter.default_filename(session.title, kind) + extension,
                filetypes=[(exporter.FORMATS[extension], f"*{extension}")])
            if not path:
                return None
        moment = session.created_at
        subtitle = f"{moment.strftime('%B')} {moment.day}, {moment.year}  ·  {format_clock(moment)}"
        title = f"{session.title.strip()} — {kind}" if session.title.strip() else kind
        try:
            exporter.export(path, body, title=title, subtitle=subtitle)
        except OSError as error:
            logger.exception(f"Export failed: {error}")
            messagebox.showerror("Export failed", f"The file could not be saved.\n\n{error}")
            return None
        if self.ui_state is not None:
            self.ui_state.set("export_dir", os.path.dirname(os.path.abspath(path)))
        self.toast(f"Saved {os.path.basename(path)}", icon_name="download")
        return path

    # =============================================================== assistant
    def ask_target(self):
        """What the assistant bar would edit: the open document, otherwise the note."""
        session = self.current
        if session is None:
            return None
        tab_id = self.current_tab()
        if tab_id.startswith("doc:"):
            document = session.find_document(tab_id.split(":", 1)[1])
            if document is not None and document.text.strip():
                return {"kind": "document", "id": document.id, "session_id": session.id,
                        "text": document.text, "label": document.title}
            return None
        if session.note.strip():
            return {"kind": "note", "id": None, "session_id": session.id, "text": session.note, "label": "note"}
        return None

    def _use_suggestion(self, text):
        """A suggestion chip sends that edit straight away."""
        if self.ask_target() is None or self.locked or self.is_recording:
            self.toast("Create a note first, then ask for edits", icon_name="info")
            return
        self.ask_entry.set_value(text)
        self._submit_ask()

    def _submit_ask(self):
        instruction = self.ask_entry.get_value().strip()
        if not instruction or self.locked:
            return
        self.flush_edits()
        target = self.ask_target()
        if target is None:
            self.toast("Create a note first, then ask for edits", icon_name="info")
            return
        self.ask_entry.set_value("")
        self.root.focus_set()
        self._act("ask", instruction, target)

    def apply_revision(self, target, text):
        """Replace the note or document the assistant was asked to edit."""
        session = self.store.get(target.get("session_id")) or self.current
        if session is None:
            return
        if target.get("kind") == "document":
            document = session.find_document(target.get("id"))
            if document is None:
                return
            document.text = text
            area = self._doc_areas.get(document.id) if session is self.current else None
            tab_id = f"doc:{document.id}"
        else:
            session.note = text
            area = self.note if session is self.current else None
            tab_id = "note"
        if area is not None:
            area.set_text(text, undoable=True)
            self.show_tab(tab_id)
            self.toast("Updated", action_text="Undo", action=area.undo, duration=6000)
        self._touch(session)

    # =========================================================== capture flow
    def begin_capture(self, resume=False):
        """Prepare a session to receive a new transcript.

        A session that already has a transcript or note is left untouched and a
        new one is started, unless ``resume`` asks to continue in the same one.
        """
        self.flush_edits()
        session = self.current
        if session is None or (session.has_output and not resume):
            self.store.prune_empty()
            session = self.store.new()
            self.select_session(session)
            self._start_with_default_template()
            self._rebuild_session_list()
        if session.is_empty:
            session.created = datetime.datetime.now().replace(microsecond=0).isoformat()
            self._refresh_meta()
        self._capture_prefix = session.transcript.rstrip()
        if self._capture_prefix:
            self.transcript.set_text(self._capture_prefix + "\n")
        self.show_tab("transcript")
        return session

    def append_transcript(self, text):
        """Add a piece of live transcription."""
        self.transcript.append(text)

    def set_transcript_result(self, text):
        """Set the transcript produced from a whole recording or uploaded file."""
        prefix = self._capture_prefix
        combined = f"{prefix}\n{text.strip()}" if prefix else (text or "").strip()
        self.transcript.set_text(combined)
        self.transcript.flush_change()
        self.set_transcript_status(None)

    def set_transcript_status(self, text=None):
        """Temporarily replace the transcript's hint text, e.g. while audio is processed."""
        self.transcript.set_placeholder(text or TRANSCRIPT_PLACEHOLDER)

    def apply_note(self, session_id, text, template=""):
        """Store a freshly generated note on its session and show it."""
        session = self.store.get(session_id) or self.current
        if session is None:
            return None
        was_empty = session.is_empty
        session.note = text
        session.template = template or session.template
        if session is self.current:
            self.note.set_text(text, undoable=True)
            self.show_tab("note")
            self.check_negatives()
        self._touch(session, was_empty)
        self._schedule_list_rebuild(delay=10)
        return session

    def check_negatives(self):
        """
        Small models sometimes leave out what the patient said no to ("Any chest pain?" "No.").
        Offer to add those to the note; nothing is added without the clinician's click.
        """
        session = self.current
        self.hide_warning(kind="negatives")
        if session is None or not session.note.strip():
            return []
        missing = missing_denials(session.note, session.transcript)
        if missing:
            count = len(missing)
            listed = "; ".join(missing)
            self.show_warning(f"The note doesn't mention {count} thing{'s' if count != 1 else ''} the patient "
                              f"said no to: {listed}.", closable=True, kind="negatives",
                              action_text="Add to note", action=lambda: self._add_negatives(session, missing))
        return missing

    def _add_negatives(self, session, phrases):
        self.hide_warning(kind="negatives")
        if session is not self.current or self.locked:
            return
        self.flush_edits()
        text = add_denials_to_note(session.note, phrases)
        session.note = text
        self.note.set_text(text, undoable=True)
        self.show_tab("note")
        self._touch(session)
        self.toast("Added to the note", action_text="Undo", action=self.note.undo, duration=6000)
