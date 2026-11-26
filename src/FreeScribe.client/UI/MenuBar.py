"""
MenuBar.py

The window's menu bar: File, Edit, View, Session, Tools and Help. It holds the settings,
appearance, data and help items that used to be scattered over the sidebar. Every item calls
the same method or action the old button called; nothing here has logic of its own.

The bar is drawn inside the window (not by the operating system) so it looks and works the same
on macOS, Windows and Linux. Items that depend on the state of the app (recording, nothing to
export, settings locked) are refreshed just before a menu opens.

This software is released under the AGPL-3.0 license.
"""

import tkinter as tk

from UI import Shortcuts
from UI.MenuPopup import MenuPopup
from UI.Theme import Palette, Theme


class MenuBar:
    """:param workspace: the Workspace whose methods and actions the menu items call"""

    def __init__(self, workspace):
        self.ws = workspace
        self.root = workspace.root
        self.mac = Shortcuts.system(self.root) == "aqua"
        self._titles = []                          # (title, menu), in bar order
        self.entries = {}                          # key -> (menu, index), so states can be refreshed
        self.bar = tk.Menu(self.root, tearoff=0)
        self.appearance = tk.StringVar(value=Theme.mode)
        self._build_file()
        self._build_edit()
        self._build_view()
        self._build_session()
        self._build_tools()
        self._build_help()
        self.strip = None
        self.popup = None
        self.open_title = None
        self._mac_application_menu()
        self._bind_settings_shortcut()

    # ---------------------------------------------------------------- helpers
    def _accel(self, key):
        """How a shortcut is shown in a menu: "Ctrl+N", or "⌘N" on a Mac."""
        return Shortcuts.label(("Cmd+" if self.mac else "Ctrl+") + key, self.mac)

    def _menu(self, title, key):
        menu = tk.Menu(self.bar, tearoff=0, postcommand=lambda: self.refresh())
        self.bar.add_cascade(label=title, menu=menu)
        self._titles.append((title, menu))
        return menu

    def _add(self, menu, key, label, command, accelerator=None, **options):
        if accelerator:
            options["accelerator"] = accelerator
        menu.add_command(label=label, command=command, **options)
        self.entries[key] = (menu, menu.index("end"))

    def _enable(self, key, enabled):
        menu, index = self.entries[key]
        menu.entryconfigure(index, state="normal" if enabled else "disabled")

    def _relabel(self, key, text):
        menu, index = self.entries[key]
        menu.entryconfigure(index, label=text)

    def enabled(self, key):
        """Whether a menu item can be used right now (for tests and for the refresh)."""
        menu, index = self.entries[key]
        return str(menu.entrycget(index, "state")) != "disabled"

    def invoke(self, key):
        menu, index = self.entries[key]
        menu.invoke(index)

    def has_action(self, name):
        return name in self.ws.actions

    def _act(self, name):
        return lambda: self.ws._act(name)

    def _focused(self):
        try:
            return self.root.focus_get()
        except (KeyError, tk.TclError):
            return None

    def _send(self, virtual_event):
        """Cut, copy, paste and the rest go to the box that has the cursor."""
        widget = self._focused()
        if widget is not None:
            try:
                widget.event_generate(virtual_event)
            except tk.TclError:
                pass

    def _shortcut(self, action):
        """What the Start/Stop, Pause and Dictate shortcuts do (they are set up in client.py)."""
        handler = Shortcuts._handlers.get(action)
        if handler is not None:
            handler()

    # ---------------------------------------------------------------- File
    def _build_file(self):
        ws = self.ws
        menu = self._menu("File", "file")
        self._add(menu, "new_session", "New session", ws.new_session, self._accel("N"))
        if self.has_action("upload"):
            self._add(menu, "upload", "Upload an audio file…", self._act("upload"))
        export = tk.Menu(menu, tearoff=0)
        for label, extension in (("Word document (.docx)", ".docx"), ("PDF (.pdf)", ".pdf"),
                                 ("Plain text (.txt)", ".txt")):
            export.add_command(label=label, command=lambda e=extension: ws.export_current(e))
        menu.add_cascade(label="Export the open tab", menu=export)
        self.entries["export"] = (menu, menu.index("end"))
        menu.add_separator()
        if self.has_action("open_activity_log"):
            self._add(menu, "activity", "Activity log…", self._act("open_activity_log"))
        self._add(menu, "recordings", "Manage recordings…", self._act("manage_recordings"))
        self._add(menu, "logs", "Manage logs…", self._act("manage_logs"))
        self._add(menu, "delete_all", "Delete all sessions…", ws.delete_all_sessions)
        menu.add_separator()
        self._add(menu, "settings", "Settings…", self._act("open_settings"), self._accel(","))
        menu.add_separator()
        self._add(menu, "exit", "Quit FreeScribe" if self.mac else "Exit", self._exit, self._accel("Q") if self.mac else None)

    def _exit(self):
        """Closes the window the same way its close button does (which asks first when it must)."""
        command = self.root.protocol("WM_DELETE_WINDOW")
        if command:
            self.root.tk.call(command)
        else:
            self.root.destroy()

    # ---------------------------------------------------------------- Edit
    def _build_edit(self):
        menu = self._menu("Edit", "edit")
        self._add(menu, "undo", "Undo", lambda: self._send("<<Undo>>"), self._accel("Z"))
        self._add(menu, "redo", "Redo", lambda: self._send("<<Redo>>"), self._accel("Shift+Z" if self.mac else "Y"))
        menu.add_separator()
        self._add(menu, "cut", "Cut", lambda: self._send("<<Cut>>"), self._accel("X"))
        self._add(menu, "copy", "Copy", lambda: self._send("<<Copy>>"), self._accel("C"))
        self._add(menu, "paste", "Paste", lambda: self._send("<<Paste>>"), self._accel("V"))
        self._add(menu, "select_all", "Select all", lambda: self._send("<<SelectAll>>"), self._accel("A"))
        menu.add_separator()
        self._add(menu, "find", "Search sessions", lambda: self.ws.search_entry.focus_set(), self._accel("F"))

    # ---------------------------------------------------------------- View
    def _build_view(self):
        ws = self.ws
        menu = self._menu("View", "view")
        look = tk.Menu(menu, tearoff=0)
        look.add_radiobutton(label="Light", variable=self.appearance, value="light",
                             command=lambda: ws.set_appearance("light"))
        look.add_radiobutton(label="Dark", variable=self.appearance, value="dark",
                             command=lambda: ws.set_appearance("dark"))
        menu.add_cascade(label="Appearance", menu=look)
        self.entries["appearance"] = (menu, menu.index("end"))
        self.appearance_menu = look
        menu.add_separator()
        go = tk.Menu(menu, tearoff=0)
        for tab_id, title in (("context", "Context"), ("transcript", "Transcript"), ("note", "Note")):
            go.add_command(label=title, command=lambda t=tab_id: ws.show_tab(t))
        menu.add_cascade(label="Go to", menu=go)
        menu.add_separator()
        if self.has_action("toggle_view"):
            self._add(menu, "compact", "Compact view", self._act("toggle_view"))
        self._add(menu, "details", "Show where speech and notes are processed", ws.toggle_details)

    # ---------------------------------------------------------------- Session
    def _build_session(self):
        ws = self.ws
        menu = self._menu("Session", "session")
        self._add(menu, "record", "Start or stop transcribing", lambda: self._shortcut("record"))
        self._add(menu, "pause", "Pause or resume", lambda: self._shortcut("pause"))
        self._add(menu, "dictate", "Dictate at the cursor", lambda: self._shortcut("dictate"))
        menu.add_separator()
        self._add(menu, "generate", "Create the note", ws._shortcut_generate, self._accel("Return" if not self.mac else "↩"))

    # ---------------------------------------------------------------- Tools
    def _build_tools(self):
        ws = self.ws
        menu = self._menu("Tools", "tools")
        if self.has_action("open_eform_mapper"):
            self._add(menu, "eform", "eForm mapper", self._act("open_eform_mapper"))
        if self.has_action("open_browser_extension"):
            self._add(menu, "extension", "Chrome extension", self._act("open_browser_extension"))
        beta = tk.Menu(menu, tearoff=0)
        count = 0
        for action, label in (("open_chat", "Chat about this visit"), ("open_oscar_records", "Ask OSCAR records"),
                              ("fill_form", "Fill PDF form"), ("open_translate", "Translate")):
            if self.has_action(action):
                beta.add_command(label=label, command=lambda a=action: ws._open_beta(a))
                count += 1
        if count:
            if menu.index("end") is not None:
                menu.add_separator()
            menu.add_cascade(label="Beta testing", menu=beta)

    # ---------------------------------------------------------------- Help
    def _build_help(self):
        ws = self.ws
        menu = self._menu("Help", "help")
        from UI.QuickTour import show_tour
        self._add(menu, "tour", "Quick tour", lambda: show_tour(self.root))
        self._add(menu, "help_guide", "Help guide", self._act("help_guide"))
        self._add(menu, "shortcuts", "Keyboard shortcuts…", self._act("keyboard_shortcuts"))
        self._add(menu, "licences", "Third-party licences", self._act("third_party_notices"))
        menu.add_separator()
        self._add(menu, "debug", "Debug window", self._act("debug_window"))
        menu.add_separator()
        self._add(menu, "about", "About FreeScribe", self._act("about"))

    def _mac_application_menu(self):
        """The Mac puts About and Preferences in the application menu, and Quit with Command+Q."""
        try:
            self.root.createcommand("tk::mac::ShowPreferences", lambda: self.ws._act("open_settings"))
            self.root.createcommand("tk::mac::standardAboutPanel", lambda: self.ws._act("about"))
        except tk.TclError:
            pass

    # ---------------------------------------------------------------- refresh
    def refresh(self):
        """Bring the items that depend on the app's state up to date (runs just before a menu opens)."""
        ws = self.ws
        locked = bool(ws.locked)
        self._enable("new_session", not locked)
        if "upload" in self.entries:
            self._enable("upload", not locked)
        has_text = False
        try:
            has_text = bool(ws.current is not None and ws.current_page().get_text().strip())
        except Exception:
            pass
        self._enable("export", has_text)
        options = ws._act("get_data_options", default={}) or {}
        self._enable("recordings", bool(options.get("recordings")) and not locked)
        self._enable("logs", bool(options.get("logs")) and not locked)
        self._enable("delete_all", any(not s.is_empty for s in ws.store.sessions) and not locked)
        if "settings" in self.entries:
            self._enable("settings", bool(ws.settings_enabled))
        self.appearance.set(Theme.mode)
        if "compact" in self.entries:
            self._relabel("compact", "Full window" if ws.compact else "Compact view")
        for action, key in (("record", "record"), ("pause", "pause"), ("dictate", "dictate")):
            shown = Shortcuts.label(Shortcuts.key_of(action))
            menu, index = self.entries[key]
            menu.entryconfigure(index, accelerator=shown)
        self._enable("generate", bool(ws.generate_button.enabled) and not ws.compact)

    # ---------------------------------------------------------------- the bar in the window
    def build_strip(self, parent, before=None):
        """The bar itself: a slim row of titles at the top of the window."""
        self.strip = tk.Frame(parent, bg=Palette.SIDEBAR, height=Theme.px(34))
        self.strip.pack_propagate(False)
        self.strip.pack(side="top", fill="x", before=before)
        self.line = tk.Frame(parent, bg=Palette.BORDER, height=1)
        self.line.pack(side="top", fill="x", before=before)
        self.labels = {}
        for title, menu in self._titles:
            label = tk.Label(self.strip, text=title, font=Theme.font("base", "medium"), bg=Palette.SIDEBAR,
                             fg=Palette.TEXT_MUTED, padx=Theme.px(11), cursor="arrow")
            label.pack(side="left", fill="y", padx=(Theme.px(8) if not self.labels else 0, 0))
            label.bind("<Enter>", lambda e, t=title: self._hover_title(t, True))
            label.bind("<Leave>", lambda e, t=title: self._hover_title(t, False))
            label.bind("<ButtonPress-1>", lambda e, t=title: self.toggle(t))
            self.labels[title] = label
        self.root.bind_all("<ButtonPress>", self._outside_click, add="+")
        self.root.bind("<Configure>", self._root_moved, add="+")
        return self.strip

    def show(self):
        if self.strip is not None and not self.strip.winfo_manager():
            self.strip.pack(side="top", fill="x", before=self.ws.full)
            self.line.pack(side="top", fill="x", before=self.ws.full)

    def hide(self):
        self.close()
        for widget in (self.strip, self.line):
            if widget is not None:
                widget.pack_forget()

    def _paint(self, title):
        label = self.labels.get(title)
        if label is None:
            return
        if title == self.open_title:
            label.configure(bg=Palette.PRIMARY_SOFT if Theme.mode == "light" else Palette.HOVER,
                            fg=Palette.PRIMARY_PRESSED if Theme.mode == "light" else Palette.TEXT)
        else:
            label.configure(bg=Palette.SIDEBAR, fg=Palette.TEXT_MUTED)

    def _hover_title(self, title, inside):
        if inside:
            if self.open_title is not None and title != self.open_title:
                self.open(title)               # once one menu is open, pointing at another switches to it
            elif title != self.open_title:
                self.labels[title].configure(bg=Palette.HOVER, fg=Palette.TEXT)
        elif title != self.open_title:
            self._paint(title)

    def toggle(self, title):
        if self.open_title == title:
            self.close()
        else:
            self.open(title)

    def open(self, title):
        self.close()
        menu = dict(self._titles)[title]
        self.refresh()
        label = self.labels[title]
        x = label.winfo_rootx()
        y = label.winfo_rooty() + label.winfo_height() - Theme.px(2)
        self.open_title = title
        self._paint(title)
        self.popup = MenuPopup(self.root, menu, x, y, on_close=self._closed, on_move=self._move)

    def _move(self, direction):
        names = [title for title, _ in self._titles]
        if self.open_title in names:
            self.open(names[(names.index(self.open_title) + direction) % len(names)])

    def _closed(self):
        title, self.open_title, self.popup = self.open_title, None, None
        if title:
            self._paint(title)

    def close(self):
        if self.popup is not None:
            self.popup.close()

    def _outside_click(self, event):
        if self.popup is None:
            return
        widget = event.widget
        if self.popup.contains(widget) or widget in self.labels.values():
            return
        self.close()

    def _root_moved(self, event):
        if event.widget is self.root and self.popup is not None:
            self.close()

    def _bind_settings_shortcut(self):
        modifier = "Command" if self.mac else "Control"
        self.root.bind_all(f"<{modifier}-comma>", lambda e: (self.ws._act("open_settings")
                           if self.ws.settings_enabled else None, "break")[1])

    def recolor(self):
        """After a light/dark switch."""
        if self.strip is None:
            return
        self.strip.configure(bg=Palette.SIDEBAR)
        self.line.configure(bg=Palette.BORDER)
        for title in self.labels:
            self._paint(title)
