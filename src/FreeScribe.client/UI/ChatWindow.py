"""
ChatWindow.py

The chat panel docked on the right of the main window: a conversation with the
AI about the open session. Each message is sent with the session's context,
transcript, note, documents and the forms filled in it; the conversation is
kept with the session. The panel follows whichever session is open.

The panel only shows the conversation and collects messages; client.py sends
them to the model (with the usual identifier scrubbing and review).

This software is released under the AGPL-3.0 license.
"""

import platform
import tkinter as tk

from services import table_export
from UI.Icons import icon
from UI.Theme import Theme, Palette
from UI.Widgets.Controls import Button, ScrollFrame

SUBTITLE = "Knows this visit's note, transcript, documents and forms"
FOOTNOTE = "Names and numbers are hidden from the AI and put back in its reply."
EMPTY = "Ask about this visit, for example “What follow-up was planned?” or “Write a short note for her employer”."
FORM_TINT, FORM_TILE = "#7E3C0E", "#FEEBDD"
QUERY_TINT, QUERY_TILE = "#1D4E89", "#E6EEF9"
OSCAR_EMPTY = ("Ask about your OSCAR records, for example “How many active patients have diabetes?” or "
               "“Which of my patients had an A1C over 8 this year?”. You see the query before it runs.")
QUERY_NOTE = "Read-only. Runs with your OSCAR login in Chrome (keep an OSCAR page with the FreeScribe panel open)."
QUERY_STATES = {"waiting": "Waiting for OSCAR in Chrome…", "running": "Running in OSCAR…",
                "cancelled": "Not run.", "refused": "Not run."}
TABLE_ROWS = 12
IS_MAC = platform.system() == "Darwin"
RECORDS_TITLE = "OSCAR records"
RECORDS_SUBTITLE = "Ask about all your patients, not one visit. You check each query before it runs."


def _mono():
    """The system's fixed-width font at the panel's small size (for the query's SQL)."""
    import tkinter.font as tkfont
    family = tkfont.nametofont("TkFixedFont").actual("family")
    return (family, Theme.font_size("small"))


class SelectableText(tk.Text):
    """
    Read-only text that looks like a label but can be selected with the mouse and copied
    (Cmd/Ctrl+C, or right-click > Copy). It sizes itself to its text: as wide as the text
    needs up to ``wrap`` pixels (or exactly ``wrap`` with ``shrink=False``), and as tall as
    its wrapped lines.
    """

    _NAV = {"Left", "Right", "Up", "Down", "Home", "End", "Prior", "Next", "Shift_L", "Shift_R",
            "Control_L", "Control_R", "Meta_L", "Meta_R", "Alt_L", "Alt_R", "Super_L", "Super_R"}

    def __init__(self, parent, text, bg, fg, font, wrap, padx=0, pady=0, shrink=True):
        import math
        import tkinter.font as tkfont
        self._font = font if isinstance(font, tkfont.Font) else tkfont.Font(root=parent, font=font)
        zero = max(1, self._font.measure("0"))
        lines = text.split("\n") or [""]
        natural = max(self._font.measure(line) for line in lines) + 6
        width_px = max(zero * 2, min(wrap, natural) if shrink else wrap)
        chars = max(1, math.ceil(width_px / zero))
        super().__init__(parent, wrap="word", bg=bg, fg=fg, font=self._font, relief="flat", bd=0,
                         highlightthickness=0, padx=padx, pady=pady, width=chars,
                         height=self._estimate(lines, chars * zero), cursor="xterm", insertwidth=0,
                         takefocus=0, exportselection=True, spacing1=0, spacing3=0,
                         selectbackground=Palette.PRIMARY_SOFT, selectforeground=Palette.TEXT,
                         inactiveselectbackground=Palette.PRIMARY_SOFT)
        self.insert("1.0", text)
        self.bind("<Key>", self._on_key)
        for event in ("<<Paste>>", "<<Cut>>", "<<Clear>>", "<<PasteSelection>>", "<Button-2>" if not IS_MAC else "<F35>"):
            self.bind(event, lambda e: "break")
        menu_button = ("<Button-2>", "<Button-3>", "<Control-Button-1>") if IS_MAC else ("<Button-3>",)
        for button in menu_button:
            self.bind(button, self._menu)
        self.bind("<Configure>", lambda e: self.after_idle(self._fit))

    def _estimate(self, lines, width_px):
        """Wrapped lines, measured the way the text box wraps (whole words)."""
        count = 0
        for line in lines:
            words, current = line.split(" "), ""
            count += 1
            for word in words:
                trial = f"{current} {word}" if current else word
                if self._font.measure(trial) > width_px and current:
                    count += 1
                    current = word
                else:
                    current = trial
        return max(1, count)

    def _fit(self):
        try:
            lines = self.count("1.0", "end-1c", "displaylines")
        except tk.TclError:
            return
        breaks = (lines[0] if isinstance(lines, tuple) else lines) or 0
        lines = int(breaks) + 1                # the count is of line breaks between the two points
        if lines != int(self.cget("height")):
            self.configure(height=lines)

    def _on_key(self, event):
        command = bool(event.state & 0x4) or (IS_MAC and bool(event.state & 0x8))
        key = event.keysym.lower()
        if command and key == "c":
            self.event_generate("<<Copy>>")
            return "break"
        if command and key == "a":
            self.tag_add("sel", "1.0", "end-1c")
            return "break"
        if event.keysym in self._NAV:
            return None
        return "break"                      # read-only: no typing, deleting or pasting

    def _menu(self, event):
        menu = tk.Menu(self, tearoff=0)
        has_selection = bool(self.tag_ranges("sel"))
        menu.add_command(label="Copy" if has_selection else "Copy all",
                         command=lambda: self._copy(self.get("sel.first", "sel.last") if has_selection
                                                    else self.get("1.0", "end-1c")))
        menu.add_command(label="Select all", command=lambda: self.tag_add("sel", "1.0", "end-1c"))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    def _copy(self, text):
        self.clipboard_clear()
        self.clipboard_append(text)


class ChatPanel:
    """
    :param parent: the frame the panel lives in
    :param on_send: called with (session, text)
    :param on_clear: called with the session when the user clears its conversation
    :param on_close: called when the panel is closed
    """

    WIDTH = 330

    def __init__(self, parent, on_send, on_clear=None, on_close=None, on_query=None, on_learn=None):
        self.on_send = on_send
        self.on_query = on_query      # (session, message, "run" | "cancel"): a query of OSCAR's records
        self.on_learn = on_learn      # (session): check how this clinic's OSCAR records things
        from UI.TableWindow import reveal
        self.on_saved = reveal        # (path): show a saved results file in Finder / File Explorer
        self.counts_only = tk.BooleanVar(value=True)   # OSCAR records: totals only, nothing that identifies
        self.oscar_mode = False       # showing the OSCAR records chat (questions become queries), not a visit's
        self.on_clear = on_clear
        self.on_close = on_close
        self.session = None
        self.busy = {}            # session id -> status text while a reply is on its way
        self.errors = {}          # session id -> last error
        self._wrap = 260

        bg = Palette.SURFACE
        self.frame = tk.Frame(parent, bg=bg, width=Theme.px(self.WIDTH))
        self.frame.pack_propagate(False)
        self._build(bg)

    # ------------------------------------------------------------------ layout
    def _build(self, bg):
        header = tk.Frame(self.frame, bg=bg)
        header.pack(fill="x", padx=Theme.px(16), pady=(Theme.px(16), Theme.px(10)))
        self.close_button = Button(header, icon_name="close", variant="ghost", size="sm", surface=bg,
                                   command=self.close, tooltip="Close the chat")
        self.close_button.pack(side="right", anchor="n")
        self.clear_button = Button(header, icon_name="trash", variant="ghost", size="sm", surface=bg,
                                   command=self._clear, tooltip="Clear this session's chat")
        self.clear_button.pack(side="right", anchor="n")
        titles = tk.Frame(header, bg=bg)
        titles.pack(side="left", fill="x", expand=True)
        self.title_label = tk.Label(titles, text="Chat", bg=bg, fg=Palette.TEXT, anchor="w",
                                    font=Theme.font("medium", "semibold"))
        self.title_label.pack(fill="x")
        self.subtitle_label = tk.Label(titles, text=SUBTITLE, bg=bg, fg=Palette.TEXT_MUTED, anchor="w",
                                       justify="left", font=Theme.font("small"),
                                       wraplength=Theme.px(self.WIDTH - 110))
        self.subtitle_label.pack(fill="x")
        # OSCAR records only: privacy switch and learning this clinic's labels
        self.records_bar = tk.Frame(self.frame, bg=bg)
        line = tk.Frame(self.records_bar, bg=bg)
        line.pack(fill="x", padx=Theme.px(16), pady=(0, Theme.px(4)))
        tk.Checkbutton(line, text="Counts only", variable=self.counts_only, bg=bg, fg=Palette.TEXT,
                       activebackground=bg, selectcolor=Palette.BG, highlightthickness=0, anchor="w",
                       font=Theme.font("small", "medium")).pack(side="left")
        self.learn_button = Button(line, text="Learn this OSCAR", icon_name="refresh", variant="ghost", size="xs",
                                   surface=bg, command=self._learn,
                                   tooltip="Check, with count-only queries, how your OSCAR names measurements, "
                                           "codes and statuses, so the AI's queries match it")
        self.learn_button.pack(side="right")
        self.records_note = tk.Label(self.records_bar, bg=bg, fg=Palette.TEXT_MUTED, anchor="w", justify="left",
                                     font=Theme.font("micro"), wraplength=Theme.px(self.WIDTH - 40),
                                     text="Counts only: totals, never names or numbers that identify a patient.")
        self.records_note.pack(fill="x", padx=Theme.px(16), pady=(0, Theme.px(8)))
        self.counts_only.trace_add("write", lambda *a: self._counts_changed())
        self._profile_note = ""
        self._divider = tk.Frame(self.frame, bg=Palette.BORDER, height=1)
        self._divider.pack(fill="x")

        # Bottom first so the conversation takes the remaining height
        bottom = tk.Frame(self.frame, bg=bg)
        bottom.pack(side="bottom", fill="x", padx=Theme.px(16), pady=(Theme.px(8), Theme.px(14)))
        self.status = tk.Label(bottom, bg=bg, fg=Palette.TEXT_MUTED, anchor="w", justify="left",
                               font=Theme.font("small"), wraplength=Theme.px(self.WIDTH - 40))
        self.status.pack(fill="x", pady=(0, Theme.px(4)))
        border = tk.Frame(bottom, bg=Palette.BORDER_STRONG, padx=1, pady=1)
        border.pack(fill="x")
        box = tk.Frame(border, bg=Palette.BG)
        box.pack(fill="x")
        self.send_button = Button(box, icon_name="send", variant="primary", size="sm", pill=True,
                                  surface=Palette.BG, command=self.send, tooltip="Send (Enter)")
        self.send_button.pack(side="right", anchor="s", padx=Theme.px(6), pady=Theme.px(6))
        self.entry = tk.Text(box, height=3, wrap="word", relief="flat", bd=0, font=Theme.font("base"),
                             padx=Theme.px(10), pady=Theme.px(8), bg=Palette.BG, fg=Palette.TEXT,
                             insertbackground=Palette.TEXT, highlightthickness=0)
        self.entry.pack(side="left", fill="x", expand=True)
        self.entry.bind("<Return>", self._on_return)
        self.entry.bind("<KP_Enter>", self._on_return)
        tk.Label(bottom, text=FOOTNOTE, bg=bg, fg=Palette.TEXT_FAINT, anchor="w", justify="left",
                 font=Theme.font("micro"), wraplength=Theme.px(self.WIDTH - 40)).pack(fill="x", pady=(Theme.px(6), 0))

        self.log = ScrollFrame(self.frame, bg=bg)
        self.log.pack(fill="both", expand=True, padx=(Theme.px(14), Theme.px(4)), pady=(Theme.px(8), 0))
        self.log.canvas.bind("<Configure>", self._on_log_resize, add="+")

    def _on_log_resize(self, event):
        wrap = max(140, event.width - Theme.px(70))
        if abs(wrap - self._wrap) > 8:
            self._wrap = wrap
            self.refresh()

    # ------------------------------------------------------------------ content
    def set_session(self, session, records=False):
        """
        Show the conversation of ``session``: a visit's chat (the panel follows the open session),
        or with ``records`` the OSCAR records chat, where each question becomes a query of OSCAR.
        """
        self.session = session
        if records != self.oscar_mode:
            self.oscar_mode = records
            if records:
                self.records_bar.pack(fill="x", before=self._divider)
            else:
                self.records_bar.pack_forget()
            self.title_label.config(text=RECORDS_TITLE if records else "Chat")
            self.subtitle_label.config(text=RECORDS_SUBTITLE if records else SUBTITLE)
            self.clear_button.set(tooltip="Clear the OSCAR records chat" if records else "Clear this session's chat")
        self.refresh()

    def refresh(self, session_id=None):
        """Redraw the conversation (only when ``session_id`` is the one shown, if given)."""
        session = self.session
        if session is None or (session_id is not None and session_id != session.id):
            return
        body = self.log.body
        for child in body.winfo_children():
            child.destroy()
        bg = Palette.SURFACE
        if not session.chat:
            tk.Label(body, text=OSCAR_EMPTY if self.oscar_mode else EMPTY, bg=bg, fg=Palette.TEXT_FAINT, anchor="w", justify="left",
                     font=Theme.font("base"), wraplength=self._wrap).pack(fill="x", pady=Theme.px(10))
        for message in session.chat:
            self._message(body, message)
        busy = self.busy.get(session.id)
        if busy is not None:
            tk.Label(body, text="FreeScribe AI is writing…", bg=bg, fg=Palette.TEXT_MUTED, anchor="w",
                     font=Theme.font("small")).pack(fill="x", pady=(Theme.px(10), Theme.px(4)))
        error = self.errors.get(session.id)
        self.status.configure(text=error or busy or "", fg=Palette.DANGER if error else Palette.TEXT_MUTED)
        self.send_button.set_enabled(busy is None)
        self.clear_button.set_enabled(bool(session.chat) and busy is None)
        self.log.scroll_to_bottom()

    def _message(self, body, message):
        bg = Palette.SURFACE
        role, text = message.get("role", "user"), (message.get("text") or "").strip()
        row = tk.Frame(body, bg=bg)
        row.pack(fill="x", pady=(Theme.px(6), Theme.px(4)), padx=(0, Theme.px(10)))
        if role == "user":
            bubble = SelectableText(row, text, bg=Palette.TEXT, fg=Palette.TEXT_ON_PRIMARY, font=Theme.font("base"),
                                    wrap=self._wrap, padx=Theme.px(12), pady=Theme.px(8))
            bubble.configure(selectbackground="#5A5F6B", selectforeground=Palette.TEXT_ON_PRIMARY,
                             inactiveselectbackground="#5A5F6B")
            bubble.pack(side="right")
        elif role == "query":
            self._query_card(row, message)
        elif role == "assistant":
            holder = tk.Frame(row, bg=bg)
            holder.pack(side="left", fill="x")
            border = tk.Frame(holder, bg=Palette.BORDER, padx=1, pady=1)
            border.pack(anchor="w")
            SelectableText(border, text, bg=Palette.BG, fg=Palette.TEXT, font=Theme.font("base"), wrap=self._wrap,
                           padx=Theme.px(12), pady=Theme.px(8)).pack()
            Button(holder, text="Copy", icon_name="copy", variant="ghost", size="xs", surface=bg,
                   command=lambda t=text: self._copy(t), tooltip="Copy this reply").pack(anchor="w")
        else:
            oscar = message.get("icon") == "database"
            tile, tint = (QUERY_TILE, QUERY_TINT) if oscar else (FORM_TILE, FORM_TINT)
            card = tk.Frame(row, bg=tile)
            card.pack(fill="x")
            tk.Label(card, image=icon("database" if oscar else "form", 16, tint), bg=tile).pack(
                side="left", anchor="n", padx=(Theme.px(10), Theme.px(8)), pady=Theme.px(9))
            SelectableText(card, text, bg=tile, fg=tint, font=Theme.font("small"),
                           wrap=self._wrap - Theme.px(56), shrink=False).pack(
                side="left", fill="x", pady=Theme.px(8), padx=(0, Theme.px(10)))

    def _query_card(self, row, message):
        """A query of OSCAR's records: its SQL, then Run / Cancel, its state, or its rows."""
        card = tk.Frame(row, bg=QUERY_TILE)
        card.pack(fill="x")
        inner = tk.Frame(card, bg=QUERY_TILE)
        inner.pack(fill="x", padx=Theme.px(10), pady=Theme.px(8))
        head = tk.Frame(inner, bg=QUERY_TILE)
        head.pack(fill="x")
        tk.Label(head, image=icon("database", 15, QUERY_TINT), bg=QUERY_TILE).pack(side="left")
        tk.Label(head, text="Query for OSCAR", bg=QUERY_TILE, fg=QUERY_TINT, font=Theme.font("small", "semibold"),
                 anchor="w").pack(side="left", padx=(Theme.px(6), 0))
        if message.get("explain"):
            SelectableText(inner, message["explain"], bg=QUERY_TILE, fg=Palette.TEXT, font=Theme.font("small"),
                           wrap=self._wrap - Theme.px(20), shrink=False).pack(
                fill="x", pady=(Theme.px(6), 0))
        tags = []
        if message.get("source") == "recipe":
            tags.append("Ready-made query")
        elif message.get("source") == "ai":
            tags.append("Written by the AI: check it before running")
        if message.get("counts_only"):
            tags.append("Counts only")
        if tags:
            tk.Label(inner, text=" · ".join(tags), bg=QUERY_TILE, fg=QUERY_TINT, anchor="w", justify="left",
                     font=Theme.font("micro", "semibold"), wraplength=self._wrap - Theme.px(20)).pack(
                fill="x", pady=(Theme.px(2), 0))
        SelectableText(inner, message.get("text", ""), bg=QUERY_TILE, fg=Palette.TEXT_MUTED, font=_mono(),
                       wrap=self._wrap - Theme.px(20), shrink=False).pack(fill="x", pady=(Theme.px(6), Theme.px(6)))
        Button(inner, text="Copy query", icon_name="copy", variant="ghost", size="xs", surface=QUERY_TILE,
               command=lambda t=message.get("text", ""): self._copy(t),
               tooltip="Copy the query (SQL) only").pack(anchor="w", pady=(0, Theme.px(4)))
        state = message.get("state", "proposed")
        if state == "proposed":
            tk.Label(inner, text=QUERY_NOTE, bg=QUERY_TILE, fg=QUERY_TINT, justify="left", anchor="w",
                     font=Theme.font("micro"), wraplength=self._wrap - Theme.px(20)).pack(fill="x")
            buttons = tk.Frame(inner, bg=QUERY_TILE)
            buttons.pack(fill="x", pady=(Theme.px(6), 0))
            Button(buttons, text="Run in OSCAR", icon_name="play", variant="primary", size="xs", surface=QUERY_TILE,
                   command=lambda m=message: self._query_action(m, "run")).pack(side="left")
            Button(buttons, text="Cancel", variant="ghost", size="xs", surface=QUERY_TILE,
                   command=lambda m=message: self._query_action(m, "cancel")).pack(side="left", padx=(Theme.px(6), 0))
        elif state in ("waiting", "running"):
            line = tk.Frame(inner, bg=QUERY_TILE)
            line.pack(fill="x")
            tk.Label(line, text=QUERY_STATES[state], bg=QUERY_TILE, fg=QUERY_TINT, font=Theme.font("small"),
                     anchor="w").pack(side="left")
            Button(line, text="Cancel", variant="ghost", size="xs", surface=QUERY_TILE,
                   command=lambda m=message: self._query_action(m, "cancel")).pack(side="right")
            if message.get("note") and state == "waiting":
                SelectableText(inner, message["note"], bg=QUERY_TILE, fg=Palette.DANGER if "No OSCAR page" in
                               message["note"] else QUERY_TINT, font=Theme.font("small"),
                               wrap=self._wrap - Theme.px(20), shrink=False).pack(fill="x", pady=(Theme.px(4), 0))
        elif state == "done":
            rows, columns = message.get("rows") or [], message.get("columns") or []
            tk.Label(inner, text=f"{len(rows)} row{'s' if len(rows) != 1 else ''}", bg=QUERY_TILE, fg=QUERY_TINT,
                     font=Theme.font("small", "semibold"), anchor="w").pack(fill="x")
            if rows:
                self._table(inner, columns, rows, message)
                actions = tk.Frame(inner, bg=QUERY_TILE)
                actions.pack(fill="x", pady=(Theme.px(4), 0))
                Button(actions, text="View all rows", icon_name="expand", variant="primary", size="xs",
                       surface=QUERY_TILE, command=lambda m=message: self.view_rows(m),
                       tooltip="Every row in a window: sort, select and copy with the mouse, or save").pack(side="left")
                Button(actions, text="Excel", icon_name="download", variant="ghost", size="xs", surface=QUERY_TILE,
                       command=lambda m=message: self.save_rows(m, "xlsx"),
                       tooltip="Save every row as an Excel workbook (.xlsx)").pack(side="left", padx=(Theme.px(4), 0))
                Button(actions, text="CSV", icon_name="download", variant="ghost", size="xs", surface=QUERY_TILE,
                       command=lambda m=message: self.save_rows(m, "csv"),
                       tooltip="Save every row as a CSV file").pack(side="left")
                Button(actions, text="Copy rows", icon_name="copy", variant="ghost", size="xs", surface=QUERY_TILE,
                       command=lambda c=columns, r=rows: self._copy(table_export.as_tsv(c, r)),
                       tooltip="Copy every row with the column names (pastes into a spreadsheet)").pack(side="left")
        else:
            text = message.get("error") or QUERY_STATES.get(state, "")
            if text:
                SelectableText(inner, text, bg=QUERY_TILE, fg=Palette.DANGER if state in ("failed", "refused")
                               else QUERY_TINT, font=Theme.font("small"), wrap=self._wrap - Theme.px(20),
                               shrink=False).pack(fill="x")

    def _table(self, parent, columns, rows, message=None):
        """The first rows as a small grid; a click on it opens every row (see :meth:`view_rows`)."""
        grid = tk.Frame(parent, bg=Palette.BORDER, cursor="hand2")
        grid.pack(fill="x", pady=(Theme.px(4), Theme.px(2)))
        shown_cols = columns[:6]
        for c in range(len(shown_cols)):
            grid.columnconfigure(c, weight=1)
        width = max(6, int((self._wrap - Theme.px(30)) / max(1, len(shown_cols)) / 7))
        for c, name in enumerate(shown_cols):
            tk.Label(grid, text=name[:width], bg=Palette.SURFACE, fg=Palette.TEXT_MUTED, anchor="w",
                     font=Theme.font("micro"), padx=Theme.px(4)).grid(row=0, column=c, sticky="ew", padx=(0, 1), pady=(0, 1))
        for r, row in enumerate(rows[:TABLE_ROWS], start=1):
            for c, cell in enumerate(row[:len(shown_cols)]):
                tk.Label(grid, text=str(cell)[:width], bg=Palette.BG, fg=Palette.TEXT, anchor="w",
                         font=Theme.font("micro"), padx=Theme.px(4)).grid(row=r, column=c, sticky="ew", padx=(0, 1), pady=(0, 1))
        if message is not None:
            for cell in [grid] + grid.winfo_children():
                cell.configure(cursor="hand2")
                cell.bind("<Button-1>", lambda e, m=message: self.view_rows(m))
        more = []
        if len(rows) > TABLE_ROWS:
            more.append(f"{len(rows) - TABLE_ROWS} more rows")
        if len(columns) > len(shown_cols):
            more.append(f"{len(columns) - len(shown_cols)} more columns")
        if more:
            tk.Label(parent, text=" and ".join(more) + ": View all rows shows them", bg=QUERY_TILE, fg=QUERY_TINT,
                     font=Theme.font("micro"), anchor="w").pack(fill="x")

    def view_rows(self, message):
        """Every row of a query's result in its own window."""
        from UI.TableWindow import TableWindow
        return TableWindow(self.frame.winfo_toplevel(), message.get("columns") or [], message.get("rows") or [],
                           title=message.get("question", ""), sql=message.get("text", ""))

    def save_rows(self, message, kind, target=None):
        """Save every row as an Excel workbook (``xlsx``) or a CSV file."""
        import os
        from tkinter import filedialog, messagebox
        from UI.TableWindow import default_name
        label, ext = ("Excel workbook", ".xlsx") if kind == "xlsx" else ("CSV file", ".csv")
        if target is None:
            docs = os.path.expanduser("~/Documents")
            target = filedialog.asksaveasfilename(
                parent=self.frame.winfo_toplevel(), title=f"Save as {label}", defaultextension=ext,
                initialdir=docs if os.path.isdir(docs) else os.path.expanduser("~"),
                initialfile=default_name(message.get("question", "")) + ext, filetypes=[(label, f"*{ext}")])
        if not target:
            return None
        try:
            write = table_export.to_xlsx if kind == "xlsx" else table_export.to_csv
            write(target, message.get("columns") or [], message.get("rows") or [])
        except Exception as error:
            messagebox.showerror("Save results", f"The results could not be saved.\n\n{error}",
                                 parent=self.frame.winfo_toplevel())
            return None
        self.status.config(text=f"Saved {os.path.basename(target)} in {os.path.dirname(target)}. It may hold "
                                "patient information: keep it safe.")
        if callable(self.on_saved):
            self.on_saved(target)
        return target

    def _query_action(self, message, action):
        if self.session is not None and callable(self.on_query):
            self.on_query(self.session, message, action)

    def _copy(self, text):
        try:
            self.frame.clipboard_clear()
            self.frame.clipboard_append(text)
            self.status.configure(text="Copied to the clipboard.", fg=Palette.TEXT_MUTED)
        except tk.TclError:
            pass

    def set_busy(self, session_id, busy: bool, status: str = ""):
        if busy:
            self.busy[session_id] = status or "Sending…"
            self.errors.pop(session_id, None)
        else:
            self.busy.pop(session_id, None)
        self.refresh(session_id)
        if not busy and status and self.session is not None and self.session.id == session_id:
            self.status.configure(text=status, fg=Palette.TEXT_MUTED)

    def show_error(self, session_id, message: str):
        self.busy.pop(session_id, None)
        self.errors[session_id] = message
        self.refresh(session_id)

    # ------------------------------------------------------------------ actions
    def text(self) -> str:
        return self.entry.get("1.0", "end-1c").strip()

    def send(self):
        message = self.text()
        if not message or self.session is None or self.session.id in self.busy:
            return
        self.entry.delete("1.0", "end")
        self.errors.pop(self.session.id, None)
        if self.oscar_mode:
            self.on_send(self.session, message, oscar=True)
        else:
            self.on_send(self.session, message)

    def _on_return(self, event):
        if event.state & 0x0001:     # Shift: new line
            return None
        self.send()
        return "break"

    def _learn(self):
        if self.session is not None and callable(self.on_learn):
            self.on_learn(self.session)

    def set_profile_note(self, text):
        """What is known of this clinic's OSCAR, shown under the switch (e.g. "Learned 28 Oct")."""
        self._profile_note = text or ""
        self._counts_changed()

    def _counts_changed(self):
        base = ("Counts only: totals, never names or numbers that identify a patient."
                if self.counts_only.get() else
                "Lists allowed: results can name patients. They stay on this computer; the AI sees them "
                "with names and numbers hidden.")
        self.records_note.config(text=base + (f" {self._profile_note}" if self._profile_note else ""))

    def _clear(self):
        session = self.session
        if session is None or session.id in self.busy or not session.chat:
            return
        if callable(self.on_clear):
            self.on_clear(session)
        self.refresh()

    def focus(self):
        self.entry.focus_set()

    def exists(self) -> bool:
        try:
            return bool(self.frame.winfo_exists())
        except tk.TclError:
            return False

    def close(self):
        if callable(self.on_close):
            self.on_close()
