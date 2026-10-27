"""
Controls.py

Reusable themed controls for the FreeScribe workspace.

Tk's stock buttons cannot be rounded or recoloured consistently across Windows,
macOS and Linux, so the controls here draw their own backgrounds (smooth rounded
rectangles rendered with Pillow) on a canvas and let Tk draw the text on top,
which keeps the lettering as sharp as any native label.

Contents:
    rounded_image   cached rounded-rectangle image
    Button          text / icon button with primary, secondary, neutral, ghost and danger looks
    RoundedFrame    container with a rounded, optionally outlined background
    PlaceholderEntry  single-line entry with hint text
    TextArea        multi-line editor with placeholder, quiet scrollbar and heading highlighting
    ScrollFrame     vertically scrolling container
    Tabs            underlined tab strip with optional closable tabs
    Chip            small rounded status label
    LevelMeter      live microphone level bars
    Tooltip, Toast  transient hints and confirmations
    popup_menu      build and post a themed drop-down menu

This software is released under the AGPL-3.0 license.
"""

import re
import sys
import tkinter as tk

from PIL import Image, ImageDraw, ImageTk

from UI.Theme import Theme, Palette
from UI.Icons import icon

_image_cache = {}
_IMAGE_CACHE_LIMIT = 400


def _rgb(color):
    color = color.lstrip("#")
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))


def rounded_image(width, height, radius, fill=None, outline=None, outline_width=1):
    """Return a cached Tk image of a smooth rounded rectangle (device pixels).

    ``fill`` and ``outline`` are ``#rrggbb`` strings or ``None`` for transparent.
    """
    width, height = max(1, int(width)), max(1, int(height))
    radius = max(0, min(int(radius), width // 2, height // 2))
    key = (width, height, radius, fill, outline, outline_width)
    cached = _image_cache.get(key)
    if cached is not None:
        return cached

    # Supersample for smooth corners; large surfaces use a lower factor to stay fast.
    factor = 4 if width * height <= 160_000 else 2
    big = Image.new("RGBA", (width * factor, height * factor), (0, 0, 0, 0))
    draw = ImageDraw.Draw(big)
    box = [0, 0, width * factor - 1, height * factor - 1]
    if outline:
        draw.rounded_rectangle(box, radius=radius * factor, fill=_rgb(outline) + (255,))
        inset = outline_width * factor
        inner = [inset, inset, width * factor - 1 - inset, height * factor - 1 - inset]
        inner_fill = _rgb(fill) + (255,) if fill else (0, 0, 0, 0)
        if not fill:
            # Punch the middle out so only the ring remains.
            mask = Image.new("L", big.size, 0)
            ImageDraw.Draw(mask).rounded_rectangle(inner, radius=max(0, radius * factor - inset), fill=255)
            big.paste((0, 0, 0, 0), mask=mask)
        else:
            draw.rounded_rectangle(inner, radius=max(0, radius * factor - inset), fill=inner_fill)
    elif fill:
        draw.rounded_rectangle(box, radius=radius * factor, fill=_rgb(fill) + (255,))

    photo = ImageTk.PhotoImage(big.resize((width, height), Image.LANCZOS))
    if len(_image_cache) >= _IMAGE_CACHE_LIMIT:
        # Drop the oldest entries; anything still displayed is held by its widget.
        for stale in list(_image_cache)[:_IMAGE_CACHE_LIMIT // 4]:
            del _image_cache[stale]
    _image_cache[key] = photo
    return photo


def _bg_of(widget, default=Palette.BG):
    try:
        return widget.cget("bg")
    except tk.TclError:
        return default


# --------------------------------------------------------------------- Button
class Button(tk.Canvas):
    """A themed push button.

    :param variant: ``primary``, ``secondary``, ``neutral``, ``ghost``, ``danger`` or ``danger-ghost``
    :param size: ``sm``, ``md`` or ``lg``
    :param icon_name: optional leading icon (see :mod:`UI.Icons`)
    :param trailing_icon: optional icon after the label (for example ``chevron-down``)
    :param stretch: fill the width given by the geometry manager instead of hugging the label
    :param align: ``center`` or ``left`` (used with ``stretch``)
    :param pill: fully rounded ends
    """

    _SIZES = {
        "xs": dict(height=24, pad=7, font=("small", "medium"), icon=13, radius=6, gap=5),
        "sm": dict(height=30, pad=10, font=("small", "medium"), icon=14, radius=7, gap=6),
        "md": dict(height=34, pad=13, font=("base", "medium"), icon=16, radius=8, gap=7),
        "lg": dict(height=40, pad=17, font=("medium", "semibold"), icon=17, radius=10, gap=8),
    }

    def __init__(self, parent, text="", command=None, variant="neutral", size="md", icon_name=None,
                 trailing_icon=None, stretch=False, align="center", pill=False, tooltip=None,
                 surface=None, min_width=0, **kwargs):
        self._surface = surface or _bg_of(parent)
        super().__init__(parent, bg=self._surface, highlightthickness=0, bd=0, takefocus=1,
                         cursor="hand2", **kwargs)
        self._text = text
        self._command = command
        self._variant = variant
        self._spec = self._SIZES[size]
        self._icon_name = icon_name
        self._trailing = trailing_icon
        self._stretch = stretch
        self._align = align
        self._pill = pill
        self._min_width = Theme.px(min_width)
        self._enabled = True
        self._hover = False
        self._pressed = False
        self._focused = False
        self._last_size = None
        self._font = Theme.font(*self._spec["font"])

        self._bg_item = self.create_image(0, 0, anchor="nw")
        self._icon_item = self.create_image(0, 0, anchor="w")
        self._text_item = self.create_text(0, 0, anchor="w", font=self._font)
        self._trail_item = self.create_image(0, 0, anchor="w")

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<FocusIn>", lambda e: self._set_focus(True))
        self.bind("<FocusOut>", lambda e: self._set_focus(False))
        self.bind("<Key-Return>", lambda e: self.invoke())
        self.bind("<Key-space>", lambda e: self.invoke())
        if stretch:
            self.bind("<Configure>", self._on_configure)
        self._tooltip = Tooltip(self, tooltip) if tooltip else None
        self._render()

    # ------------------------------------------------------------ public API
    def set(self, text=None, icon_name=False, variant=None, command=None, trailing_icon=False, tooltip=None):
        """Change any of the button's properties and redraw."""
        if text is not None:
            self._text = text
        if icon_name is not False:
            self._icon_name = icon_name
        if trailing_icon is not False:
            self._trailing = trailing_icon
        if variant is not None:
            self._variant = variant
        if command is not None:
            self._command = command
        if tooltip is not None:
            if self._tooltip:
                self._tooltip.text = tooltip
            else:
                self._tooltip = Tooltip(self, tooltip)
        self._render()

    def set_enabled(self, enabled: bool):
        enabled = bool(enabled)
        if enabled == self._enabled:
            return
        self._enabled = enabled
        self._pressed = False
        if not enabled:
            self._hover = False
        self.configure(cursor="hand2" if enabled else "arrow", takefocus=1 if enabled else 0)
        self._render()

    @property
    def enabled(self):
        return self._enabled

    @property
    def text(self):
        return self._text

    def invoke(self):
        """Run the button's command if it is enabled."""
        if self._enabled and self._command:
            return self._command()
        return None

    # --------------------------------------------------------------- drawing
    def _colors(self):
        c = Palette
        v = self._variant
        if not self._enabled:
            if v in ("ghost", "ghost-strong", "danger-ghost"):
                return None, None, c.DISABLED_TEXT
            return c.DISABLED_BG, None, c.DISABLED_TEXT
        state = "press" if self._pressed else "hover" if self._hover else "idle"
        table = {
            "primary": {"idle": (c.PRIMARY, None), "hover": (c.PRIMARY_HOVER, None), "press": (c.PRIMARY_PRESSED, None), "fg": c.TEXT_ON_PRIMARY},
            "danger": {"idle": (c.DANGER, None), "hover": (c.DANGER_HOVER, None), "press": (c.DANGER_PRESSED, None), "fg": c.TEXT_ON_PRIMARY},
            "secondary": {"idle": (c.BG, c.BORDER_STRONG), "hover": (c.SURFACE, c.TEXT_FAINT), "press": (c.RAISED, c.TEXT_FAINT), "fg": c.TEXT},
            "neutral": {"idle": (c.RAISED, None), "hover": (c.HOVER, None), "press": (c.PRESSED, None), "fg": c.TEXT},
            "soft": {"idle": (c.PRIMARY_SOFT, None), "hover": (c.PRIMARY_SOFT_HOVER, None), "press": (c.PRIMARY_SOFT_HOVER, None), "fg": c.PRIMARY_PRESSED},
            "ghost": {"idle": (None, None), "hover": (c.HOVER, None), "press": (c.PRESSED, None), "fg": c.TEXT_MUTED if state == "idle" else c.TEXT},
            "ghost-strong": {"idle": (None, None), "hover": (c.HOVER, None), "press": (c.PRESSED, None), "fg": c.TEXT},
            "danger-ghost": {"idle": (None, None), "hover": (c.DANGER_SOFT, None), "press": (c.DANGER_SOFT, None), "fg": c.DANGER},
        }
        entry = table.get(v, table["neutral"])
        fill, outline = entry[state]
        return fill, outline, entry["fg"]

    def _natural_width(self):
        spec = self._spec
        pad, gap = Theme.px(spec["pad"]), Theme.px(spec["gap"])
        icon_px = Theme.px(spec["icon"])
        width = 0
        if self._icon_name:
            width += icon_px
        if self._text:
            if width:
                width += gap
            width += self._font.measure(self._text)
        if self._trailing:
            if width:
                width += gap - Theme.px(1)
            width += Theme.px(spec["icon"] - 2)
        if not self._text:
            # Icon-only buttons are square.
            return max(Theme.px(spec["height"]), width + 2 * Theme.px(max(spec["pad"] - 4, 5)))
        return max(width + 2 * pad, self._min_width)

    def _render(self):
        spec = self._spec
        height = Theme.px(spec["height"])
        natural = self._natural_width()
        width = natural
        if self._stretch:
            current = self.winfo_width()
            width = current if current > 1 else natural
        if self._last_size != (natural, height):
            self._last_size = (natural, height)
            self.configure(width=natural, height=height)

        fill, outline, fg = self._colors()
        radius = height // 2 if self._pill else Theme.px(spec["radius"])
        if self._focused and self._enabled:
            ring = Palette.PRIMARY if self._variant not in ("primary",) else Palette.PRIMARY_PRESSED
            background = rounded_image(width, height, radius, fill or self._surface, ring, Theme.px(2))
        elif fill or outline:
            background = rounded_image(width, height, radius, fill, outline, max(1, Theme.px(1)))
        else:
            background = ""
        self.itemconfigure(self._bg_item, image=background)
        self._bg_ref = background

        pad, gap = Theme.px(spec["pad"]), Theme.px(spec["gap"])
        icon_px = Theme.px(spec["icon"])
        trail_px = Theme.px(spec["icon"] - 2)
        content = 0
        if self._icon_name:
            content += icon_px
        if self._text:
            content += (gap if content else 0) + self._font.measure(self._text)
        if self._trailing:
            content += (gap - Theme.px(1) if content else 0) + trail_px

        if self._align == "left" and self._text:
            x = pad
        else:
            x = (width - content) / 2
        mid = height / 2

        if self._icon_name:
            self.itemconfigure(self._icon_item, image=icon(self._icon_name, spec["icon"], fg), state="normal")
            self.coords(self._icon_item, x, mid)
            x += icon_px + gap
        else:
            self.itemconfigure(self._icon_item, state="hidden")

        if self._text:
            self.itemconfigure(self._text_item, text=self._text, fill=fg, state="normal")
            self.coords(self._text_item, x, mid)
            x += self._font.measure(self._text) + gap - Theme.px(1)
        else:
            self.itemconfigure(self._text_item, state="hidden")

        if self._trailing:
            if self._stretch and self._align == "left":
                x = width - pad - trail_px
            self.itemconfigure(self._trail_item, image=icon(self._trailing, spec["icon"] - 2, fg), state="normal")
            self.coords(self._trail_item, x, mid)
        else:
            self.itemconfigure(self._trail_item, state="hidden")

    # ---------------------------------------------------------------- events
    def _on_configure(self, event):
        self._render()

    def _on_enter(self, event):
        if self._enabled:
            self._hover = True
            self._render()

    def _on_leave(self, event):
        self._hover = False
        self._pressed = False
        self._render()

    def _on_press(self, event):
        if self._enabled:
            self._pressed = True
            self._render()

    def _on_release(self, event):
        was_pressed = self._pressed
        self._pressed = False
        self._render()
        inside = 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height()
        if was_pressed and inside:
            self.invoke()

    def _set_focus(self, focused):
        self._focused = focused
        self._render()


# --------------------------------------------------------------- RoundedFrame
class RoundedFrame(tk.Canvas):
    """A container with a rounded background. Put children in ``.body``."""

    def __init__(self, parent, fill=Palette.BG, outline=Palette.BORDER_STRONG, radius=10,
                 padx=10, pady=6, surface=None, hug=False, **kwargs):
        self._surface = surface or _bg_of(parent)
        super().__init__(parent, bg=self._surface, highlightthickness=0, bd=0, **kwargs)
        self._fill, self._outline, self._radius = fill, outline, radius
        self._padx, self._pady = Theme.px(padx), Theme.px(pady)
        self._outline_width = 1
        self._hug = hug                 # size to the content's width instead of filling
        self.body = tk.Frame(self, bg=fill)
        self._bg_item = self.create_image(0, 0, anchor="nw")
        self._window = self.create_window(self._padx, self._pady, anchor="nw", window=self.body)
        self._size = None
        self._wanted_height = None
        self.bind("<Configure>", self._on_configure)
        self.body.bind("<Configure>", self._on_body_configure)

    def set_outline(self, color, width=1):
        if (color, width) != (self._outline, self._outline_width):
            self._outline, self._outline_width = color, width
            self._size = None
            self._redraw(self.winfo_width(), self.winfo_height())

    def _on_body_configure(self, event):
        wanted = self.body.winfo_reqheight() + 2 * self._pady
        if wanted != self._wanted_height:
            self._wanted_height = wanted
            self.configure(height=wanted)
        if self._hug:
            self.configure(width=self.body.winfo_reqwidth() + 2 * self._padx)

    def _on_configure(self, event):
        if not self._hug:
            self.itemconfigure(self._window, width=max(1, event.width - 2 * self._padx))
        self._redraw(event.width, event.height)

    def _redraw(self, width, height):
        if width <= 1 or height <= 1 or self._size == (width, height):
            return
        self._size = (width, height)
        self._image = rounded_image(width, height, Theme.px(self._radius), self._fill, self._outline,
                                    max(1, Theme.px(self._outline_width)))
        self.itemconfigure(self._bg_item, image=self._image)


# ------------------------------------------------------------------ Scrollbar
class Scrollbar(tk.Canvas):
    """Slim vertical scrollbar with a rounded thumb.

    Drop-in for the usual protocol: pass ``command=widget.yview`` and give
    ``scrollbar.set`` to the widget's ``yscrollcommand``.
    """

    THUMB = "#CFCFD2"
    THUMB_ACTIVE = "#AEAEB2"

    def __init__(self, parent, command=None, surface=None, width=8):
        self._surface = surface or _bg_of(parent)
        super().__init__(parent, width=Theme.px(width), bg=self._surface, highlightthickness=0, bd=0)
        self._command = command
        self._first, self._last = 0.0, 1.0
        self._hover = False
        self._drag_offset = None
        self._thumb_item = self.create_image(0, 0, anchor="nw")
        self._geometry = (0, 0)          # thumb top, thumb height
        self.bind("<Configure>", lambda e: self._redraw())
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)

    def set(self, first, last):
        first, last = float(first), float(last)
        if (first, last) != (self._first, self._last):
            self._first, self._last = first, last
            self._redraw()

    @property
    def needed(self):
        return self._first > 0.0 or self._last < 1.0

    def _set_hover(self, hover):
        self._hover = hover
        self._redraw()

    def _redraw(self):
        width, height = self.winfo_width(), self.winfo_height()
        span = max(0.0, min(1.0, self._last - self._first))
        if height <= 1 or span >= 1.0:
            self.itemconfigure(self._thumb_item, state="hidden")
            self._geometry = (0, 0)
            return
        inset = Theme.px(1)
        thumb_height = int(max(Theme.px(28), span * height))
        travel = max(1, height - thumb_height)
        top = int(round(self._first / max(1e-9, 1.0 - span) * travel))
        top = max(0, min(top, travel))
        color = self.THUMB_ACTIVE if (self._hover or self._drag_offset is not None) else self.THUMB
        thumb_width = max(2, width - 2 * inset)
        self._image = rounded_image(thumb_width, thumb_height, thumb_width // 2, color)
        self.itemconfigure(self._thumb_item, image=self._image, state="normal")
        self.coords(self._thumb_item, inset, top)
        self._geometry = (top, thumb_height)

    def _on_press(self, event):
        top, thumb_height = self._geometry
        if not thumb_height or self._command is None:
            return
        if top <= event.y <= top + thumb_height:
            self._drag_offset = event.y - top
            self._redraw()
        else:
            self._command("scroll", -1 if event.y < top else 1, "pages")

    def _on_drag(self, event):
        if self._drag_offset is None or self._command is None:
            return
        _, thumb_height = self._geometry
        travel = max(1, self.winfo_height() - thumb_height)
        span = max(0.0, min(1.0, self._last - self._first))
        fraction = (event.y - self._drag_offset) / travel * (1.0 - span)
        self._command("moveto", max(0.0, min(fraction, 1.0)))

    def _on_release(self, event):
        self._drag_offset = None
        self._redraw()


# ----------------------------------------------------------- PlaceholderEntry
class PlaceholderEntry(tk.Entry):
    """Borderless single-line entry that shows greyed hint text while empty.

    The hint is never returned by :meth:`get_value`.
    """

    def __init__(self, parent, placeholder="", on_change=None, on_submit=None, font=None, bg=None,
                 fg=Palette.TEXT, placeholder_fg=Palette.TEXT_FAINT, **kwargs):
        self._var = tk.StringVar()
        bg = bg or _bg_of(parent)
        super().__init__(parent, textvariable=self._var, relief="flat", bd=0, highlightthickness=0,
                         bg=bg, fg=fg, insertbackground=fg, font=font or Theme.font("base"),
                         disabledbackground=bg, disabledforeground=Palette.TEXT_MUTED,
                         selectbackground=Palette.PRIMARY_SOFT_HOVER, selectforeground=Palette.TEXT, **kwargs)
        self._placeholder = placeholder
        self._fg, self._placeholder_fg = fg, placeholder_fg
        self._on_change, self._on_submit = on_change, on_submit
        self._showing_placeholder = False
        self._internal = False
        self._var.trace_add("write", self._on_write)
        self.bind("<FocusIn>", self._on_focus_in, add="+")
        self.bind("<FocusOut>", self._on_focus_out, add="+")
        self.bind("<Return>", self._on_return, add="+")
        self._show_placeholder_if_empty()

    def get_value(self) -> str:
        return "" if self._showing_placeholder else self._var.get()

    def set_value(self, value: str):
        self._internal = True
        try:
            self._showing_placeholder = False
            self.configure(fg=self._fg)
            self._var.set(value or "")
        finally:
            self._internal = False
        if self.focus_get() is not self:
            self._show_placeholder_if_empty()

    def set_placeholder(self, placeholder: str):
        self._placeholder = placeholder
        if self._showing_placeholder:
            self._internal = True
            try:
                self._var.set(placeholder)
            finally:
                self._internal = False

    def _show_placeholder_if_empty(self):
        if self._var.get() == "" and self._placeholder:
            self._internal = True
            try:
                self._showing_placeholder = True
                self.configure(fg=self._placeholder_fg)
                self._var.set(self._placeholder)
            finally:
                self._internal = False

    def _on_focus_in(self, event=None):
        if self._showing_placeholder:
            self._internal = True
            try:
                self._showing_placeholder = False
                self.configure(fg=self._fg)
                self._var.set("")
            finally:
                self._internal = False

    def _on_focus_out(self, event=None):
        self._show_placeholder_if_empty()

    def _on_write(self, *args):
        if self._internal or self._showing_placeholder:
            return
        if self._on_change:
            self._on_change(self._var.get())

    def _on_return(self, event=None):
        if self._on_submit:
            self._on_submit(self.get_value())
            return "break"
        return None


# ------------------------------------------------------------------- TextArea
_HEADING_RE = re.compile(
    r"^[ \t]*(?:#{1,6}[ \t]+\S.*|\*\*[^*\n]{1,60}\*\*:?|[A-Z][A-Za-z0-9 /&,()'-]{1,48}:)[ \t]*$",
    re.MULTILINE,
)


class TextArea(tk.Frame):
    """Borderless multi-line editor with placeholder text and a quiet scrollbar.

    The placeholder is an overlay, never part of the content, so ``get_text``
    only ever returns what the user (or the app) actually put there.
    """

    # Longest comfortable line in the reading layout, in pixels before scaling
    READING_WIDTH = 760

    def __init__(self, parent, placeholder="", on_change=None, highlight_headings=False,
                 padx=28, pady=18, bg=Palette.BG, font=None, height=8, reading=False, on_section_copy=None,
                 comfortable=False):
        """
        :param reading: note layout: section headings in the accent colour with a Copy button each,
            roomier lines, and lines no longer than :attr:`READING_WIDTH`
        :param on_section_copy: called with the heading after a section was copied
        :param comfortable: the reading layout's type and line length, without the section buttons
        """
        super().__init__(parent, bg=bg)
        self._on_change = on_change
        self._reading = reading
        self._on_section_copy = on_section_copy
        self._sections = []          # (heading text, heading start index, body text)
        self._section_buttons = []
        self._highlight = highlight_headings or reading
        self._readonly = False
        self._change_job = None
        self._last_text = ""

        self.text = tk.Text(
            self, wrap="word", undo=True, autoseparators=True, maxundo=-1, height=height,
            bg=bg, fg=Palette.TEXT, insertbackground=Palette.TEXT, relief="flat", bd=0,
            highlightthickness=0, padx=Theme.px(padx), pady=Theme.px(pady),
            font=font or Theme.font("medium"), spacing1=Theme.px(1), spacing3=Theme.px(4),
            selectbackground=Palette.PRIMARY_SOFT_HOVER, selectforeground=Palette.TEXT,
            inactiveselectbackground=Palette.RAISED, insertwidth=Theme.px(2), exportselection=False,
        )
        self.scrollbar = Scrollbar(self, command=self.text.yview, surface=bg)
        self.text.configure(yscrollcommand=self._on_scroll)
        self.text.grid(row=0, column=0, sticky="nsew")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self._scroll_visible = False

        self.text.tag_configure("heading", font=Theme.font("medium", "semibold"), spacing1=Theme.px(8))
        # Words that look like a misheard drug name
        self.text.tag_configure("flag", underline=True, foreground=Palette.WARNING)
        self.text.tag_bind("flag", "<Button-1>", self._on_flag_click)
        self.text.tag_bind("flag", "<Enter>", lambda e: self.text.configure(cursor="hand2"))
        self.text.tag_bind("flag", "<Leave>", lambda e: self.text.configure(cursor="xterm"))
        self._flags = []
        self.on_flag_ignore = None
        self._comfortable = comfortable or reading
        if self._comfortable:
            self.text.configure(font=font or Theme.font("large"), spacing1=Theme.px(2), spacing2=Theme.px(3),
                                spacing3=Theme.px(3))
            self.text.tag_configure("measure", rmargin=0)
        if reading:
            self.text.tag_configure("heading", font=Theme.font("base", "semibold"),
                                    foreground=Palette.PRIMARY_PRESSED, spacing1=Theme.px(10), spacing3=Theme.px(4))
            self.text.tag_configure("measure", rmargin=0)
        self.text.tag_configure("status", foreground=Palette.TEXT_MUTED)

        self._placeholder = tk.Label(self.text, text=placeholder, bg=bg, fg=Palette.TEXT_FAINT,
                                     font=font or Theme.font("large" if self._comfortable else "medium"),
                                     justify="left", anchor="nw", cursor="xterm")
        self._placeholder.bind("<Button-1>", lambda e: self.text.focus_set())
        self._placeholder_shown = False
        self._padx, self._pady = Theme.px(padx), Theme.px(pady)

        self.text.bind("<<Modified>>", self._on_modified)
        self.text.bind("<Configure>", self._on_text_configure)
        self._refresh_placeholder()

    # ------------------------------------------------------------ public API
    def get_text(self) -> str:
        return self.text.get("1.0", "end-1c")

    def set_text(self, value: str, undoable=False):
        """Replace the whole content. With ``undoable`` the change can be reverted with Ctrl+Z."""
        value = value or ""
        previous = str(self.text.cget("state"))
        self.text.configure(state="normal")
        if undoable:
            # One undo step for the whole replacement.
            self.text.configure(autoseparators=False)
            self.text.edit_separator()
        self.text.delete("1.0", "end")
        if value:
            self.text.insert("1.0", value)
        if undoable:
            self.text.edit_separator()
            self.text.configure(autoseparators=True)
        else:
            self.text.edit_reset()
        self.text.configure(state=previous)
        self.text.yview_moveto(0)
        self._after_programmatic_change()

    def append(self, value: str, tag=None):
        """Append text and keep the end in view (used for live transcription)."""
        if not value:
            return
        previous = str(self.text.cget("state"))
        self.text.configure(state="normal")
        self.text.insert("end", value, (tag,) if tag else ())
        self.text.configure(state=previous)
        self.text.see("end")
        self._after_programmatic_change()

    def set_readonly(self, readonly: bool):
        self._readonly = bool(readonly)
        self.text.configure(state="disabled" if readonly else "normal")

    def set_placeholder(self, value: str):
        self._placeholder.configure(text=value)

    def focus_editor(self):
        self.text.focus_set()

    def undo(self) -> bool:
        """Undo the last change. Returns False when there is nothing to undo."""
        try:
            self.text.edit_undo()
        except tk.TclError:
            return False
        self._after_programmatic_change()
        return True

    def copy_all(self) -> str:
        value = self.get_text()
        try:
            self.clipboard_clear()
            self.clipboard_append(value)
        except tk.TclError:
            pass
        return value

    # ------------------------------------------------------------- internals
    def _after_programmatic_change(self):
        self._refresh_placeholder()
        self._schedule_change()

    def _on_modified(self, event=None):
        try:
            if not self.text.edit_modified():
                return
            self.text.edit_modified(False)
        except tk.TclError:
            return
        self._refresh_placeholder()
        self._schedule_change()

    def _schedule_change(self):
        if self._change_job is not None:
            try:
                self.after_cancel(self._change_job)
            except tk.TclError:
                pass
        self._change_job = self.after(120, self._emit_change)

    def _emit_change(self):
        self._change_job = None
        if not self.winfo_exists():
            return
        value = self.get_text()
        if self._highlight:
            self._apply_headings(value)
        elif self._comfortable:
            self.text.tag_add("measure", "1.0", "end")
        if value != self._last_text:
            self._last_text = value
            if self._on_change:
                self._on_change(value)

    def flush_change(self):
        """Deliver a pending change notification immediately."""
        if self._change_job is not None:
            try:
                self.after_cancel(self._change_job)
            except tk.TclError:
                pass
            self._emit_change()

    def _apply_headings(self, value):
        self.text.tag_remove("heading", "1.0", "end")
        matches = list(_HEADING_RE.finditer(value))
        for match in matches:
            self.text.tag_add("heading", f"1.0+{match.start()}c", f"1.0+{match.end()}c")
        if self._comfortable:
            self.text.tag_add("measure", "1.0", "end")
        if not self._reading:
            return
        self._sections = []
        for number, match in enumerate(matches):
            end = matches[number + 1].start() if number + 1 < len(matches) else len(value)
            heading = match.group(0).strip().strip("#*: ").strip()
            body = value[match.end():end].strip("\n")
            if body.strip():
                self._sections.append((heading, f"1.0+{match.start()}c", body))
        # Let Tk lay the lines out with the heading style first, so the buttons line up with them
        self.after(15, self._layout_section_buttons)

    def set_flags(self, flags):
        """Underline ``flags`` - (start, end, word, suggestions) character ranges in the text."""
        self._flags = list(flags)
        self.text.tag_remove("flag", "1.0", "end")
        for start, end, _word, _options in self._flags:
            self.text.tag_add("flag", f"1.0+{start}c", f"1.0+{end}c")

    def flags(self):
        return list(self._flags)

    def flag_items(self, number):
        """Menu items for flagged word ``number``: replace it with a suggestion, or leave it."""
        start, end, word, options = self._flags[number]
        items = [{"label": f"“{word}” may be a misheard drug name", "header": True}]
        for option in options:
            items.append({"label": f"Change to {option}",
                          "command": lambda o=option, s=start, e=end, w=word: self.replace_flag(s, e, w, o),
                          "enabled": not self._readonly})
        items.append(None)
        items.append({"label": f"“{word}” is right", "command": lambda w=word: self._ignore_flag(w)})
        return items

    def replace_flag(self, start, end, word, replacement):
        """Replace the flagged ``word`` (if it is still there) as one undoable edit."""
        first, last = f"1.0+{start}c", f"1.0+{end}c"
        if self._readonly or self.text.get(first, last) != word:
            return False
        if word.isupper():
            replacement = replacement.upper()
        elif word[:1].isupper():
            replacement = replacement[:1].upper() + replacement[1:]
        # One undo step for the whole swap
        self.text.configure(autoseparators=False)
        self.text.edit_separator()
        self.text.delete(first, last)
        self.text.insert(first, replacement)
        self.text.edit_separator()
        self.text.configure(autoseparators=True)
        self._after_programmatic_change()
        return True

    def _ignore_flag(self, word):
        if callable(self.on_flag_ignore):
            self.on_flag_ignore(word)

    def _on_flag_click(self, event):
        index = self.text.index(f"@{event.x},{event.y}")
        offset = len(self.text.get("1.0", index))
        for number, (start, end, _word, _options) in enumerate(self._flags):
            if start <= offset <= end:
                popup_menu(self.text, self.flag_items(number), at=(event.x_root, event.y_root + Theme.px(8)))
                return "break"
        return None

    def section_texts(self):
        """[(heading, body)] of the sections found in the reading layout."""
        return [(heading, body) for heading, _start, body in self._sections]

    def copy_section(self, number):
        heading, _start, body = self._sections[number]
        try:
            self.clipboard_clear()
            self.clipboard_append(body)
        except tk.TclError:
            pass
        if callable(self._on_section_copy):
            self._on_section_copy(heading)
        return body

    def _reading_right_edge(self):
        width = self.text.winfo_width()
        limit = self._padx + Theme.px(self.READING_WIDTH)
        return min(width - self._padx, limit)

    def _layout_section_buttons(self):
        """Keep a Copy button beside each section heading that is on screen."""
        if not self._reading or not self.winfo_exists():
            return
        while len(self._section_buttons) < len(self._sections):
            number = len(self._section_buttons)
            button = Button(self.text, text="Copy", icon_name="copy", variant="ghost", size="xs",
                            surface=self.text.cget("bg"), command=lambda n=number: self._copy_clicked(n),
                            tooltip="Copy this section")
            self._section_buttons.append(button)
        right = self._reading_right_edge()
        for number, button in enumerate(self._section_buttons):
            info = self.text.dlineinfo(self._sections[number][1]) if number < len(self._sections) else None
            if info is None:
                button.place_forget()
                continue
            _x, y, _w, height, baseline = info
            # Centre the button on the heading's text (the line box also holds the space above it)
            button.place(x=right, y=y + baseline - Theme.px(4), anchor="e", bordermode="outside")

    def _copy_clicked(self, number):
        if number < len(self._sections):
            self.copy_section(number)

    def _refresh_placeholder(self):
        empty = self.text.compare("end-1c", "==", "1.0")
        if empty and not self._placeholder_shown:
            # bordermode="outside": measure from the widget's edge. Tk otherwise adds the text
            # widget's own padding, which would indent the hint twice.
            self._placeholder.place(x=self._padx, y=self._pady, anchor="nw", bordermode="outside")
            self._placeholder_shown = True
        elif not empty and self._placeholder_shown:
            self._placeholder.place_forget()
            self._placeholder_shown = False

    def _on_text_configure(self, event):
        self._placeholder.configure(wraplength=max(120, event.width - 2 * self._padx))
        if self._comfortable:
            spare = event.width - 2 * self._padx - Theme.px(self.READING_WIDTH)
            self.text.tag_configure("measure", rmargin=max(0, spare))
            self._placeholder.configure(wraplength=max(120, min(event.width - 2 * self._padx,
                                                                Theme.px(self.READING_WIDTH))))
        if self._reading:
            self.after_idle(self._layout_section_buttons)

    def _on_scroll(self, first, last):
        self.scrollbar.set(first, last)
        if self._reading and self._sections:
            self.after_idle(self._layout_section_buttons)
        needed = float(first) > 0.0 or float(last) < 1.0
        if needed and not self._scroll_visible:
            self.scrollbar.grid(row=0, column=1, sticky="ns", padx=(0, Theme.px(4)), pady=Theme.px(6))
            self._scroll_visible = True
        elif not needed and self._scroll_visible:
            self.scrollbar.grid_remove()
            self._scroll_visible = False


# ---------------------------------------------------------------- ScrollFrame
class ScrollFrame(tk.Frame):
    """A vertically scrolling container. Put children in ``.body``."""

    def __init__(self, parent, bg=Palette.BG):
        super().__init__(parent, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.scrollbar = Scrollbar(self, command=self.canvas.yview, surface=bg, width=7)
        self.body = tk.Frame(self.canvas, bg=bg)
        self._window = self.canvas.create_window(0, 0, anchor="nw", window=self.body)
        self.canvas.configure(yscrollcommand=self._on_scroll)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self._scroll_visible = False

        self.body.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._window, width=e.width))
        self.bind("<Enter>", self._bind_wheel)
        self.bind("<Leave>", self._unbind_wheel)

    def scroll_to_top(self):
        self.canvas.yview_moveto(0)

    def scroll_to_bottom(self):
        self.canvas.update_idletasks()
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self.canvas.yview_moveto(1)

    def _on_scroll(self, first, last):
        self.scrollbar.set(first, last)
        needed = float(first) > 0.0 or float(last) < 1.0
        if needed and not self._scroll_visible:
            self.scrollbar.grid(row=0, column=1, sticky="ns", pady=Theme.px(4))
            self._scroll_visible = True
        elif not needed and self._scroll_visible:
            self.scrollbar.grid_remove()
            self._scroll_visible = False

    def _bind_wheel(self, event=None):
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)
        self.canvas.bind_all("<Button-4>", self._on_wheel)
        self.canvas.bind_all("<Button-5>", self._on_wheel)

    def _unbind_wheel(self, event=None):
        # Only release the bindings when the pointer really left this widget tree.
        try:
            x, y = self.winfo_pointerxy()
            under = self.winfo_containing(x, y)
        except (tk.TclError, KeyError):
            under = None
        widget = under
        while widget is not None:
            if widget is self:
                return
            widget = getattr(widget, "master", None)
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            try:
                self.canvas.unbind_all(sequence)
            except tk.TclError:
                pass

    def _on_wheel(self, event):
        if not self._scroll_visible or not self.canvas.winfo_exists():
            return
        if getattr(event, "num", None) == 4:
            step = -2
        elif getattr(event, "num", None) == 5:
            step = 2
        elif sys.platform == "darwin":
            step = -int(event.delta)
        else:
            step = -int(event.delta / 60) or (-1 if event.delta > 0 else 1)
        self.canvas.yview_scroll(step, "units")
        return "break"


# ----------------------------------------------------------------------- Tabs
class Tabs(tk.Frame):
    """Underlined tab strip. The right-hand side (``.actions``) is free for toolbar buttons."""

    def __init__(self, parent, on_select=None, on_close=None, bg=Palette.BG):
        super().__init__(parent, bg=bg)
        self._on_select, self._on_close = on_select, on_close
        self._tabs = {}
        self._order = []
        self.current = None
        row = tk.Frame(self, bg=bg)
        row.pack(fill="x")
        self.strip = tk.Frame(row, bg=bg)
        self.strip.pack(side="left", fill="y")
        self.trailing = tk.Frame(row, bg=bg)
        self.trailing.pack(side="left", fill="y", padx=(Theme.px(2), 0))
        self.actions = tk.Frame(row, bg=bg)
        self.actions.pack(side="right", fill="y")
        tk.Frame(self, bg=Palette.BORDER, height=1).pack(fill="x")

    def add(self, tab_id, label, icon_name=None, closable=False, select=False):
        if tab_id in self._tabs:
            self.rename(tab_id, label)
            if select:
                self.select(tab_id)
            return
        bg = self.cget("bg")
        frame = tk.Frame(self.strip, bg=bg, cursor="hand2")
        inner = tk.Frame(frame, bg=bg)
        inner.pack(side="top", padx=Theme.px(11), pady=(Theme.px(8), Theme.px(7)))
        widgets = [frame, inner]
        icon_label = None
        if icon_name:
            icon_label = tk.Label(inner, bg=bg, image=icon(icon_name, 15, Palette.TEXT_MUTED))
            icon_label.pack(side="left", padx=(0, Theme.px(6)))
            widgets.append(icon_label)
        text_label = tk.Label(inner, text=label, bg=bg, fg=Palette.TEXT_MUTED, font=Theme.font("base", "medium"))
        text_label.pack(side="left")
        widgets.append(text_label)
        close_button = None
        if closable:
            close_button = Button(inner, icon_name="close", variant="ghost", size="xs", surface=bg,
                                  command=lambda t=tab_id: self._on_close and self._on_close(t),
                                  tooltip="Remove this document")
            close_button.pack(side="left", padx=(Theme.px(4), 0))
        underline = tk.Frame(frame, bg=bg, height=Theme.px(2))
        underline.pack(side="bottom", fill="x", padx=Theme.px(8))
        frame.pack(side="left")
        for widget in widgets:
            widget.bind("<Button-1>", lambda e, t=tab_id: self.select(t, notify=True))
            widget.bind("<Enter>", lambda e, t=tab_id: self._hover(t, True))
            widget.bind("<Leave>", lambda e, t=tab_id: self._hover(t, False))
        self._tabs[tab_id] = dict(frame=frame, label=text_label, icon=icon_label, icon_name=icon_name,
                                  underline=underline, close=close_button)
        self._order.append(tab_id)
        if select or self.current is None:
            self.select(tab_id)

    def set_compact(self, compact):
        """Drop the tab icons when the strip is short of room."""
        compact = bool(compact)
        if compact == getattr(self, "_compact", False):
            return
        self._compact = compact
        for tab in self._tabs.values():
            if tab["icon"] is not None:
                if compact:
                    tab["icon"].pack_forget()
                else:
                    tab["icon"].pack(side="left", padx=(0, Theme.px(6)), before=tab["label"])

    def set_done(self, tab_id, done):
        """Show a tick instead of the tab's icon once that step has content."""
        tab = self._tabs.get(tab_id)
        if tab is not None and tab.get("done") != bool(done):
            tab["done"] = bool(done)
            self._paint(tab_id, hover=False)

    def rename(self, tab_id, label):
        if tab_id in self._tabs:
            self._tabs[tab_id]["label"].configure(text=label)

    def remove(self, tab_id):
        tab = self._tabs.pop(tab_id, None)
        if not tab:
            return
        tab["frame"].destroy()
        self._order.remove(tab_id)
        if self.current == tab_id:
            self.current = None

    def ids(self):
        return list(self._order)

    def has(self, tab_id):
        return tab_id in self._tabs

    def select(self, tab_id, notify=False):
        if tab_id not in self._tabs:
            return
        changed = tab_id != self.current
        self.current = tab_id
        for other, tab in self._tabs.items():
            self._paint(other, hover=False)
        if notify and changed and self._on_select:
            self._on_select(tab_id)

    def _hover(self, tab_id, hover):
        if tab_id in self._tabs:
            self._paint(tab_id, hover)

    def _paint(self, tab_id, hover):
        tab = self._tabs[tab_id]
        active = tab_id == self.current
        color = Palette.TEXT if (active or hover) else Palette.TEXT_MUTED
        tab["label"].configure(fg=color)
        if tab["icon"] is not None:
            if tab.get("done"):
                tab["icon"].configure(image=icon("check", 15, Palette.PRIMARY))
            else:
                tab["icon"].configure(image=icon(tab["icon_name"], 15, color))
        tab["underline"].configure(bg=Palette.PRIMARY if active else self.cget("bg"))


# ----------------------------------------------------------------------- Chip
class Chip(tk.Canvas):
    """Small rounded label used for status and metadata."""

    _TONES = {
        "neutral": (Palette.RAISED, Palette.TEXT_MUTED),
        "primary": (Palette.PRIMARY_SOFT, Palette.PRIMARY_PRESSED),
        "danger": (Palette.DANGER_SOFT, Palette.DANGER),
        "warning": (Palette.WARNING_SOFT, Palette.WARNING),
        "info": (Palette.INFO_SOFT, Palette.INFO),
        "plain": (None, Palette.TEXT_MUTED),
    }

    def __init__(self, parent, text="", tone="neutral", icon_name=None, dot=None, surface=None, command=None,
                 tooltip=None):
        self._surface = surface or _bg_of(parent)
        super().__init__(parent, bg=self._surface, highlightthickness=0, bd=0)
        self._text, self._tone, self._icon_name, self._dot = text, tone, icon_name, dot
        self._command = command
        self._hover = False
        self._font = Theme.font("small", "medium")
        self._bg_item = self.create_image(0, 0, anchor="nw")
        self._icon_item = self.create_image(0, 0, anchor="w")
        self._dot_item = self.create_oval(0, 0, 0, 0, width=0)
        self._text_item = self.create_text(0, 0, anchor="w", font=self._font)
        if command:
            self.configure(cursor="hand2")
            self.bind("<Button-1>", lambda e: self._command())
            self.bind("<Enter>", lambda e: self._set_hover(True))
            self.bind("<Leave>", lambda e: self._set_hover(False))
        self._tooltip = Tooltip(self, tooltip) if tooltip else None
        self._render()

    def set(self, text=None, tone=None, icon_name=False, dot=False):
        if text is not None:
            self._text = text
        if tone is not None:
            self._tone = tone
        if icon_name is not False:
            self._icon_name = icon_name
        if dot is not False:
            self._dot = dot
        self._render()

    def _set_hover(self, hover):
        self._hover = hover
        self._render()

    def _render(self):
        fill, fg = self._TONES.get(self._tone, self._TONES["neutral"])
        if self._command and self._hover:
            fill = Palette.HOVER if self._tone in ("plain", "neutral") else fill
            fg = Palette.TEXT if self._tone in ("plain", "neutral") else fg
        height = Theme.px(24)
        pad = Theme.px(9) if fill or self._command else 0
        gap = Theme.px(6)
        x = pad
        mid = height / 2
        if self._dot:
            radius = Theme.px(3.5)
            self.coords(self._dot_item, x, mid - radius, x + 2 * radius, mid + radius)
            self.itemconfigure(self._dot_item, fill=self._dot, state="normal")
            x += 2 * radius + gap
        else:
            self.itemconfigure(self._dot_item, state="hidden")
        if self._icon_name:
            self.itemconfigure(self._icon_item, image=icon(self._icon_name, 14, fg), state="normal")
            self.coords(self._icon_item, x, mid)
            x += Theme.px(14) + gap
        else:
            self.itemconfigure(self._icon_item, state="hidden")
        self.itemconfigure(self._text_item, text=self._text, fill=fg)
        self.coords(self._text_item, x, mid)
        width = int(x + self._font.measure(self._text) + pad)
        self.configure(width=max(1, width), height=height)
        self._image = rounded_image(width, height, height // 2, fill) if fill else ""
        self.itemconfigure(self._bg_item, image=self._image)


# ----------------------------------------------------------------- LevelMeter
class LevelMeter(tk.Canvas):
    """Animated bars showing the microphone input level (0.0 – 1.0)."""

    _PROFILE = (0.55, 0.85, 1.0, 0.7, 0.45)

    def __init__(self, parent, bars=5, height=22, surface=None):
        self._surface = surface or _bg_of(parent)
        self._bar_w, self._gap = Theme.px(3), Theme.px(3)
        self._h = Theme.px(height)
        width = bars * self._bar_w + (bars - 1) * self._gap
        super().__init__(parent, bg=self._surface, highlightthickness=0, bd=0, width=width, height=self._h)
        self._count = bars
        self._items = [self.create_rectangle(0, 0, 0, 0, width=0) for _ in range(bars)]
        self._levels = [0.0] * bars
        self._color = Palette.TEXT_FAINT
        self._tick = 0
        self.set_level(0.0)

    def set_color(self, color):
        self._color = color
        for item in self._items:
            self.itemconfigure(item, fill=color)

    def set_level(self, level: float):
        level = min(max(float(level), 0.0), 1.0)
        self._tick += 1
        min_h = self._bar_w
        for i, item in enumerate(self._items):
            shape = self._PROFILE[i % len(self._PROFILE)]
            # A little per-bar variation makes speech look alive rather than a block moving up and down.
            wobble = 0.82 + 0.18 * (((self._tick * (i + 3)) % 7) / 6.0)
            target = level * shape * wobble
            current = self._levels[i]
            current += (target - current) * (0.6 if target > current else 0.3)
            self._levels[i] = current
            bar_h = max(min_h, int(current * self._h))
            x0 = i * (self._bar_w + self._gap)
            y0 = (self._h - bar_h) / 2
            self.coords(item, x0, y0, x0 + self._bar_w, y0 + bar_h)
            self.itemconfigure(item, fill=self._color)


# -------------------------------------------------------------------- Tooltip
class Tooltip:
    """Shows a short hint after the pointer rests on a widget."""

    def __init__(self, widget, text, delay=550):
        self.widget, self.text, self.delay = widget, text, delay
        self._job = None
        self._window = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")
        widget.bind("<Destroy>", self._hide, add="+")

    def _text(self):
        """The hint; ``text`` may be a function, for hints that change (such as a shortcut)."""
        try:
            return self.text() if callable(self.text) else self.text
        except Exception:
            return ""

    def _schedule(self, event=None):
        self._cancel()
        if self._text():
            self._job = self.widget.after(self.delay, self._show)

    def _cancel(self):
        if self._job is not None:
            try:
                self.widget.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None

    def _show(self):
        self._job = None
        if self._window is not None or not self.widget.winfo_exists():
            return
        try:
            window = tk.Toplevel(self.widget)
            window.wm_overrideredirect(True)
            try:
                window.attributes("-topmost", True)
            except tk.TclError:
                pass
            label = tk.Label(window, text=self._text(), bg=Palette.TOAST_BG, fg=Palette.TOAST_TEXT,
                             font=Theme.font("small"), padx=Theme.px(8), pady=Theme.px(4), justify="left")
            label.pack()
            window.update_idletasks()
            x = self.widget.winfo_rootx() + (self.widget.winfo_width() - window.winfo_reqwidth()) // 2
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + Theme.px(6)
            x = max(4, min(x, self.widget.winfo_screenwidth() - window.winfo_reqwidth() - 4))
            if y + window.winfo_reqheight() > self.widget.winfo_screenheight() - 4:
                y = self.widget.winfo_rooty() - window.winfo_reqheight() - Theme.px(6)
            window.wm_geometry(f"+{x}+{y}")
            self._window = window
        except tk.TclError:
            self._window = None

    def _hide(self, event=None):
        self._cancel()
        if self._window is not None:
            try:
                self._window.destroy()
            except tk.TclError:
                pass
            self._window = None


# ---------------------------------------------------------------------- Toast
class Toast:
    """Brief confirmation shown at the bottom of a container."""

    _current = {}

    @classmethod
    def show(cls, host, message, icon_name="check", duration=2400, action_text=None, action=None):
        cls.dismiss(host)
        frame = RoundedFrame(host, fill=Palette.TOAST_BG, outline=None, radius=10, padx=16, pady=10,
                             surface=_bg_of(host), hug=True)
        inner = frame.body
        if icon_name:
            tk.Label(inner, image=icon(icon_name, 15, Palette.TOAST_TEXT), bg=Palette.TOAST_BG).pack(
                side="left", padx=(0, Theme.px(8)))
        tk.Label(inner, text=message, bg=Palette.TOAST_BG, fg=Palette.TOAST_TEXT,
                 font=Theme.font("base", "medium")).pack(side="left")
        if action_text and action:
            link = tk.Label(inner, text=action_text, bg=Palette.TOAST_BG, fg="#7FD49A",
                            font=Theme.font("base", "semibold"), cursor="hand2")
            link.pack(side="left", padx=(Theme.px(14), 0))
            link.bind("<Button-1>", lambda e: (cls.dismiss(host), action()))
        frame.place(relx=0.5, rely=1.0, y=-Theme.px(92), anchor="s")
        tk.Misc.lift(frame)     # Canvas.lift would raise a canvas item, not the widget
        job = host.after(duration, lambda: cls.dismiss(host, frame))
        cls._current[host] = (frame, job)
        return frame

    @classmethod
    def dismiss(cls, host, only=None):
        entry = cls._current.get(host)
        if not entry:
            return
        frame, job = entry
        if only is not None and frame is not only:
            return
        cls._current.pop(host, None)
        try:
            host.after_cancel(job)
        except tk.TclError:
            pass
        try:
            frame.destroy()
        except tk.TclError:
            pass


# ----------------------------------------------------------------- popup menu
def build_menu(parent, items):
    """Create a themed ``tk.Menu`` from a list of item descriptions.

    Each item is ``None`` (separator) or a dict with ``label`` and ``command`` and
    optionally ``checked`` (bool), ``enabled`` (bool), ``header`` (bool) or
    ``submenu`` (list of items).
    """
    menu = tk.Menu(parent, tearoff=0, font=Theme.font("base"), bg=Palette.BG, fg=Palette.TEXT,
                   activebackground=Palette.PRIMARY_SOFT, activeforeground=Palette.TEXT,
                   disabledforeground=Palette.TEXT_FAINT, relief="flat", bd=1, activeborderwidth=0)
    for item in items:
        if item is None:
            menu.add_separator()
            continue
        label = item.get("label", "")
        state = "normal" if item.get("enabled", True) else "disabled"
        if item.get("header"):
            menu.add_command(label=label, state="disabled", font=Theme.font("small", "semibold"))
        elif "submenu" in item:
            menu.add_cascade(label=label, menu=build_menu(menu, item["submenu"]), state=state)
        elif "checked" in item:
            # A leading check mark reads the same on every platform, unlike native check items.
            prefix = "✓  " if item["checked"] else "     "
            menu.add_command(label=prefix + label, command=item.get("command"), state=state)
        else:
            menu.add_command(label=label, command=item.get("command"), state=state)
    return menu


def popup_menu(anchor, items, align="left", at=None):
    """Post a drop-down menu just below ``anchor`` (or at screen position ``at``)."""
    popup_menu.last_items = items
    if not popup_menu.enabled:
        return None
    menu = build_menu(anchor, items)
    anchor.update_idletasks()
    x = anchor.winfo_rootx()
    if align == "right":
        x = anchor.winfo_rootx() + anchor.winfo_width() - menu.winfo_reqwidth()
    y = anchor.winfo_rooty() + anchor.winfo_height() + Theme.px(4)
    if at is not None:
        x, y = at
    try:
        menu.tk_popup(max(0, x), y)
    finally:
        menu.grab_release()
    return menu


# ``enabled`` can be switched off by automated tests, which then read ``last_items``
# and call the commands directly instead of driving a native menu.
popup_menu.enabled = True
popup_menu.last_items = None
