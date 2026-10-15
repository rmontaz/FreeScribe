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
from UI.Widgets.Controls import Button, LevelMeter, ScrollFrame
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
    :param talk: optional controller for talking through the form (see :class:`TalkPanel`);
        when given, the window offers "Talk to fill"
    :param from_session: True when ``values`` came from the session (False: the form opened blank)
    :param profile: optional (get, save) pair for the clinician's saved details; when given the
        window offers "Your details…" and fills the clinician boxes from them
    """

    def __init__(self, parent, pdf_path, fields, values, session_title="", on_saved=None,
                 fill_path=None, converted=False, talk=None, from_session=True, profile=None):
        self.parent = parent
        self.pdf_path = pdf_path
        self.fill_path = fill_path or pdf_path
        self.converted = converted
        self.fields = list(fields)
        self.values = dict(values)
        self.session_title = session_title
        self.on_saved = on_saved
        self.saved_values = {}
        self.rows = []
        self.talk = talk
        self.from_session = from_session
        self.talk_panel = None
        self.profile = profile

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

        if self.talk is not None:
            self.talk_panel = TalkPanel(self.window, self.talk, on_values=self.apply_values,
                                        on_state=self._talk_state_changed)
            self.talk_panel.frame.pack(fill="x", padx=pad, pady=(Theme.px(12), 0))

        notice = tk.Frame(self.window, bg=Palette.WARNING_SOFT, highlightthickness=1,
                          highlightbackground=Palette.WARNING_BORDER)
        notice.pack(fill="x", padx=pad, pady=(Theme.px(12), Theme.px(8)))
        self.notice_label = notice_label = tk.Label(notice, bg=Palette.WARNING_SOFT, fg=Palette.TEXT,
                                                    justify="left", anchor="w", font=Theme.font("small"),
                                                    text=self._notice_text(filled, asked))
        notice_label.pack(fill="x", padx=Theme.px(12), pady=Theme.px(8))
        notice.bind("<Configure>", lambda e: notice_label.configure(wraplength=max(100, e.width - Theme.px(28))))

        controls = tk.Frame(self.window, bg=Palette.BG)
        controls.pack(fill="x", padx=pad, pady=(0, Theme.px(6)))
        self.show_empty = tk.BooleanVar(value=True)
        ttk.Checkbutton(controls, text="Show blank fields", variable=self.show_empty,
                        command=self._apply_filter).pack(side="left")
        if self.profile is not None and any(f.role == ff.CLINICIAN for f in self.fields):
            Button(controls, text="Your details…", icon_name="settings", variant="ghost", size="sm",
                   command=self.edit_profile,
                   tooltip="Your name, specialty and clinic details, used for the clinician boxes").pack(
                side="left", padx=(Theme.px(12), 0))
        self.count_label = tk.Label(controls, bg=Palette.BG, fg=Palette.TEXT_MUTED, font=Theme.font("small"))
        self.count_label.pack(side="right")

        tk.Frame(self.window, bg=Palette.BORDER, height=1).pack(fill="x", padx=pad)
        self.scroll = ScrollFrame(self.window, bg=Palette.BG)
        self.scroll.pack(fill="both", expand=True, padx=(pad, Theme.px(8)))
        self.scroll.body.grid_columnconfigure(1, weight=1)

        pages = len({f.page for f in self.fields}) > 1
        for index, field in enumerate(self.fields):
            self.rows.append(_FieldRow(self.scroll.body, index, field, self._initial_value(field),
                                       ai_filled=bool(self.values.get(field.name)) and field.needs_answer,
                                       show_page=pages, on_change=self._update_count))

        # The footer is packed ahead of the field list so a long form never pushes Save out of the window
        footer = tk.Frame(self.window, bg=Palette.BG)
        footer.pack(side="bottom", fill="x", padx=pad, pady=Theme.px(14), before=self.scroll)
        tk.Frame(self.window, bg=Palette.BORDER, height=1).pack(side="bottom", fill="x", padx=pad,
                                                                before=self.scroll)
        self.open_after = tk.BooleanVar(value=True)
        ttk.Checkbutton(footer, text="Open the PDF after saving", variable=self.open_after).pack(side="left")
        self.save_button = Button(footer, text="Save filled PDF…", variant="primary", icon_name="download",
                                  command=self.save)
        self.save_button.pack(side="right")
        Button(footer, text="Cancel", variant="neutral", command=self.close).pack(
            side="right", padx=(0, Theme.px(8)))

        self._apply_filter()

    def _notice_text(self, filled, asked, talked=False):
        if talked:
            text = (f"FreeScribe filled {filled} of {asked} fields from what you said and this session. "
                    "AI answers can be wrong or incomplete, so check every value before you save.")
        elif not self.from_session:
            text = ("Fill in the fields yourself" + (", or talk through the form above and FreeScribe fills them"
                                                     if self.talk is not None else "") + ". "
                    "Check every value before you save.")
        else:
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

    def _talk_state_changed(self, state):
        """No saving half way through: the form is filled when talking is done."""
        if hasattr(self, "save_button"):
            self.save_button.set_enabled(state == "idle")

    # ------------------------------------------------------------------ actions
    def apply_values(self, values):
        """Puts AI answers (from talking through the form) into the fields; blanks leave a field as it is."""
        changed = 0
        for row in self.rows:
            value = values.get(row.field.name, "")
            if value and value != row.value():
                row.ai_filled = True
                row.set_value(value)
                changed += 1
        filled = sum(1 for row in self.rows if row.value())
        asked = sum(1 for f in self.fields if f.needs_answer)
        self.notice_label.configure(text=self._notice_text(filled, asked, talked=True))
        self._apply_filter()
        return changed

    def edit_profile(self):
        """Edit the clinician's saved details and put them into this form's clinician boxes."""
        from UI.ClinicianProfileWindow import ClinicianProfileWindow
        get, save = self.profile

        def saved(details):
            save(details)
            values = ff.clinician_values(self.fields, details)
            for row in self.rows:
                if row.field.role == ff.CLINICIAN and row.field.name in values:
                    row.set_value(values[row.field.name])
            self._update_count()

        ClinicianProfileWindow(self.window, get(), saved)

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
        values = self.collect()
        try:
            ff.fill_pdf(self.fill_path, target, self.fields, values)
        except Exception as error:
            logger.exception(f"Saving filled form failed: {error}")
            messagebox.showerror("Fill PDF form", f"The form could not be saved.\n\n{error}", parent=self.window)
            return
        self.saved_values = values      # what was written, readable after the window closes
        if self.open_after.get():
            open_file(target)
        self.close()
        if callable(self.on_saved):
            self.on_saved(target)

    def close(self):
        if self.talk_panel is not None:
            self.talk_panel.cancel()
        if self.window.winfo_exists():
            self.window.destroy()


class TalkPanel:
    """
    "Talk to fill": record the clinician talking through the form, transcribe it and
    have the AI fill the fields from it.

    ``talk`` does the work; it needs:

    - ``start()``: start recording (raises an exception with a readable message if it cannot)
    - ``level()`` and ``elapsed()``: input level 0-1 and seconds recorded, for the meter
    - ``finish(on_text, on_error)``: stop and transcribe; calls back on the UI thread
    - ``fill(text, on_values, on_error)``: fill the form from ``text``; calls back on the UI thread
    - ``cancel()``: stop and throw the recording away
    """

    HINT = ("Press Start talking and say the details for this form in your own words, for example “off work "
            "from March 3 to March 10, light duties after that, no lifting over 10 kilograms”. Then press "
            "Stop and fill form. You can check and change every field before saving.")

    def __init__(self, parent, talk, on_values, on_state=None):
        self.talk = talk
        self.on_values = on_values
        self.on_state = on_state
        self.state = "idle"       # idle, recording, working
        bg = Palette.PRIMARY_SOFT
        self.frame = tk.Frame(parent, bg=bg)
        inner = tk.Frame(self.frame, bg=bg)
        inner.pack(fill="x", padx=Theme.px(14), pady=Theme.px(12))

        tk.Label(inner, text="Talk to fill", bg=bg, fg=Palette.TEXT, anchor="w",
                 font=Theme.font("medium", "semibold")).pack(fill="x")
        hint = tk.Label(inner, text=self.HINT, bg=bg, fg=Palette.TEXT_MUTED, anchor="w", justify="left",
                        font=Theme.font("small"))
        hint.pack(fill="x", pady=(Theme.px(2), Theme.px(8)))
        inner.bind("<Configure>", lambda e: hint.configure(wraplength=max(100, e.width)))

        row = tk.Frame(inner, bg=bg)
        row.pack(fill="x")
        self.main_button = Button(row, text="Start talking", icon_name="mic", variant="primary",
                                  command=self._main_action, surface=bg)
        self.main_button.pack(side="left")
        self.discard_button = Button(row, text="Discard", variant="ghost", command=self._discard, surface=bg)
        self.meter = LevelMeter(row, bars=7, height=22, surface=bg)
        self.time_label = tk.Label(row, bg=bg, fg=Palette.TEXT, font=Theme.font("base", "medium"))
        self.status = tk.Label(row, bg=bg, fg=Palette.TEXT_MUTED, anchor="w", font=Theme.font("small"))
        self.status.pack(side="left", padx=(Theme.px(12), 0), fill="x", expand=True)

        # What was said, editable, shown after the first recording
        self.said_frame = tk.Frame(inner, bg=bg)
        tk.Label(self.said_frame, text="What you said (you can edit it)", bg=bg, fg=Palette.TEXT_MUTED,
                 anchor="w", font=Theme.font("small", "medium")).pack(fill="x", pady=(Theme.px(10), Theme.px(4)))
        border = tk.Frame(self.said_frame, bg=Palette.BORDER_STRONG, padx=1, pady=1)
        border.pack(fill="x")
        self.said = tk.Text(border, height=3, wrap="word", relief="flat", bd=0, font=Theme.font("base"),
                            padx=Theme.px(8), pady=Theme.px(6), bg=Palette.BG, fg=Palette.TEXT,
                            insertbackground=Palette.TEXT, highlightthickness=0)
        self.said.pack(fill="x")
        actions = tk.Frame(self.said_frame, bg=bg)
        actions.pack(fill="x", pady=(Theme.px(6), 0))
        self.refill_button = Button(actions, text="Fill again from this text", icon_name="sparkle",
                                    variant="secondary", size="sm", command=self._refill, surface=bg)
        self.refill_button.pack(side="left")

    # ------------------------------------------------------------------ state
    def said_text(self) -> str:
        return self.said.get("1.0", "end-1c").strip()

    def _set_state(self, state, status="", error=False):
        self.state = state
        self.status.configure(text=status, fg=Palette.DANGER if error else Palette.TEXT_MUTED)
        recording = state == "recording"
        if recording:
            self.main_button.set(text="Stop and fill form", icon_name="stop", variant="danger")
            self.discard_button.pack(side="left", padx=(Theme.px(6), 0), after=self.main_button)
            self.meter.pack(side="left", padx=(Theme.px(12), 0), after=self.discard_button)
            self.time_label.pack(side="left", padx=(Theme.px(8), 0), after=self.meter)
        else:
            self.main_button.set(text="Talk again" if self.said_text() else "Start talking", icon_name="mic",
                                 variant="primary")
            for widget in (self.discard_button, self.meter, self.time_label):
                widget.pack_forget()
        self.main_button.set_enabled(state != "working")
        self.refill_button.set_enabled(state == "idle")
        if callable(self.on_state):
            self.on_state(state)

    def _main_action(self):
        if self.state == "idle":
            self.start()
        elif self.state == "recording":
            self.finish()

    def start(self):
        try:
            self.talk.start()
        except Exception as error:
            self._set_state("idle", str(error), error=True)
            return
        self._set_state("recording", "Listening… press Stop and fill form when you are done.")
        self._tick()

    def _tick(self):
        if self.state != "recording" or not self.frame.winfo_exists():
            return
        self.meter.set_color(Palette.PRIMARY)
        self.meter.set_level(self.talk.level())
        seconds = int(self.talk.elapsed())
        self.time_label.configure(text=f"{seconds // 60}:{seconds % 60:02d}")
        self.frame.after(80, self._tick)

    def finish(self):
        self._set_state("working", "Transcribing what you said…")
        self.talk.finish(self._got_text, self._failed)

    def _got_text(self, text):
        text = (text or "").strip()
        if not text:
            self._set_state("idle", "Nothing was heard. Check the microphone and try again.", error=True)
            return
        if not self.said_frame.winfo_manager():
            self.said_frame.pack(fill="x")
        existing = self.said_text()
        self.said.insert("end", ("\n" if existing else "") + text)
        self._fill(self.said_text())

    def _refill(self):
        text = self.said_text()
        if text and self.state == "idle":
            self._fill(text)

    def _fill(self, text):
        self._set_state("working", "Filling the form from what you said…")
        self.talk.fill(text, self._got_values, self._failed)

    def _got_values(self, values):
        if not self.frame.winfo_exists():
            return
        changed = self.on_values(values)
        filled = sum(1 for value in values.values() if value)
        if filled:
            self._set_state("idle", f"Filled {filled} field{'s' if filled != 1 else ''} from what you said"
                            + ("" if changed else " (nothing new)") + ". Check them, then Save filled PDF.")
        else:
            self._set_state("idle", "No field could be filled from what you said. Edit the text above and press "
                                    "Fill again, or fill the fields yourself.", error=True)

    def _failed(self, message):
        if self.frame.winfo_exists():
            self._set_state("idle", message, error=True)

    def _discard(self):
        self.talk.cancel()
        self._set_state("idle", "Recording discarded.")

    def cancel(self):
        if self.state == "recording":
            self.talk.cancel()
        self.state = "idle"


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

    def set_value(self, value):
        field = self.field
        if field.kind == ff.CHECKBOX:
            self.var.set(value == "yes")
        elif field.kind in (ff.RADIO, ff.CHOICE):
            self.var.set(field.option_label(value) if value else "")
        elif field.multiline:
            self.text.delete("1.0", "end")
            self.text.insert("1.0", value)
        else:
            self.var.set(value)
        self._changed()

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
