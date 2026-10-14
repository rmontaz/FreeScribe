"""
FormFillWindow.py

Review window for a PDF form filled from a session.

Every field of the form is listed with its label and an editor. Fields the AI
filled are marked, fields whose answer will not fit the box on the page are
flagged, and nothing is written to disk until the clinician chooses
"Save filled PDF". The original PDF is never changed.

This software is released under the AGPL-3.0 license.
"""

import os
import platform
import subprocess
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import UI.Helpers
from UI.Theme import Theme, Palette
from UI.Widgets.Controls import Button, ScrollFrame
from services import form_filler as ff
from utils.log_config import logger

AI_MARK = "AI"
PDF_MARK = "From PDF"


class FormFillWindow:
    """
    :param parent: the main window
    :param pdf_path: the blank form
    :param fields: fields from :func:`services.form_filler.read_form`
    :param values: {field name: value} suggested by the AI (already normalised)
    :param session_title: used to suggest the file name
    :param on_saved: called with the path of the saved copy
    :param fill_path: the PDF the values are written into, when it is not ``pdf_path``
        (a flat form gets a fillable copy)
    :param converted: True when FreeScribe added the fields itself (a flat form)
    """

    def __init__(self, parent, pdf_path, fields, values, session_title="", on_saved=None,
                 fill_path=None, converted=False):
        self.parent = parent
        self.pdf_path = pdf_path
        self.fill_path = fill_path or pdf_path
        self.converted = converted
        self.fields = list(fields)
        self.values = dict(values)
        self.session_title = session_title
        self.on_saved = on_saved
        self.rows = []

        self.window = tk.Toplevel(parent)
        self.window.title("Fill PDF form")
        self.window.configure(bg=Palette.BG)
        UI.Helpers.set_window_icon(self.window)
        self.window.transient(parent)
        self.window.minsize(Theme.px(560), Theme.px(420))
        width, height = Theme.px(780), Theme.px(680)
        parent.update_idletasks()
        x = parent.winfo_rootx() + max(0, (parent.winfo_width() - width) // 2)
        y = parent.winfo_rooty() + max(0, (parent.winfo_height() - height) // 3)
        self.window.geometry(f"{width}x{height}+{x}+{y}")
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.bind("<Escape>", lambda e: self.close())

        self._build()
        self.window.focus_set()

    # ------------------------------------------------------------------ layout
    def _build(self):
        pad = Theme.px(22)
        filled = sum(1 for f in self.fields if self.values.get(f.name))
        asked = sum(1 for f in self.fields if f.needs_answer)

        header = tk.Frame(self.window, bg=Palette.BG)
        header.pack(fill="x", padx=pad, pady=(Theme.px(18), 0))
        tk.Label(header, text="Review the form", bg=Palette.BG, fg=Palette.TEXT,
                 font=Theme.font("large", "semibold"), anchor="w").pack(fill="x")
        tk.Label(header, text=os.path.basename(self.pdf_path), bg=Palette.BG, fg=Palette.TEXT_MUTED,
                 font=Theme.font("small"), anchor="w").pack(fill="x", pady=(Theme.px(2), 0))

        notice = tk.Frame(self.window, bg=Palette.WARNING_SOFT, highlightthickness=1,
                          highlightbackground=Palette.WARNING_BORDER)
        notice.pack(fill="x", padx=pad, pady=(Theme.px(12), Theme.px(8)))
        notice_label = tk.Label(notice, bg=Palette.WARNING_SOFT, fg=Palette.TEXT, justify="left", anchor="w",
                                font=Theme.font("small"),
                                text=self._notice_text(filled, asked))
        notice_label.pack(fill="x", padx=Theme.px(12), pady=Theme.px(8))
        notice.bind("<Configure>", lambda e: notice_label.configure(wraplength=max(100, e.width - Theme.px(28))))

        controls = tk.Frame(self.window, bg=Palette.BG)
        controls.pack(fill="x", padx=pad, pady=(0, Theme.px(6)))
        self.show_empty = tk.BooleanVar(value=True)
        ttk.Checkbutton(controls, text="Show blank fields", variable=self.show_empty,
                        command=self._apply_filter).pack(side="left")
        self.count_label = tk.Label(controls, bg=Palette.BG, fg=Palette.TEXT_MUTED, font=Theme.font("small"))
        self.count_label.pack(side="right")

        tk.Frame(self.window, bg=Palette.BORDER, height=1).pack(fill="x", padx=pad)
        self.scroll = ScrollFrame(self.window, bg=Palette.BG)
        self.scroll.pack(fill="both", expand=True, padx=(pad, Theme.px(8)))
        self.scroll.body.grid_columnconfigure(1, weight=1)
        tk.Frame(self.window, bg=Palette.BORDER, height=1).pack(fill="x", padx=pad)

        pages = len({f.page for f in self.fields}) > 1
        for index, field in enumerate(self.fields):
            self.rows.append(_FieldRow(self.scroll.body, index, field, self._initial_value(field),
                                       ai_filled=bool(self.values.get(field.name)) and field.needs_answer,
                                       show_page=pages, on_change=self._update_count))

        footer = tk.Frame(self.window, bg=Palette.BG)
        footer.pack(fill="x", padx=pad, pady=Theme.px(14))
        self.open_after = tk.BooleanVar(value=True)
        ttk.Checkbutton(footer, text="Open the PDF after saving", variable=self.open_after).pack(side="left")
        Button(footer, text="Save filled PDF…", variant="primary", icon_name="download",
               command=self.save).pack(side="right")
        Button(footer, text="Cancel", variant="neutral", command=self.close).pack(
            side="right", padx=(0, Theme.px(8)))

        self._apply_filter()

    def _notice_text(self, filled, asked):
        text = (f"FreeScribe filled {filled} of {asked} fields from this session. "
                "AI answers can be wrong or incomplete, so check every value before you save. "
                "Blank fields were not found in the session.")
        if self.converted:
            text = ("This PDF had no fillable fields, so FreeScribe added one wherever it found a blank "
                    f"(lines, boxes, tick boxes). Open the saved PDF to check each answer landed in the "
                    f"right place.\n\n{text}")
        return text

    def _initial_value(self, field):
        value = self.values.get(field.name, "")
        if not value and field.existing:
            if field.kind == ff.CHECKBOX:
                return "yes"
            return field.existing
        return value

    def _apply_filter(self):
        show_empty = self.show_empty.get()
        row_index = 0
        for row in self.rows:
            visible = show_empty or bool(row.value())
            row.place_at(row_index if visible else None)
            if visible:
                row_index += 1
        self._update_count()
        self.scroll.scroll_to_top()

    def _update_count(self):
        filled = sum(1 for row in self.rows if row.value())
        self.count_label.configure(text=f"{filled} of {len(self.rows)} fields have a value")

    # ------------------------------------------------------------------ actions
    def collect(self):
        """{field name: value} for every field, as :func:`fill_pdf` expects."""
        return {row.field.name: row.value() for row in self.rows}

    def save(self):
        folder = os.path.dirname(self.pdf_path)
        target = filedialog.asksaveasfilename(
            parent=self.window, title="Save filled form", defaultextension=".pdf",
            initialdir=folder, initialfile=ff.default_output_name(self.pdf_path, self.session_title),
            filetypes=[("PDF", "*.pdf")])
        if not target:
            return
        if os.path.abspath(target) == os.path.abspath(self.pdf_path):
            messagebox.showwarning("Fill PDF form", "Choose a new name so the blank form is kept.",
                                   parent=self.window)
            return
        try:
            ff.fill_pdf(self.fill_path, target, self.fields, self.collect())
        except Exception as error:
            logger.exception(f"Saving filled form failed: {error}")
            messagebox.showerror("Fill PDF form", f"The form could not be saved.\n\n{error}", parent=self.window)
            return
        if self.open_after.get():
            open_file(target)
        self.close()
        if callable(self.on_saved):
            self.on_saved(target)

    def close(self):
        if self.window.winfo_exists():
            self.window.destroy()


class _FieldRow:
    """Label, editor and status mark for one field, laid out on two grid rows."""

    def __init__(self, parent, index, field, value, ai_filled, show_page, on_change):
        self.field = field
        self.on_change = on_change
        self.ai_filled = ai_filled
        self.widgets = []

        label_text = field.label + (f"   · page {field.page + 1}" if show_page else "")
        self.label = tk.Label(parent, text=label_text, bg=Palette.BG, fg=Palette.TEXT, anchor="nw",
                              justify="left", font=Theme.font("base", "medium"), wraplength=Theme.px(230))
        self.editor_frame = tk.Frame(parent, bg=Palette.BG)
        self.mark = tk.Label(parent, bg=Palette.BG, font=Theme.font("small", "semibold"), anchor="ne")
        self.widgets = [self.label, self.editor_frame, self.mark]
        self._build_editor(value)
        self._refresh_mark()

    def _build_editor(self, value):
        field = self.field
        frame = self.editor_frame
        if field.kind == ff.CHECKBOX:
            self.var = tk.BooleanVar(value=value == "yes")
            ttk.Checkbutton(frame, text="Ticked", variable=self.var, command=self._changed).pack(anchor="w")
        elif field.kind in (ff.RADIO, ff.CHOICE):
            self.var = tk.StringVar(value=field.option_label(value) if value else "")
            box = ttk.Combobox(frame, textvariable=self.var, values=[""] + field.labels(), state="readonly")
            box.pack(fill="x")
            box.bind("<<ComboboxSelected>>", lambda e: self._changed())
        elif field.multiline:
            border = tk.Frame(frame, bg=Palette.BORDER_STRONG, padx=1, pady=1)
            border.pack(fill="x")
            self.text = tk.Text(border, height=4, wrap="word", relief="flat", bd=0, font=Theme.font("base"),
                                padx=Theme.px(8), pady=Theme.px(6), bg=Palette.BG, fg=Palette.TEXT,
                                insertbackground=Palette.TEXT, highlightthickness=0)
            self.text.pack(fill="x")
            self.text.insert("1.0", value)
            self.text.bind("<<Modified>>", self._text_modified)
            self.text.edit_modified(False)
            self.var = None
        else:
            self.var = tk.StringVar(value=value)
            border = tk.Frame(frame, bg=Palette.BORDER_STRONG, padx=1, pady=1)
            border.pack(fill="x")
            inner = tk.Frame(border, bg=Palette.BG)
            inner.pack(fill="x")
            entry = tk.Entry(inner, textvariable=self.var, relief="flat", bd=0, font=Theme.font("base"),
                             bg=Palette.BG, fg=Palette.TEXT, insertbackground=Palette.TEXT, highlightthickness=0)
            entry.pack(fill="x", ipady=Theme.px(5), padx=Theme.px(8))
            self.var.trace_add("write", lambda *a: self._changed())

    def _text_modified(self, _event=None):
        if self.text.edit_modified():
            self.text.edit_modified(False)
            self._changed()

    def _changed(self):
        self._refresh_mark()
        self.on_change()

    def value(self):
        field = self.field
        if field.kind == ff.CHECKBOX:
            return "yes" if self.var.get() else ""
        if field.kind in (ff.RADIO, ff.CHOICE):
            return field.option_value(self.var.get()) if self.var.get() else ""
        if field.multiline:
            return self.text.get("1.0", "end-1c").strip()
        return self.var.get().strip()

    def _refresh_mark(self):
        value = self.value()
        limit = ff.capacity(self.field) if self.field.kind == ff.TEXT else None
        if limit and len(value) > limit:
            self.mark.configure(text="Too long for the box", fg=Palette.WARNING)
        elif self.ai_filled and value:
            self.mark.configure(text=AI_MARK, fg=Palette.PRIMARY)
        elif self.field.existing and value:
            self.mark.configure(text=PDF_MARK, fg=Palette.TEXT_MUTED)
        else:
            self.mark.configure(text="")

    def place_at(self, position):
        """Grid the row at ``position`` (None hides it)."""
        if position is None:
            for widget in self.widgets:
                widget.grid_remove()
            return
        pad_y = (Theme.px(10), 0)
        self.label.grid(row=position, column=0, sticky="nw", padx=(0, Theme.px(14)), pady=pad_y)
        self.editor_frame.grid(row=position, column=1, sticky="ew", pady=pad_y)
        self.mark.grid(row=position, column=2, sticky="ne", padx=(Theme.px(10), Theme.px(8)), pady=pad_y)
        self.label.master.grid_columnconfigure(0, minsize=Theme.px(240))
        self.label.master.grid_columnconfigure(2, minsize=Theme.px(130))


def open_file(path):
    """Open ``path`` with the system's default application."""
    try:
        system = platform.system()
        if system == "Darwin":
            subprocess.Popen(["open", path])
        elif system == "Windows":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception as error:
        logger.exception(f"Could not open {path}: {error}")
