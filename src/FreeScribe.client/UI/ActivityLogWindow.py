"""
ActivityLogWindow.py

File > Activity log…: everything FreeScribe did with a visit's text, newest first. Search it, filter
it by type and time, click an entry to read it in full, export what is shown (CSV opens in Excel, or
JSON), choose how long entries are kept, switch the log off, or clear it.

The entries hold patient information; the window says so before anything is exported.

This software is released under the AGPL-3.0 license.
"""

import datetime as dt
import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import UI.Helpers
from UI.Art import Artwork
from UI.Theme import Palette, Theme
from UI.Widgets.Controls import Button, PlaceholderEntry, RoundedFrame, Scrollbar, popup_menu
from services.activity_log import KINDS, PERIODS, RETENTION_CHOICES

EXPORT_WARNING = ("The file will contain patient information, exactly as it was written, and it is not "
                  "encrypted.\n\nSave it only somewhere that is as private as your records.")

SECTIONS = (            # (field, heading) in the order they are shown in the detail pane
    ("instruction", "What you asked for"),
    ("prompt", "Sent to the AI"),
    ("reply", "Reply"),
    ("before", "Before"),
    ("after", "After"),
    ("text", "Text"),
)
FACTS = (("kind", "Type"), ("session_title", "Session"), ("template", "Template"), ("form", "Form"),
         ("tab", "Where"), ("by", "Changed by"), ("source", "From"), ("chart", "OSCAR chart"),
         ("seconds", "Took (seconds)"), ("error", "Error"))


def _when(value):
    try:
        at = dt.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return value or ""
    today = dt.datetime.now().date()
    if at.date() == today:
        return f"Today {at:%H:%M}"
    if at.date() == today - dt.timedelta(days=1):
        return f"Yesterday {at:%H:%M}"
    return f"{at:%b} {at.day} {at:%H:%M}"


class ActivityLogWindow:
    _open = None

    @classmethod
    def show(cls, parent, log):
        existing = cls._open
        if existing is not None and existing.window.winfo_exists():
            existing.window.lift()
            existing.refresh()
            return existing
        cls._open = cls(parent, log)
        return cls._open

    def __init__(self, parent, log):
        self.log, self.parent = log, parent
        self.kind, self.period = None, PERIODS[0]
        self.shown = []
        self.window = tk.Toplevel(parent, bg=Palette.BG)
        self.window.title("Activity log")
        UI.Helpers.set_window_icon(self.window)
        width, height = Theme.px(1120), Theme.px(720)
        x = parent.winfo_rootx() + max(0, (parent.winfo_width() - width) // 2)
        y = parent.winfo_rooty() + max(0, (parent.winfo_height() - height) // 4)
        self.window.geometry(f"{width}x{height}+{x}+{y}")
        self.window.minsize(Theme.px(820), Theme.px(520))
        self.window.bind("<Escape>", lambda e: self.window.destroy())
        self._build()
        self.refresh()
        self.window.focus_set()

    # ------------------------------------------------------------------ layout
    def _build(self):
        w = self.window
        footer = tk.Frame(w, bg=Palette.SIDEBAR)
        footer.pack(side="bottom", fill="x")
        tk.Frame(footer, bg=Palette.BORDER, height=1).pack(fill="x")
        row = tk.Frame(footer, bg=Palette.SIDEBAR)
        row.pack(fill="x", padx=Theme.px(24), pady=Theme.px(12))
        Button(row, text="Export CSV", icon_name="download", variant="secondary", size="sm",
               command=lambda: self.export("csv"), surface=Palette.SIDEBAR).pack(side="left")
        Button(row, text="Export JSON", variant="secondary", size="sm", command=lambda: self.export("json"),
               surface=Palette.SIDEBAR).pack(side="left", padx=(Theme.px(8), 0))
        self.clear_button = Button(row, text="Clear the log…", variant="danger-ghost", size="sm",
                                   command=self.clear, surface=Palette.SIDEBAR)
        self.clear_button.pack(side="right")
        self.keep_button = Button(row, text="", variant="ghost-strong", size="sm", trailing_icon="chevron-down",
                                  command=self._keep_menu, surface=Palette.SIDEBAR)
        self.keep_button.pack(side="right", padx=(0, Theme.px(8)))

        header = tk.Frame(w, bg=Palette.BG)
        header.pack(fill="x", padx=Theme.px(24), pady=(Theme.px(18), Theme.px(8)))
        titles = tk.Frame(header, bg=Palette.BG)
        titles.pack(side="left", fill="x", expand=True)
        tk.Label(titles, text="Activity log", font=Theme.font("display", "semibold"), bg=Palette.BG,
                 fg=Palette.TEXT).pack(anchor="w")
        self.subtitle = tk.Label(titles, text="", font=Theme.font("base"), bg=Palette.BG, fg=Palette.TEXT_MUTED,
                                 anchor="w", justify="left")
        self.subtitle.pack(anchor="w", pady=(Theme.px(2), 0))
        self.switch = Button(header, text="", variant="secondary", size="md", command=self.toggle_logging)
        self.switch.pack(side="right")

        bar = tk.Frame(w, bg=Palette.BG)
        bar.pack(fill="x", padx=Theme.px(24), pady=(0, Theme.px(10)))
        field = RoundedFrame(bar, fill=Palette.BG, radius=8, padx=8, pady=0)
        field.pack(side="left", fill="x", expand=True)
        self.search = PlaceholderEntry(field.body, placeholder="Search the text of every entry",
                                      on_change=lambda value: self.refresh(), font=Theme.font("base"),
                                      bg=Palette.BG)
        self.search.pack(fill="x", ipady=Theme.px(5))
        self.kind_button = Button(bar, text="All activity", variant="secondary", size="md",
                                  trailing_icon="chevron-down", command=self._kind_menu)
        self.kind_button.pack(side="left", padx=(Theme.px(8), 0))
        self.period_button = Button(bar, text=self.period[1], variant="secondary", size="md",
                                    trailing_icon="chevron-down", command=self._period_menu)
        self.period_button.pack(side="left", padx=(Theme.px(8), 0))

        body = tk.PanedWindow(w, orient="horizontal", bg=Palette.BG, sashwidth=Theme.px(10), bd=0,
                              sashrelief="flat")
        body.pack(fill="both", expand=True, padx=Theme.px(24), pady=(0, Theme.px(14)))
        left = tk.Frame(body, bg=Palette.BG)
        self.tree = ttk.Treeview(left, columns=("when", "type", "what", "session"), show="headings", selectmode="browse")
        for column, text, size in (("when", "When", 112), ("type", "Type", 92), ("what", "What", 230),
                                   ("session", "Session", 120)):
            self.tree.heading(column, text=text, anchor="w")
            self.tree.column(column, width=Theme.px(size), anchor="w", stretch=column == "what")
        style = ttk.Style(self.window)
        style.configure("Treeview", rowheight=Theme.px(30), font=Theme.font("base"))
        scroll = Scrollbar(left, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._show_detail())
        body.add(left, minsize=Theme.px(420), stretch="always")

        right = tk.Frame(body, bg=Palette.BG)
        self.detail = tk.Text(right, wrap="word", bg=Palette.BG, fg=Palette.TEXT, relief="flat", bd=0,
                              padx=Theme.px(16), pady=Theme.px(12), font=Theme.font("base"),
                              highlightthickness=0, cursor="arrow", spacing3=Theme.px(2))
        dscroll = Scrollbar(right, command=self.detail.yview)
        self.detail.configure(yscrollcommand=dscroll.set)
        dscroll.pack(side="right", fill="y")
        self.detail.pack(side="left", fill="both", expand=True)
        self.detail.tag_configure("heading", font=Theme.font("small", "semibold"), foreground=Palette.PRIMARY
                                  if Theme.mode == "light" else Palette.MINT, spacing1=Theme.px(12))
        self.detail.tag_configure("title", font=Theme.font("large", "semibold"), foreground=Palette.TEXT)
        self.detail.tag_configure("fact", foreground=Palette.TEXT_MUTED)
        self.detail.tag_configure("body", lmargin1=Theme.px(2), lmargin2=Theme.px(2))
        body.add(right, minsize=Theme.px(320), stretch="always")

        self.empty = tk.Frame(w, bg=Palette.BG)
        Artwork(self.empty, "empty-activity", height=170).pack(fill="x")
        self.empty_text = tk.Label(self.empty, text="", font=Theme.font("medium"), bg=Palette.BG,
                                   fg=Palette.TEXT_MUTED, justify="center")
        self.empty_text.pack(pady=Theme.px(8))
        self._body = body

    # ------------------------------------------------------------------ data
    def refresh(self):
        log = self.log
        days = self.period[2]
        self.shown = log.query(self.search.get_value(), [self.kind] if self.kind else None, days)
        total = log.count()
        self.tree.delete(*self.tree.get_children())
        for entry in self.shown:
            self.tree.insert("", "end", iid=entry["id"], values=(
                _when(entry.get("at")), KINDS.get(entry.get("kind"), entry.get("kind", "")),
                (entry.get("title") or "")[:90], (entry.get("session_title") or "")[:40]))
        state = "is on" if log.enabled else "is off"
        self.subtitle.configure(
            text=f"Kept on this computer, encrypted. The log {state}: {total} entr{'y' if total == 1 else 'ies'}, "
                 f"{log.retention_days} days kept.")
        self.switch.set(text="Turn the log off" if log.enabled else "Turn the log on")
        self.keep_button.set(text=f"Keep for {log.retention_days} days")
        self.clear_button.set_enabled(total > 0)
        if self.shown:
            self.empty.place_forget()
            self.tree.selection_set(self.shown[0]["id"])
            self._show_detail()
        else:
            self._write_detail(None)
            filtered = total > 0
            self.empty_text.configure(text=("Nothing matches. Try another search or filter." if filtered else
                                            "Nothing has been logged yet." if log.enabled else
                                            "The log is off, so nothing is being recorded."))
            self.empty.place(in_=self._body, relx=0, rely=0, relwidth=1, relheight=1)
            self.empty.lift()

    def _entry(self, iid):
        return next((e for e in self.shown if e["id"] == iid), None)

    def _show_detail(self):
        selection = self.tree.selection()
        self._write_detail(self._entry(selection[0]) if selection else None)

    def _write_detail(self, entry):
        t = self.detail
        t.configure(state="normal")
        t.delete("1.0", "end")
        if entry is not None:
            t.insert("end", (entry.get("title") or "") + "\n", "title")
            t.insert("end", dt.datetime.fromisoformat(entry["at"]).strftime("%A %B %d, %Y at %H:%M:%S") + "\n", "fact")
            for field, label in FACTS:
                value = entry.get(field)
                if value in (None, ""):
                    continue
                value = KINDS.get(value, value) if field == "kind" else value
                t.insert("end", f"{label}: ", "fact")
                t.insert("end", f"{value}\n", "body")
            for field, heading in SECTIONS:
                value = entry.get(field)
                if value:
                    t.insert("end", heading.upper() + "\n", "heading")
                    t.insert("end", str(value) + "\n", "body")
        t.configure(state="disabled")

    # ------------------------------------------------------------------ choices
    def _kind_menu(self):
        items = [{"label": "All activity", "command": lambda: self._set_kind(None)}]
        items += [{"label": label, "command": lambda k=key: self._set_kind(k)} for key, label in KINDS.items()]
        popup_menu(self.kind_button, items)

    def _set_kind(self, kind):
        self.kind = kind
        self.kind_button.set(text=KINDS.get(kind, "All activity"))
        self.refresh()

    def _period_menu(self):
        popup_menu(self.period_button, [{"label": p[1], "command": lambda p=p: self._set_period(p)} for p in PERIODS])

    def _set_period(self, period):
        self.period = period
        self.period_button.set(text=period[1])
        self.refresh()

    def _keep_menu(self):
        popup_menu(self.keep_button, [{"label": f"Keep for {d} days", "command": lambda d=d: self.set_retention(d)}
                                      for d in RETENTION_CHOICES])

    # ------------------------------------------------------------------ actions
    def set_retention(self, days):
        removed = self.log.set_retention_days(days)
        self.refresh()
        if removed:
            messagebox.showinfo("Activity log", f"{removed} older entr{'y was' if removed == 1 else 'ies were'} deleted.",
                                parent=self.window)

    def toggle_logging(self):
        self.log.set_enabled(not self.log.enabled)
        self.refresh()

    def clear(self):
        if messagebox.askyesno("Clear the activity log",
                               "Delete every entry in the activity log? This can't be undone.",
                               icon="warning", parent=self.window):
            self.log.clear()
            self.refresh()

    def export(self, kind):
        if not self.shown:
            messagebox.showinfo("Activity log", "There is nothing to export. Change the search or filters first.",
                                parent=self.window)
            return
        if not messagebox.askokcancel("Export the activity log", EXPORT_WARNING, icon="warning", parent=self.window):
            return
        extension = ".csv" if kind == "csv" else ".json"
        stamp = dt.datetime.now().strftime("%Y-%m-%d")
        path = filedialog.asksaveasfilename(parent=self.window, defaultextension=extension,
                                            initialfile=f"FreeScribe activity {stamp}{extension}",
                                            filetypes=[("CSV (opens in Excel)" if kind == "csv" else "JSON", f"*{extension}")])
        if not path:
            return
        try:
            count = (self.log.export_csv if kind == "csv" else self.log.export_json)(self.shown, path)
        except OSError as error:
            messagebox.showerror("Activity log", f"The file could not be saved.\n\n{error}", parent=self.window)
            return
        messagebox.showinfo("Activity log", f"Saved {count} entr{'y' if count == 1 else 'ies'} to {os.path.basename(path)}.",
                            parent=self.window)
