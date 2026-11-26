"""
MenuPopup.py

The drop-down menus of the in-window menu bar. They are drawn by the app (not by the operating
system), so File, Edit, View and the rest look and behave the same on macOS, Windows and Linux.

A popup reads its rows from a ``tk.Menu`` that is used purely as a list of items, so the menu
definitions in MenuBar.py stay plain and testable.

This software is released under the AGPL-3.0 license.
"""

import tkinter as tk

from UI.Theme import Palette, Theme

ROW = 30          # design pixels
PAD = 6
MIN_WIDTH = 230


class MenuPopup(tk.Toplevel):
    """One drop-down list. ``on_close`` runs when it goes away; ``on_move`` handles Left and Right."""

    def __init__(self, owner, source, x, y, on_close=None, on_move=None, parent_popup=None):
        super().__init__(owner)
        self.source = source
        self.on_close = on_close
        self.on_move = on_move
        self.parent_popup = parent_popup
        self.child = None
        self.hover = None
        self._after = None
        self.overrideredirect(True)
        try:
            self.attributes("-topmost", True)
            self.attributes("-alpha", 0.0)
        except tk.TclError:
            pass
        self.rows = self._read_rows()
        self.configure(bg=Palette.BORDER_STRONG)
        width, height = self._measure()
        self.canvas = tk.Canvas(self, width=width, height=height, bg=Palette.SURFACE,
                                highlightthickness=0, bd=0, cursor="arrow")
        self.canvas.pack(padx=1, pady=1)
        self.width, self.height = width, height
        self._place(x, y, width + 2, height + 2)
        self._draw()
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", self._on_leave)
        self.canvas.bind("<ButtonRelease-1>", self._on_click)
        for key, handler in (("<Escape>", self._escape), ("<Up>", lambda e: self._step(-1)),
                             ("<Down>", lambda e: self._step(1)), ("<Return>", self._activate_hover),
                             ("<Left>", self._left), ("<Right>", self._right)):
            self.bind(key, handler)
        self.after(10, self._fade_in)
        try:
            self.focus_force()
        except tk.TclError:
            pass

    # ------------------------------------------------------------------ rows
    def _read_rows(self):
        rows = []
        last = self.source.index("end")
        if last is None:
            return rows
        for i in range(last + 1):
            kind = self.source.type(i)
            if kind == "separator":
                rows.append({"kind": "separator"})
                continue
            if kind == "tearoff":
                continue
            label = self.source.entrycget(i, "label")
            try:
                accelerator = self.source.entrycget(i, "accelerator")
            except tk.TclError:
                accelerator = ""
            state = str(self.source.entrycget(i, "state"))
            row = {"kind": kind, "index": i, "label": label, "accel": accelerator,
                   "enabled": state != "disabled", "checked": False}
            if kind == "radiobutton":
                variable = self.source.entrycget(i, "variable")
                try:
                    row["checked"] = self.source.getvar(variable) == self.source.entrycget(i, "value")
                except tk.TclError:
                    pass
            if kind == "cascade":
                row["submenu"] = self.source.nametowidget(self.source.entrycget(i, "menu"))
            rows.append(row)
        return rows

    def _measure(self):
        font = Theme.font("base")
        accel_font = Theme.font("small")
        left = max((font.measure(r["label"]) for r in self.rows if r["kind"] != "separator"), default=0)
        right = max((accel_font.measure(r["accel"]) for r in self.rows if r.get("accel")), default=0)
        width = max(Theme.px(MIN_WIDTH), left + right + Theme.px(34 + 40 + (24 if right else 0)))
        height = sum(Theme.px(9) if r["kind"] == "separator" else Theme.px(ROW) for r in self.rows) + Theme.px(PAD * 2)
        return width, height

    def _row_boxes(self):
        y = Theme.px(PAD)
        for row in self.rows:
            h = Theme.px(9) if row["kind"] == "separator" else Theme.px(ROW)
            yield row, y, y + h
            y += h

    # ------------------------------------------------------------------ drawing
    def _draw(self):
        c = self.canvas
        c.delete("all")
        inset = Theme.px(5)
        font = Theme.font("base")
        accel_font = Theme.font("small")
        for position, (row, top, bottom) in enumerate(self._row_boxes()):
            if row["kind"] == "separator":
                mid = (top + bottom) // 2
                c.create_line(Theme.px(14), mid, self.width - Theme.px(14), mid, fill=Palette.BORDER)
                continue
            active = position == self.hover and row["enabled"]
            if active:
                self._pill(inset, top, self.width - inset, bottom,
                           Palette.PRIMARY_SOFT if Theme.mode == "light" else Palette.HOVER)
            colour = Palette.TEXT if row["enabled"] else Palette.DISABLED_TEXT
            if active:
                colour = Palette.PRIMARY_PRESSED if Theme.mode == "light" else Palette.TEXT
            middle = (top + bottom) // 2
            if row["checked"]:
                c.create_text(Theme.px(20), middle, text="●", fill=Palette.PRIMARY if row["enabled"] else colour,
                              font=Theme.font("micro"))
            c.create_text(Theme.px(34), middle, text=row["label"], anchor="w", fill=colour, font=font)
            if row["kind"] == "cascade":
                self._chevron(self.width - Theme.px(20), middle, colour)
            elif row.get("accel"):
                c.create_text(self.width - Theme.px(18), middle, text=row["accel"], anchor="e",
                              fill=Palette.TEXT_FAINT if row["enabled"] else Palette.DISABLED_TEXT, font=accel_font)

    def _pill(self, x1, y1, x2, y2, fill):
        r = Theme.px(6)
        c = self.canvas
        c.create_rectangle(x1 + r, y1, x2 - r, y2, fill=fill, outline=fill)
        c.create_rectangle(x1, y1 + r, x2, y2 - r, fill=fill, outline=fill)
        for cx, cy in ((x1, y1), (x2 - 2 * r, y1), (x1, y2 - 2 * r), (x2 - 2 * r, y2 - 2 * r)):
            c.create_oval(cx, cy, cx + 2 * r, cy + 2 * r, fill=fill, outline=fill)

    def _chevron(self, x, y, colour):
        s = Theme.px(3)
        self.canvas.create_line(x - s, y - s - 1, x + 1, y, x - s, y + s + 1, fill=colour,
                                width=max(1, Theme.px(1)), capstyle="round", joinstyle="round")

    # ------------------------------------------------------------------ placement and motion
    def _place(self, x, y, w, h):
        screen_w, screen_h = self.winfo_screenwidth(), self.winfo_screenheight()
        x = max(0, min(x, screen_w - w - 4))
        y = max(0, min(y, screen_h - h - 4))
        self.geometry(f"+{x}+{y}")

    def _fade_in(self, step=0):
        try:
            self.attributes("-alpha", min(1.0, (step + 1) / 4))
        except tk.TclError:
            return
        if step < 3:
            self.after(16, lambda: self._fade_in(step + 1))

    # ------------------------------------------------------------------ input
    def _row_at(self, y):
        for position, (row, top, bottom) in enumerate(self._row_boxes()):
            if top <= y < bottom:
                return position
        return None

    def _selectable(self, position):
        return position is not None and self.rows[position]["kind"] != "separator"

    def _on_motion(self, event):
        position = self._row_at(event.y)
        if not self._selectable(position):
            position = None
        if position != self.hover:
            self._set_hover(position)

    def _on_leave(self, event):
        if self.child is None:
            self._set_hover(None)

    def _set_hover(self, position):
        self.hover = position
        self._draw()
        if self._after:
            self.after_cancel(self._after)
            self._after = None
        row = self.rows[position] if position is not None else None
        if self.child is not None and (row is None or row.get("submenu") is not self.child.source):
            self.child.close()
            self.child = None
        if row is not None and row["kind"] == "cascade" and row["enabled"]:
            self._after = self.after(120, lambda: self._open_child(position))

    def _open_child(self, position):
        self._after = None
        if self.hover != position or self.child is not None:
            return
        row = self.rows[position]
        top = list(self._row_boxes())[position][1]
        x = self.winfo_rootx() + self.winfo_width() - 4
        y = self.winfo_rooty() + top - Theme.px(PAD)
        self.child = MenuPopup(self.master, row["submenu"], x, y, on_close=self._child_closed,
                               parent_popup=self)

    def _child_closed(self):
        self.child = None

    def _on_click(self, event):
        position = self._row_at(event.y)
        if not self._selectable(position):
            return
        row = self.rows[position]
        if not row["enabled"]:
            return
        if row["kind"] == "cascade":
            self._open_child(position) if self.child is None else None
            return
        source, index = self.source, row["index"]
        self.close_all()
        self.master.after(30, lambda: source.invoke(index))

    def _step(self, direction):
        count = len(self.rows)
        position = self.hover if self.hover is not None else (-1 if direction > 0 else count)
        for _ in range(count):
            position = (position + direction) % count
            row = self.rows[position]
            if row["kind"] != "separator" and row["enabled"]:
                self._set_hover(position)
                return

    def _activate_hover(self, event=None):
        if self.hover is None:
            return
        row = self.rows[self.hover]
        if row["kind"] == "cascade":
            self._open_child(self.hover)
            if self.child is None:
                self._after = None
                self._open_child(self.hover)
            return
        source, index = self.source, row["index"]
        self.close_all()
        self.master.after(30, lambda: source.invoke(index))

    def _right(self, event=None):
        if self.hover is not None and self.rows[self.hover]["kind"] == "cascade":
            self._activate_hover()
        elif self.on_move:
            self.on_move(1)

    def _left(self, event=None):
        if self.parent_popup is not None:
            self.close()
            try:
                self.parent_popup.focus_force()
            except tk.TclError:
                pass
        elif self.on_move:
            self.on_move(-1)

    def _escape(self, event=None):
        self.close_all()

    # ------------------------------------------------------------------ closing
    def close(self):
        if self.child is not None:
            self.child.close()
        if self._after:
            try:
                self.after_cancel(self._after)
            except tk.TclError:
                pass
        callback, self.on_close = self.on_close, None
        try:
            self.destroy()
        except tk.TclError:
            pass
        if callback:
            callback()

    def close_all(self):
        top = self
        while top.parent_popup is not None:
            top = top.parent_popup
        top.close()

    def contains(self, widget):
        """Whether ``widget`` belongs to this popup or one opened from it."""
        popup = self
        while popup is not None:
            if widget is not None and str(widget).startswith(str(popup)):
                return True
            popup = popup.child
        return False
