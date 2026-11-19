"""
TableWindow.py

Every row of a result (such as a query of OSCAR's records) in a window of its own: click a
column to sort, select rows with the mouse (Shift and Ctrl/Cmd for several) and copy them,
or save everything as a CSV file or an Excel workbook.

This software is released under the AGPL-3.0 license.
"""

import datetime
import logging
import os
import platform
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from services import table_export
from UI.Icons import icon
from UI.Theme import Theme, Palette
from UI.Widgets.Controls import Button

logger = logging.getLogger(__name__)
IS_MAC = platform.system() == "Darwin"
MOD = "Command" if IS_MAC else "Control"


def _sort_key(value):
    """Numbers sort as numbers, everything else as text (case-insensitive); blanks last."""
    text = str(value).strip()
    if not text:
        return (2, 0, "")
    try:
        return (0, float(text), "")
    except ValueError:
        return (1, 0, text.lower())


def reveal(path):
    """Show a saved file in Finder / File Explorer, selected, so the clinician sees where it went."""
    import subprocess
    try:
        if IS_MAC:
            subprocess.Popen(["open", "-R", path])
        elif platform.system() == "Windows":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(path) or "."])
    except Exception as error:
        logger.exception(f"Could not show {path}: {error}")


def default_name(title=""):
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H%M")
    words = "".join(ch if ch.isalnum() or ch in " -" else " " for ch in (title or "").strip())
    words = " ".join(words.split())[:50]
    return f"OSCAR query {stamp}" + (f" - {words}" if words else "")


class TableWindow:
    """
    :param columns, rows: the table (rows are lists of strings)
    :param title: what the table answers (the clinician's question), for the window and file names
    :param sql: the query that made it, shown under the title
    """

    def __init__(self, parent, columns, rows, title="", sql="", open_after_save=reveal):
        self.columns = list(columns) or [f"Column {i + 1}" for i in range(max((len(r) for r in rows), default=0))]
        self.rows = [list(r) + [""] * (len(self.columns) - len(r)) for r in rows]
        self.title = title
        self.open_after_save = open_after_save
        self._sorted = (None, False)
        bg = Palette.BG
        self.window = tk.Toplevel(parent, bg=bg)
        self.window.title(f"Results: {title}" if title else "Results")
        self.window.geometry(f"{Theme.px(980)}x{Theme.px(620)}")
        self.window.minsize(Theme.px(520), Theme.px(320))

        pad = Theme.px(18)
        head = tk.Frame(self.window, bg=bg)
        head.pack(fill="x", padx=pad, pady=(pad, Theme.px(8)))
        tk.Label(head, text=title or "Results", bg=bg, fg=Palette.TEXT, anchor="w", justify="left",
                 font=Theme.font("large", "semibold"), wraplength=Theme.px(900)).pack(fill="x")
        count = f"{len(self.rows)} row{'s' if len(self.rows) != 1 else ''} · {len(self.columns)} column" \
                f"{'s' if len(self.columns) != 1 else ''}"
        self.count_label = tk.Label(head, text=count, bg=bg, fg=Palette.TEXT_MUTED, anchor="w",
                                    font=Theme.font("small"))
        self.count_label.pack(fill="x")
        wrapping = []
        if sql:
            wrapping.append(tk.Label(head, text=sql, bg=bg, fg=Palette.TEXT_FAINT, anchor="w", justify="left",
                                     font=Theme.font("micro")))
            wrapping[-1].pack(fill="x", pady=(Theme.px(2), 0))
        head.bind("<Configure>", lambda e: [w.config(wraplength=e.width) for w in wrapping], add="+")

        bar = tk.Frame(self.window, bg=bg)
        bar.pack(fill="x", side="bottom", padx=pad, pady=(Theme.px(8), pad))
        Button(bar, text="Save as Excel…", icon_name="download", variant="primary", size="sm",
               command=lambda: self.save("xlsx")).pack(side="right")
        Button(bar, text="Save as CSV…", icon_name="download", variant="neutral", size="sm",
               command=lambda: self.save("csv")).pack(side="right", padx=(0, Theme.px(6)))
        self.copy_button = Button(bar, text="Copy rows", icon_name="copy", variant="neutral", size="sm",
                                  command=self.copy, tooltip="Copy the selected rows (all when none is selected)")
        self.copy_button.pack(side="right", padx=(0, Theme.px(6)))
        self.sql = sql
        if sql:
            Button(bar, text="Copy query", icon_name="copy", variant="ghost", size="sm", command=self.copy_query,
                   tooltip="Copy the query (SQL) only").pack(side="right", padx=(0, Theme.px(6)))
        self.hint = tk.Label(bar, bg=bg, fg=Palette.TEXT_MUTED, anchor="w", justify="left", font=Theme.font("small"),
                             text=f"Click a column to sort. Select rows, then {'⌘' if IS_MAC else 'Ctrl+'}C "
                                  "or Copy: they paste into a spreadsheet as columns.")
        self.hint.pack(side="left", fill="x", expand=True)
        bar.bind("<Configure>", lambda e: self.hint.config(wraplength=max(120, e.width - Theme.px(400))), add="+")

        grid = tk.Frame(self.window, bg=bg)
        grid.pack(fill="both", expand=True, padx=pad)
        style = ttk.Style(self.window)
        style.configure("Results.Treeview", rowheight=Theme.px(24), font=Theme.font("small"))
        style.configure("Results.Treeview.Heading", font=Theme.font("small", "semibold"))
        ids = [f"c{i}" for i in range(len(self.columns))]
        self.tree = ttk.Treeview(grid, columns=ids, show="headings", selectmode="extended",
                                 style="Results.Treeview")
        for cid, name in zip(ids, self.columns):
            self.tree.heading(cid, text=name, anchor="w", command=lambda c=cid: self.sort(c))
            longest = max([len(name)] + [len(str(r[ids.index(cid)])) for r in self.rows[:300]])
            self.tree.column(cid, width=Theme.px(min(320, max(70, 8 * longest + 20))), anchor="w",
                             stretch=cid == ids[-1])         # the last column takes any room left
        ys = ttk.Scrollbar(grid, orient="vertical", command=self.tree.yview)
        xs = ttk.Scrollbar(grid, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        grid.rowconfigure(0, weight=1)
        grid.columnconfigure(0, weight=1)
        self._fill(self.rows)

        self.tree.bind(f"<{MOD}-c>", lambda e: (self.copy(), "break")[1])
        self.tree.bind(f"<{MOD}-C>", lambda e: (self.copy(), "break")[1])
        self.tree.bind(f"<{MOD}-a>", lambda e: (self.select_all(), "break")[1])
        self.menu = tk.Menu(self.window, tearoff=0)
        self.menu.add_command(label="Copy selected rows", command=self.copy)
        self.menu.add_command(label="Copy this cell", command=lambda: self.copy_cell())
        self.menu.add_separator()
        self.menu.add_command(label="Select all", command=self.select_all)
        for button in ("<Button-3>", "<Button-2>") if IS_MAC else ("<Button-3>",):
            self.tree.bind(button, self._show_menu, add="+")
        self._menu_cell = None
        self.window.bind("<Escape>", lambda e: self.window.destroy())
        self.tree.focus_set()

    # ------------------------------------------------------------------ content
    def _fill(self, rows):
        self.tree.delete(*self.tree.get_children())
        for n, row in enumerate(rows):
            self.tree.insert("", "end", iid=str(n), values=row)
        self.shown = rows

    def sort(self, cid):
        index = int(cid[1:])
        column, descending = self._sorted
        descending = (not descending) if column == cid else False
        self._sorted = (cid, descending)
        rows = sorted(self.rows, key=lambda r: _sort_key(r[index]), reverse=descending)
        self._fill(rows)
        arrow = icon("chevron-down" if descending else "chevron-up", 12, Palette.TEXT_MUTED)
        for i, name in enumerate(self.columns):
            self.tree.heading(f"c{i}", text=name, image=arrow if f"c{i}" == cid else "")

    def selected_rows(self):
        return [self.shown[int(iid)] for iid in self.tree.selection()]

    def select_all(self):
        self.tree.selection_set(self.tree.get_children())

    # ------------------------------------------------------------------ copy and save
    def _clip(self, text):
        self.window.clipboard_clear()
        self.window.clipboard_append(text)

    def copy(self):
        """The selected rows (all of them when none is selected), with the column names."""
        rows = self.selected_rows() or self.shown
        self._clip(table_export.as_tsv(self.columns, rows))
        self.hint.config(text=f"Copied {len(rows)} row{'s' if len(rows) != 1 else ''} with the column names.")
        return rows

    def copy_query(self):
        self._clip(self.sql)
        self.hint.config(text="Copied the query.")
        return self.sql

    def _show_menu(self, event):
        row, column = self.tree.identify_row(event.y), self.tree.identify_column(event.x)
        if row and row not in self.tree.selection():
            self.tree.selection_set(row)
        self._menu_cell = (row, int(column[1:]) - 1) if row and column else None
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def copy_cell(self, cell=None):
        cell = cell or self._menu_cell
        if not cell:
            return ""
        value = str(self.shown[int(cell[0])][cell[1]])
        self._clip(value)
        self.hint.config(text="Copied the cell.")
        return value

    def save(self, kind, target=None):
        label, ext = ("Excel workbook", ".xlsx") if kind == "xlsx" else ("CSV file", ".csv")
        if target is None:
            target = filedialog.asksaveasfilename(
                parent=self.window, title=f"Save as {label}", defaultextension=ext,
                initialdir=os.path.expanduser("~/Documents") if os.path.isdir(os.path.expanduser("~/Documents"))
                else os.path.expanduser("~"),
                initialfile=default_name(self.title) + ext, filetypes=[(label, f"*{ext}")])
        if not target:
            return None
        try:
            (table_export.to_xlsx if kind == "xlsx" else table_export.to_csv)(target, self.columns, self.shown)
        except Exception as error:
            logger.exception(f"Saving the results failed: {error}")
            messagebox.showerror("Save results", f"The results could not be saved.\n\n{error}", parent=self.window)
            return None
        self.hint.config(text=f"Saved {os.path.basename(target)} in {os.path.dirname(target)}. It may hold "
                              "patient information: keep it somewhere safe.")
        if callable(self.open_after_save):
            self.open_after_save(target)
        return target
