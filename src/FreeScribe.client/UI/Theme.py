"""
Theme.py

Design tokens and widget styling for the FreeScribe client.

Everything visual that is shared between windows lives here: the colour palette,
the type scale, pixel scaling for high-DPI screens, the ttk style definitions and
the Tk option database defaults that give the classic ``tk`` widgets used by the
older dialogs (settings, loading, pop-ups) the same look as the main workspace.

Call :func:`Theme.init` once, right after the root window is created and before
any other widget is built.

This software is released under the AGPL-3.0 license.
"""

import sys
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk


class Palette:
    """Colour tokens. Use these names rather than literal colours in UI code."""

    # Peach & lime: warm cream and peach surfaces, a leaf-green accent and pastel lime highlights.
    # Every text / background pair here meets WCAG AA (4.5:1) except TEXT_FAINT, used only for hints.

    # Surfaces
    BG = "#FFFCF9"              # main working surface (warm cream)
    SIDEBAR = "#FCEFE6"         # navigation rail (soft peach)
    SURFACE = "#FFF7F1"         # quiet panels, input wells
    RAISED = "#FAE8DC"          # neutral buttons, chips
    HOVER = "#F7E0D1"           # hover on sidebar rows / neutral buttons
    SELECTED = "#F3D6C3"        # selected sidebar row
    PRESSED = "#EDCAB4"

    # Lines
    BORDER = "#F1E1D6"
    BORDER_STRONG = "#E0C6B5"

    # Text
    TEXT = "#2A2421"            # warm charcoal
    TEXT_MUTED = "#6B5E57"
    TEXT_FAINT = "#9A867C"
    TEXT_ON_PRIMARY = "#FFFFFF"

    # Brand / primary action (leaf green, with pastel lime highlights)
    PRIMARY = "#3E7B1E"
    PRIMARY_HOVER = "#346A18"
    PRIMARY_PRESSED = "#2B5A13"
    PRIMARY_SOFT = "#E8F6CF"
    PRIMARY_SOFT_HOVER = "#DCF0B8"

    # Status
    DANGER = "#CC4B37"          # warm coral red
    DANGER_HOVER = "#B33F2D"
    DANGER_PRESSED = "#983324"
    DANGER_SOFT = "#FFE5DD"
    WARNING = "#94590A"
    WARNING_SOFT = "#FFF2D6"
    WARNING_BORDER = "#F2D59A"
    SUCCESS = "#4C9A2A"
    INFO = "#376399"
    INFO_SOFT = "#E8EFF9"

    # Overlays
    TOAST_BG = "#2E2723"
    TOAST_TEXT = "#FFFFFF"
    DISABLED_BG = "#F5E9E0"
    DISABLED_TEXT = "#B9A99F"


# Type scale in points for Windows / Linux. macOS draws a point as one pixel,
# so the scale is multiplied up there (see ``Theme._font_factor``).
_TYPE_SCALE = {
    "micro": 8,
    "small": 9,
    "base": 10,
    "medium": 11,
    "large": 12,
    "title": 15,
    "display": 18,
}

_WEIGHT_SUFFIXES = {
    "medium": ("Medium",),
    "semibold": ("SemiBold", "Semibold", "Semi Bold", "DemiBold", "Demibold"),
}


class Theme:
    """Process-wide theme state. All members are class-level."""

    C = Palette
    scale = 1.0
    family = "TkDefaultFont"
    initialized = False
    _font_factor = 1.0
    _fonts = {}
    _families = frozenset()

    # ------------------------------------------------------------------ setup
    @staticmethod
    def _forget_images():
        """Images belong to one Tk interpreter; a new main window needs fresh ones."""
        import sys
        for module_name, names in (("UI.Widgets.Controls", ("_image_cache",)),
                                   ("UI.Icons", ("_photo_cache",))):
            module = sys.modules.get(module_name)
            for name in names:
                cache = getattr(module, name, None) if module is not None else None
                if isinstance(cache, dict):
                    cache.clear()

    is_mac = False

    @classmethod
    def keys(cls, text: str) -> str:
        """Shortcut labels as each platform writes them: "Alt+R" is "⌥R" and "Ctrl+N" is "⌘N" on a Mac."""
        if not cls.is_mac:
            return text
        return text.replace("Ctrl+Enter", "⌘↩").replace("Ctrl+", "⌘").replace("Alt+", "⌥")

    @classmethod
    def init(cls, root: tk.Misc) -> None:
        """Detect scaling and fonts, then install ttk styles and Tk defaults."""
        cls._forget_images()
        try:
            cls.is_mac = root.tk.call("tk", "windowingsystem") == "aqua"
        except tk.TclError:
            cls.is_mac = False
        cls._detect_scale(root)
        cls._detect_fonts(root)
        cls._configure_named_fonts()
        cls._configure_option_database(root)
        cls._configure_ttk(root)
        try:
            root.configure(bg=cls.C.BG)
        except tk.TclError:
            pass
        cls.initialized = True

    @classmethod
    def _detect_scale(cls, root) -> None:
        try:
            dpi = float(root.winfo_fpixels("1i"))
        except (tk.TclError, ValueError):
            dpi = 96.0
        if sys.platform == "darwin":
            # Tk on macOS reports 72 dpi and handles Retina backing itself.
            cls.scale = 1.0
            cls._font_factor = 1.3
        else:
            cls.scale = min(max(dpi / 96.0, 1.0), 3.0)
            cls._font_factor = 1.0

    @classmethod
    def _detect_fonts(cls, root) -> None:
        try:
            cls._families = frozenset(tkfont.families(root))
        except tk.TclError:
            cls._families = frozenset()

        if sys.platform == "win32":
            candidates = ["Segoe UI Variable Text", "Segoe UI"]
        elif sys.platform == "darwin":
            candidates = ["SF Pro Text", "Helvetica Neue"]
        else:
            candidates = ["Inter", "Noto Sans", "Ubuntu", "Cantarell", "Open Sans", "DejaVu Sans", "Liberation Sans"]

        for name in candidates:
            if name in cls._families:
                cls.family = name
                break
        else:
            try:
                cls.family = tkfont.nametofont("TkDefaultFont").actual("family")
            except tk.TclError:
                cls.family = "Helvetica"
        cls._fonts = {}

    @classmethod
    def _configure_named_fonts(cls) -> None:
        """Point Tk's built-in named fonts at the theme family so that widgets
        created without an explicit font (most of the dialogs) match."""
        size = cls.font_size("base")
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont",
                     "TkCaptionFont", "TkIconFont", "TkTooltipFont", "TkSmallCaptionFont"):
            try:
                named = tkfont.nametofont(name)
            except tk.TclError:
                continue
            small = name in ("TkTooltipFont", "TkSmallCaptionFont")
            named.configure(family=cls.family, size=cls.font_size("small") if small else size)

    # ------------------------------------------------------------------ fonts
    @classmethod
    def font_size(cls, size) -> int:
        points = _TYPE_SCALE.get(size, size)
        return int(round(points * cls._font_factor))

    @classmethod
    def font(cls, size="base", weight="normal", underline=False) -> tkfont.Font:
        """Return a cached :class:`tkinter.font.Font`.

        ``weight`` may be ``normal``, ``medium``, ``semibold`` or ``bold``. Tk only
        knows normal and bold, so the in-between weights are resolved to a
        matching family face when the system has one (for example
        "Segoe UI Semibold") and fall back gracefully when it does not.
        """
        key = (size, weight, underline)
        cached = cls._fonts.get(key)
        if cached is not None:
            return cached

        family, tk_weight = cls.family, "normal"
        if weight == "bold":
            tk_weight = "bold"
        elif weight in _WEIGHT_SUFFIXES:
            for suffix in _WEIGHT_SUFFIXES[weight]:
                candidate = f"{cls.family} {suffix}"
                if candidate in cls._families:
                    family = candidate
                    break
            else:
                tk_weight = "bold" if weight == "semibold" else "normal"

        font = tkfont.Font(family=family, size=cls.font_size(size), weight=tk_weight, underline=underline)
        cls._fonts[key] = font
        return font

    # ---------------------------------------------------------------- scaling
    @classmethod
    def px(cls, value) -> int:
        """Convert a design pixel value to device pixels."""
        return int(round(value * cls.scale))

    # ------------------------------------------------------- classic tk widgets
    @classmethod
    def _configure_option_database(cls, root) -> None:
        c = cls.C
        add = lambda pattern, value: root.option_add(pattern, value, "startupFile")

        add("*Background", c.BG)
        add("*Foreground", c.TEXT)
        add("*selectBackground", c.PRIMARY_SOFT_HOVER)
        add("*selectForeground", c.TEXT)
        add("*insertBackground", c.TEXT)
        add("*Canvas.highlightThickness", 0)
        add("*Frame.highlightThickness", 0)
        add("*Label.highlightThickness", 0)

        for cls_name in ("Entry", "Text", "Listbox", "Spinbox"):
            add(f"*{cls_name}.relief", "flat")
            # With a flat relief the border is drawn in the field colour, so on
            # single-line fields it works as inner padding around the text.
            add(f"*{cls_name}.borderWidth", cls.px(4) if cls_name in ("Entry", "Spinbox") else 0)
            add(f"*{cls_name}.highlightThickness", 1)
            add(f"*{cls_name}.highlightBackground", c.BORDER_STRONG)
            add(f"*{cls_name}.highlightColor", c.PRIMARY)
            add(f"*{cls_name}.background", c.BG)
        add("*Entry.disabledBackground", c.DISABLED_BG)
        add("*Entry.disabledForeground", c.TEXT_MUTED)
        add("*Entry.readonlyBackground", c.SURFACE)
        add("*Text.padX", cls.px(8))
        add("*Text.padY", cls.px(6))
        add("*Listbox.activeStyle", "none")

        add("*Button.relief", "flat")
        add("*Button.borderWidth", 0)
        add("*Button.background", c.RAISED)
        add("*Button.activeBackground", c.HOVER)
        add("*Button.activeForeground", c.TEXT)
        add("*Button.disabledForeground", c.DISABLED_TEXT)
        add("*Button.highlightThickness", 1)
        add("*Button.highlightBackground", c.BORDER_STRONG)
        add("*Button.highlightColor", c.PRIMARY)
        add("*Button.padX", cls.px(12))
        add("*Button.padY", cls.px(4))
        add("*Button.cursor", "hand2")

        for cls_name in ("Checkbutton", "Radiobutton"):
            add(f"*{cls_name}.activeBackground", c.BG)
            add(f"*{cls_name}.selectColor", c.BG)
            add(f"*{cls_name}.highlightThickness", 0)
        cls._configure_checkbuttons(root, add)

        add("*Menu.background", c.BG)
        add("*Menu.foreground", c.TEXT)
        add("*Menu.activeBackground", c.PRIMARY_SOFT)
        add("*Menu.activeForeground", c.TEXT)
        add("*Menu.disabledForeground", c.DISABLED_TEXT)
        add("*Menu.relief", "flat")
        add("*Menu.borderWidth", 1)
        add("*Menu.activeBorderWidth", 0)
        add("*Menu.tearOff", 0)

        add("*Scrollbar.borderWidth", 0)
        add("*Scrollbar.relief", "flat")
        add("*Scrollbar.troughColor", c.BG)
        add("*Scrollbar.background", c.BORDER_STRONG)
        add("*Scrollbar.activeBackground", c.TEXT_FAINT)
        add("*Scrollbar.highlightThickness", 0)
        add("*Scrollbar.width", cls.px(10))

        # Drop-down list of ttk comboboxes is a classic Listbox.
        add("*TCombobox*Listbox.background", c.BG)
        add("*TCombobox*Listbox.foreground", c.TEXT)
        add("*TCombobox*Listbox.selectBackground", c.PRIMARY_SOFT)
        add("*TCombobox*Listbox.selectForeground", c.TEXT)
        add("*TCombobox*Listbox.borderWidth", 0)
        add("*TCombobox*Listbox.highlightThickness", 0)

    @classmethod
    def _configure_checkbuttons(cls, root, add) -> None:
        """Give classic check buttons a drawn box that scales with the display.

        macOS keeps its native check boxes, which already look right there.
        """
        try:
            if root.tk.call("tk", "windowingsystem") == "aqua":
                return
            from PIL import Image, ImageDraw, ImageTk
            from UI.Icons import icon_image
        except Exception:
            return

        size, factor = cls.px(16), 4
        radius = cls.px(4)

        def box(fill, outline):
            big = Image.new("RGBA", (size * factor, size * factor), (0, 0, 0, 0))
            draw = ImageDraw.Draw(big)
            draw.rounded_rectangle([0, 0, size * factor - 1, size * factor - 1], radius=radius * factor, fill=outline)
            inset = max(1, cls.px(1)) * factor
            draw.rounded_rectangle([inset, inset, size * factor - 1 - inset, size * factor - 1 - inset],
                                   radius=max(0, radius * factor - inset), fill=fill)
            return big.resize((size, size), Image.LANCZOS)

        unchecked = box(cls.C.BG, cls.C.TEXT_FAINT)
        checked = box(cls.C.PRIMARY, cls.C.PRIMARY)
        tick = icon_image("check", size - cls.px(4), cls.C.TEXT_ON_PRIMARY)
        checked.alpha_composite(tick, (cls.px(2), cls.px(2)))

        # Keep references: Tk only holds images by name.
        cls._check_images = (ImageTk.PhotoImage(unchecked, master=root), ImageTk.PhotoImage(checked, master=root))
        add("*Checkbutton.image", str(cls._check_images[0]))
        add("*Checkbutton.selectImage", str(cls._check_images[1]))
        add("*Checkbutton.indicatorOn", 0)
        add("*Checkbutton.compound", "left")
        add("*Checkbutton.relief", "flat")
        add("*Checkbutton.offRelief", "flat")
        add("*Checkbutton.overRelief", "flat")
        add("*Checkbutton.borderWidth", 0)
        add("*Checkbutton.padX", cls.px(2))
        add("*Checkbutton.padY", cls.px(2))
        add("*Checkbutton.cursor", "hand2")

    # --------------------------------------------------------------------- ttk
    @classmethod
    def _configure_ttk(cls, root) -> None:
        c = cls.C
        style = ttk.Style(root)
        try:
            # ``clam`` is the only built-in theme that honours custom colours on
            # every platform, which keeps Windows, macOS and Linux consistent.
            style.theme_use("clam")
        except tk.TclError:
            pass

        base_font = cls.font("base")
        style.configure(".", background=c.BG, foreground=c.TEXT, font=base_font,
                        bordercolor=c.BORDER, darkcolor=c.BG, lightcolor=c.BG,
                        troughcolor=c.BG, focuscolor=c.PRIMARY,
                        selectbackground=c.PRIMARY_SOFT_HOVER, selectforeground=c.TEXT,
                        insertcolor=c.TEXT, fieldbackground=c.BG)
        style.map(".", foreground=[("disabled", c.DISABLED_TEXT)])

        style.configure("TFrame", background=c.BG)
        style.configure("Disabled.TFrame", background=c.DISABLED_BG)
        style.configure("TLabel", background=c.BG, foreground=c.TEXT)
        style.configure("Muted.TLabel", foreground=c.TEXT_MUTED)
        style.configure("TLabelframe", background=c.BG, bordercolor=c.BORDER, relief="solid", borderwidth=1)
        style.configure("TLabelframe.Label", background=c.BG, foreground=c.TEXT_MUTED, font=cls.font("small", "semibold"))
        style.configure("TSeparator", background=c.BORDER)

        # Buttons
        pad = (cls.px(14), cls.px(6))
        style.configure("TButton", background=c.RAISED, foreground=c.TEXT, bordercolor=c.BORDER_STRONG,
                        lightcolor=c.RAISED, darkcolor=c.RAISED, relief="flat", padding=pad,
                        focusthickness=1, focuscolor=c.RAISED, font=cls.font("base", "medium"))
        style.map("TButton",
                  background=[("disabled", c.DISABLED_BG), ("pressed", c.PRESSED), ("active", c.HOVER)],
                  lightcolor=[("pressed", c.PRESSED), ("active", c.HOVER)],
                  darkcolor=[("pressed", c.PRESSED), ("active", c.HOVER)],
                  bordercolor=[("focus", c.PRIMARY)],
                  focuscolor=[("pressed", c.PRESSED), ("active", c.HOVER)])
        style.configure("Accent.TButton", background=c.PRIMARY, foreground=c.TEXT_ON_PRIMARY,
                        bordercolor=c.PRIMARY, lightcolor=c.PRIMARY, darkcolor=c.PRIMARY, focuscolor=c.PRIMARY)
        style.map("Accent.TButton",
                  background=[("disabled", c.DISABLED_BG), ("pressed", c.PRIMARY_PRESSED), ("active", c.PRIMARY_HOVER)],
                  lightcolor=[("pressed", c.PRIMARY_PRESSED), ("active", c.PRIMARY_HOVER)],
                  darkcolor=[("pressed", c.PRIMARY_PRESSED), ("active", c.PRIMARY_HOVER)],
                  focuscolor=[("pressed", c.PRIMARY_PRESSED), ("active", c.PRIMARY_HOVER)],
                  foreground=[("disabled", c.DISABLED_TEXT)])

        # Text inputs
        field_pad = (cls.px(8), cls.px(5))
        for name in ("TEntry", "TCombobox", "TSpinbox"):
            style.configure(name, fieldbackground=c.BG, background=c.BG, foreground=c.TEXT,
                            bordercolor=c.BORDER_STRONG, lightcolor=c.BG, darkcolor=c.BG,
                            arrowcolor=c.TEXT_MUTED, arrowsize=cls.px(14), padding=field_pad,
                            selectbackground=c.PRIMARY_SOFT_HOVER, selectforeground=c.TEXT)
            style.map(name,
                      fieldbackground=[("disabled", c.DISABLED_BG), ("readonly", c.BG)],
                      foreground=[("disabled", c.TEXT_MUTED)],
                      bordercolor=[("focus", c.PRIMARY), ("hover", c.TEXT_FAINT)],
                      lightcolor=[("focus", c.PRIMARY)],
                      darkcolor=[("focus", c.PRIMARY)],
                      selectbackground=[("readonly", c.BG)],
                      selectforeground=[("readonly", c.TEXT)],
                      background=[("active", c.SURFACE), ("readonly", c.BG)],
                      arrowcolor=[("disabled", c.DISABLED_TEXT)])
        style.configure("Mic.TCombobox", padding=field_pad)

        # Check / radio
        for name in ("TCheckbutton", "TRadiobutton"):
            style.configure(name, background=c.BG, foreground=c.TEXT, focuscolor=c.BG,
                            indicatorcolor=c.BG, indicatorbackground=c.BG,
                            upperbordercolor=c.BORDER_STRONG, lowerbordercolor=c.BORDER_STRONG,
                            padding=(cls.px(2), cls.px(2)))
            style.map(name,
                      background=[("active", c.BG)],
                      indicatorcolor=[("selected", c.PRIMARY), ("pressed", c.PRIMARY_SOFT)],
                      indicatorbackground=[("selected", c.PRIMARY)])

        # Notebook (settings tabs): flat tabs, the selected one sits on the page.
        style.configure("TNotebook", background=c.SIDEBAR, bordercolor=c.BORDER, lightcolor=c.SIDEBAR,
                        darkcolor=c.SIDEBAR, tabmargins=(cls.px(10), cls.px(8), cls.px(10), 0), borderwidth=0)
        style.configure("TNotebook.Tab", background=c.SIDEBAR, foreground=c.TEXT_MUTED,
                        bordercolor=c.SIDEBAR, lightcolor=c.SIDEBAR, darkcolor=c.SIDEBAR,
                        padding=(cls.px(14), cls.px(8)), font=cls.font("base", "medium"), focuscolor=c.SIDEBAR)
        style.map("TNotebook.Tab",
                  background=[("selected", c.BG), ("active", c.HOVER)],
                  foreground=[("selected", c.TEXT), ("active", c.TEXT)],
                  lightcolor=[("selected", c.BG), ("active", c.HOVER)],
                  darkcolor=[("selected", c.BG), ("active", c.HOVER)],
                  bordercolor=[("selected", c.BORDER)],
                  focuscolor=[("selected", c.BG), ("active", c.HOVER)],
                  padding=[("selected", (cls.px(14), cls.px(8)))],
                  expand=[("selected", (0, 0, 0, 0))])

        # Scrollbars: thin, arrow-less, quiet until hovered.
        thumb, thumb_hover = "#D3D0C8", "#B5B2A9"
        for orient, sticky in (("Vertical", "ns"), ("Horizontal", "ew")):
            name = f"{orient}.TScrollbar"
            try:
                style.layout(name, [(f"{orient}.Scrollbar.trough", {
                    "sticky": sticky,
                    "children": [(f"{orient}.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})],
                })])
            except tk.TclError:
                pass
            style.configure(name, background=thumb, troughcolor=c.BG, bordercolor=c.BG,
                            lightcolor=thumb, darkcolor=thumb, relief="flat", borderwidth=0,
                            arrowsize=cls.px(9), width=cls.px(9), gripcount=0)
            style.map(name,
                      background=[("pressed", thumb_hover), ("active", thumb_hover), ("disabled", c.BG)],
                      lightcolor=[("pressed", thumb_hover), ("active", thumb_hover), ("disabled", c.BG)],
                      darkcolor=[("pressed", thumb_hover), ("active", thumb_hover), ("disabled", c.BG)])
            sidebar_name = f"Sidebar.{name}"
            style.configure(sidebar_name, troughcolor=c.SIDEBAR, bordercolor=c.SIDEBAR)
            style.map(sidebar_name,
                      background=[("pressed", thumb_hover), ("active", thumb_hover), ("disabled", c.SIDEBAR)],
                      lightcolor=[("pressed", thumb_hover), ("active", thumb_hover), ("disabled", c.SIDEBAR)],
                      darkcolor=[("pressed", thumb_hover), ("active", thumb_hover), ("disabled", c.SIDEBAR)])

        # Progress / scale
        style.configure("TProgressbar", background=c.PRIMARY, troughcolor=c.RAISED, bordercolor=c.RAISED,
                        lightcolor=c.PRIMARY, darkcolor=c.PRIMARY, thickness=cls.px(6), borderwidth=0)
        style.configure("TScale", background=c.BG, troughcolor=c.RAISED, bordercolor=c.BORDER,
                        lightcolor=c.BG, darkcolor=c.BORDER_STRONG)

        # Trees / lists used by the data managers
        style.configure("Treeview", background=c.BG, fieldbackground=c.BG, foreground=c.TEXT,
                        bordercolor=c.BORDER, rowheight=cls.px(28), borderwidth=0)
        style.map("Treeview", background=[("selected", c.PRIMARY_SOFT)], foreground=[("selected", c.TEXT)])
        style.configure("Treeview.Heading", background=c.SURFACE, foreground=c.TEXT_MUTED, relief="flat",
                        font=cls.font("small", "semibold"), padding=(cls.px(8), cls.px(6)))
