"""
form_filler.py

Fills a PDF form from what was said and written in a session.

The work is split so that everything except the model call can be tested
without a window or a model:

1. :func:`read_form` lists the fillable fields of a PDF and works out a human
   readable label for each one. Field names in real forms are often cryptic
   (``Text1``, ``topmostSubform[0].Page1[0].f1_06[0]``), so the label comes from
   the field's tooltip or, failing that, from the words printed next to it.
2. :func:`pseudonymize` replaces identifiers (phone numbers, emails, health card
   numbers ...) with numbered placeholders before anything is sent to a model,
   and :func:`restore` puts the real values back into the answers. The model
   never sees the identifiers, but they still end up in the form.
3. :func:`build_prompts` asks for the fields a few at a time, because local
   models have small context windows, and :func:`parse_answers` reads the
   replies, tolerating the formatting slips small models make.
4. :func:`fill_pdf` writes the reviewed values into a copy of the PDF.

Only real (AcroForm) form fields can be filled. A scanned or "flat" PDF has no
fields, and :class:`FormError` explains that to the user.

This software is released under the AGPL-3.0 license.
"""

from __future__ import annotations

import datetime as _dt
import difflib
import json
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, NameObject, TextStringObject

# Field kinds
TEXT = "text"
CHECKBOX = "checkbox"
RADIO = "radio"
CHOICE = "choice"

# Field flag bits (PDF 32000-1, section 12.7)
_FF_READ_ONLY = 1
_FF_MULTILINE = 1 << 12
_FF_RADIO = 1 << 15
_FF_PUSHBUTTON = 1 << 16

FIELDS_PER_PROMPT = 16
# A small model on this computer answers more accurately a few fields at a time
FIELDS_PER_PROMPT_LOCAL = 6
DICTATION_HEADING = "What the clinician said for this form:"

# Replies that mean "nothing to put here"
_EMPTY_ANSWERS = {
    "", "none", "null", "n/a", "na", "unknown", "not mentioned", "not discussed", "not stated",
    "not provided", "not available", "not specified", "not given", "-", "--", "?",
}
_YES = {"yes", "y", "true", "checked", "check", "x", "on", "1", "selected", "ticked"}
_NO = {"no", "n", "false", "unchecked", "off", "0", "not checked", "unticked"}


class FormError(Exception):
    """A PDF that cannot be filled. The message is written for the user."""


class NoFieldsError(FormError):
    """The PDF has no fillable fields (it may still be a flat form with blanks to fill)."""


@dataclass
class PreparedForm:
    """A form ready to fill."""

    original_path: str        # the PDF the user chose; never changed
    fill_path: str            # the PDF values are written into (a fillable copy for flat forms)
    fields: List["FormField"]
    converted: bool = False   # True when the blanks of a flat form were turned into fields


@dataclass
class FormField:
    """One fillable field of a PDF form."""

    name: str                 # fully qualified field name, used to write the value back
    kind: str                 # TEXT, CHECKBOX, RADIO or CHOICE
    label: str                # what the field is called on the page
    page: int = 0             # 0-based page index of the first widget
    options: List[str] = field(default_factory=list)   # RADIO / CHOICE values as stored in the PDF
    option_labels: List[str] = field(default_factory=list)  # what each option is called on the page
    on_state: str = "/Yes"    # CHECKBOX value when ticked
    multiline: bool = False
    max_length: Optional[int] = None
    existing: str = ""        # value already in the PDF; such fields are not asked for
    top: float = 0.0          # position on the page, for ordering
    line: str = ""            # the printed line when it holds several fields ("from … to")
    box: Tuple[float, float] = (0.0, 0.0)  # width and height of the field on the page, in points

    def describe(self) -> str:
        """How the field is described to the model."""
        name = self.label
        if self.line:
            # One of several fields on a printed line, like "Unable to work from … to …"
            part = self.label.split(" … ")[-1]
            name = f'{self.line} — only the part after "{part}"'
        if self.kind == CHECKBOX:
            return f"{name} (checkbox: yes or no)"
        if self.kind in (RADIO, CHOICE):
            return f"{name} (choose one: {' | '.join(self.labels())})"
        return f"{name} (text{', several lines allowed' if self.multiline else ''})"

    @property
    def needs_answer(self) -> bool:
        """Whether to ask the model. Text the PDF already holds (a clinic address,
        say) is kept; choices are asked even when the PDF preselects one."""
        return self.kind != TEXT or not self.existing

    def labels(self) -> List[str]:
        """Display names of the options, in the same order as :attr:`options`."""
        if len(self.option_labels) == len(self.options):
            return list(self.option_labels)
        return list(self.options)

    def option_label(self, value: str) -> str:
        """Display name of the option stored as ``value``."""
        for option, label in zip(self.options, self.labels()):
            if option == value:
                return label
        return value

    def option_value(self, label: str) -> str:
        """Stored value of the option displayed as ``label``."""
        for option, shown in zip(self.options, self.labels()):
            if shown == label:
                return option
        return label if label in self.options else ""


# =================================================================== reading
def prepare_form(path: str, work_dir: Optional[str] = None) -> PreparedForm:
    """
    Get the PDF at ``path`` ready to fill.

    A PDF with form fields is used as it is. A flat PDF (printed to PDF, no
    fields) is copied with a form field added on every blank FreeScribe can find
    (underscores, lines, empty boxes, tick boxes), and that copy is filled.

    :raises FormError: with a message for the user when the PDF cannot be filled.
    """
    try:
        return PreparedForm(path, path, read_form(path))
    except NoFieldsError:
        pass

    import os
    import tempfile
    try:
        from services import flat_form
    except ImportError as error:  # PyMuPDF missing
        raise FormError("This PDF has no fillable fields, and the add-on FreeScribe uses to find the "
                        "blanks on flat forms (PyMuPDF) is not installed.") from error

    try:
        if flat_form.is_scanned(path):
            raise FormError("This PDF is a scanned image, so there is no text FreeScribe can read. "
                            "Use the digital version of the form, or a copy with selectable text "
                            "(run it through text recognition, OCR, first).")
        blanks = flat_form.find_blanks(path)
    except FormError:
        raise
    except Exception as error:
        raise FormError(f"This file could not be read as a PDF ({error}).") from error
    if not blanks:
        raise FormError("This PDF has no fillable fields, and FreeScribe could not find any blanks to "
                        "fill (lines, underscores, empty boxes or tick boxes).")

    work_dir = work_dir or tempfile.mkdtemp(prefix="freescribe-form-")
    fillable = os.path.join(work_dir, os.path.basename(path))
    flat_form.make_fillable(path, fillable, blanks)
    return PreparedForm(path, fillable, read_form(fillable), converted=True)


def read_form(path: str) -> List[FormField]:
    """
    List the fillable fields of the PDF at ``path`` in reading order.

    :raises FormError: if the PDF cannot be opened or has no fillable fields.
    """
    try:
        reader = PdfReader(path)
        if reader.is_encrypted and not reader.decrypt(""):
            raise FormError("This PDF is password-protected. Remove the password and try again.")
    except FormError:
        raise
    except Exception as error:
        raise FormError(f"This file could not be read as a PDF ({error}).") from error

    raw = reader.get_fields() or {}
    acroform = reader.trailer["/Root"].get("/AcroForm")
    if not raw:
        if acroform is not None and "/XFA" in acroform.get_object():
            raise FormError("This PDF uses an Adobe XFA form, which FreeScribe cannot fill. "
                            "If the publisher offers a standard fillable PDF, use that instead.")
        raise NoFieldsError("This PDF has no fillable fields. FreeScribe can only fill PDFs with form "
                            "fields, the kind you can type into in Preview or Acrobat.")

    widgets = _collect_widgets(reader)
    lines_by_page: Dict[int, list] = {}

    fields: List[FormField] = []
    for name, data in raw.items():
        field_type = data.get("/FT")
        flags = int(data.get("/Ff", 0) or 0)
        if field_type not in ("/Tx", "/Btn", "/Ch") or flags & _FF_READ_ONLY:
            continue  # signatures, read-only and unknown fields
        if field_type == "/Btn" and flags & _FF_PUSHBUTTON:
            continue

        own_widgets = widgets.get(name, [])
        page, rect, _state = (own_widgets[0] if own_widgets else (0, None, None))
        if page not in lines_by_page:
            lines_by_page[page] = _text_lines(reader.pages[page])
        page_rects = [r for p, r in _all_rects(widgets) if p == page]

        kind, options, option_labels, on_state = _kind_of(data, flags)
        label = _label_for(name, data, kind, rect, lines_by_page[page], page_rects)
        if kind == RADIO:
            options, option_labels = _radio_options(reader, own_widgets, options, lines_by_page, widgets)
        max_length = data.get("/MaxLen")

        fields.append(FormField(
            name=name, kind=kind, label=label, page=page, options=options,
            option_labels=option_labels, on_state=on_state,
            multiline=bool(flags & _FF_MULTILINE),
            max_length=int(max_length) if max_length else None,
            existing=_existing_value(data, kind),
            top=-(rect[3] if rect else 0.0),
            box=((rect[2] - rect[0], rect[3] - rect[1]) if rect else (0.0, 0.0)),
        ))

    # Fields that share a printed line ("from … to"): describe each one against the whole line
    for later in fields:
        if " … " not in later.label:
            continue
        lead, short = later.label.split(" … ", 1)
        line = f"{lead} … {short} …"
        later.line = line
        for earlier in fields:
            if earlier.label == lead and earlier.page == later.page:
                earlier.line = line

    if not fields:
        raise FormError("This PDF's form fields are all read-only or signatures, so there is nothing to fill.")

    fields.sort(key=lambda f: (f.page, round(f.top / 6), _left_of(widgets, f.name)))
    return fields


def _collect_widgets(reader) -> Dict[str, List[Tuple[int, tuple, Optional[str]]]]:
    """Map each fully qualified field name to the (page, rect, on-state) of its widgets."""
    found: Dict[str, List[Tuple[int, tuple, Optional[str]]]] = {}
    for page_index, page in enumerate(reader.pages):
        for annotation in page.get("/Annots") or []:
            annotation = annotation.get_object()
            if annotation.get("/Subtype") != "/Widget":
                continue
            name = _qualified_name(annotation)
            if not name:
                continue
            rect = tuple(float(v) for v in annotation.get("/Rect", [0, 0, 0, 0]))
            rect = (min(rect[0], rect[2]), min(rect[1], rect[3]), max(rect[0], rect[2]), max(rect[1], rect[3]))
            found.setdefault(name, []).append((page_index, rect, _on_state(annotation)))
    return found


def _on_state(annotation) -> Optional[str]:
    """The appearance state a button widget shows when it is selected (e.g. ``/Yes``)."""
    try:
        normal = annotation["/AP"]["/N"]
        states = [str(key) for key in normal.keys() if str(key) != "/Off"]
        return states[0] if states else None
    except Exception:
        return None


def _all_rects(widgets):
    for entries in widgets.values():
        for page, rect, _state in entries:
            yield page, rect


def _left_of(widgets, name):
    entries = widgets.get(name)
    return entries[0][1][0] if entries else 0.0


def _qualified_name(annotation) -> str:
    parts = []
    node = annotation
    while node is not None:
        if "/T" in node:
            parts.append(str(node["/T"]))
        parent = node.get("/Parent")
        node = parent.get_object() if parent is not None else None
    return ".".join(reversed(parts))


def _kind_of(data, flags):
    field_type = data.get("/FT")
    states = [str(s) for s in (data.get("/_States_") or [])]
    if field_type == "/Btn":
        on_states = [s for s in states if s != "/Off"]
        if flags & _FF_RADIO:
            return RADIO, [s.lstrip("/") for s in on_states], [], ""
        return CHECKBOX, [], [], (on_states[0] if on_states else "/Yes")
    if field_type == "/Ch":
        options, labels = [], []
        for option in data.get("/Opt") or []:
            option = option.get_object() if hasattr(option, "get_object") else option
            if isinstance(option, (list, ArrayObject)) and option:
                # [export value, display text]: the export value is what is stored
                options.append(str(option[0]))
                labels.append(str(option[1] if len(option) > 1 else option[0]))
            else:
                options.append(str(option))
                labels.append(str(option))
        return CHOICE, options, labels, ""
    return TEXT, [], [], ""


def _radio_options(reader, own_widgets, options, lines_by_page, widgets):
    """Each radio button's stored value and the words printed beside it."""
    values, labels = [], []
    for page, rect, state in own_widgets:
        if not state:
            continue
        value = state.lstrip("/")
        if value in values:
            continue
        if page not in lines_by_page:
            lines_by_page[page] = _text_lines(reader.pages[page])
        same_line = [line for line in lines_by_page[page] if rect[1] - 3 <= line["y"] <= rect[3] + 1]
        label = _right_text(same_line, rect[2])
        values.append(value)
        labels.append(label[:40] if label else value)
    if not values:
        return options, list(options)
    # Page words are only useful when they tell the buttons apart
    if len(set(labels)) != len(labels):
        labels = list(values)
    return values, labels


def _existing_value(data, kind) -> str:
    value = data.get("/V")
    if value is None:
        return ""
    value = str(value)
    if kind in (CHECKBOX, RADIO):
        return "" if value in ("/Off", "") else value.lstrip("/")
    return value.strip()


# --------------------------------------------------------------- page labels
def _text_lines(page) -> List[dict]:
    """Text fragments of ``page`` grouped into lines: [{y, fragments: [(x, text, size)]}]."""
    fragments = []

    def visit(text, cm, tm, _font, size):
        if not text or not text.strip():
            return
        x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
        y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
        scale = abs(cm[0]) or 1.0
        for piece in text.splitlines():
            if piece.strip():
                fragments.append((x, y, piece.strip(), (size or 10) * scale))

    try:
        page.extract_text(visitor_text=visit)
    except Exception:
        return []

    lines: List[dict] = []
    for x, y, text, size in sorted(fragments, key=lambda f: (-f[1], f[0])):
        for line in lines:
            if abs(line["y"] - y) <= 2.5:
                line["fragments"].append((x, text, size))
                break
        else:
            lines.append({"y": y, "fragments": [(x, text, size)]})
    for line in lines:
        line["fragments"].sort()
    return lines


def _approx_width(text, size):
    return len(text) * size * 0.5


def _label_for(name, data, kind, rect, lines, page_rects) -> str:
    tooltip = str(data.get("/TU") or "").strip()
    if tooltip:
        return tooltip
    if rect is not None and lines:
        x0, y0, x1, y1 = rect
        height = y1 - y0
        same_line = [line for line in lines if y0 - 3 <= line["y"] <= y1 + 1 or
                     (height > 30 and abs(line["y"] - (y1 - 12)) <= 6)]

        if kind in (CHECKBOX, RADIO):
            right = _right_text(same_line, x1)
            left = _left_text(same_line, x0, page_rects, rect)
            if kind == CHECKBOX and right:
                return right
            if left:
                return left
            if kind == CHECKBOX and right:
                return right
            # A radio button's right-hand text names its own option, not the question
        else:
            left = _left_text(same_line, x0, page_rects, rect)
            if left:
                if is_connective(left):
                    lead = _line_start(same_line)
                    if lead and lead != left:
                        return f"{lead} … {left}"
                return left

        above = _above_text(lines, x0, x1, y1)
        if above:
            return above
    return _clean_name(name)


def _left_text(lines, x0, page_rects, rect) -> str:
    # Text between the nearest field to the left on the same line and this field
    boundary = max([r[2] for r in page_rects
                    if r != rect and r[2] <= x0 + 1 and r[1] <= rect[3] and r[3] >= rect[1]] or [-1e9])
    best = ""
    for line in lines:
        words = [text for x, text, size in line["fragments"]
                 if x >= boundary - 1 and x + _approx_width(text, size) <= x0 + 8 and x0 - x < 320]
        if words:
            best = " ".join(words)
    return _tidy(best)


def _right_text(lines, x1) -> str:
    for line in lines:
        words = []
        for x, text, _size in line["fragments"]:
            if x1 - 1 <= x <= x1 + 40 or (words and x > x1):
                words.append(text)
            if words and len(" ".join(words)) > 60:
                break
        if words:
            return _tidy(words[0] if len(words) > 1 and len(words[0]) > 3 else " ".join(words[:1]))
    return ""


def _above_text(lines, x0, x1, y1) -> str:
    candidates = []
    for line in lines:
        gap = line["y"] - y1
        if 0 <= gap <= 30:
            words = [text for x, text, size in line["fragments"]
                     if x <= x1 and x + _approx_width(text, size) >= x0 - 10]
            if words:
                candidates.append((gap, " ".join(words)))
    if not candidates:
        return ""
    return _tidy(min(candidates)[1])


def _line_start(lines) -> str:
    for line in lines:
        if line["fragments"]:
            return _tidy(line["fragments"][0][1])
    return ""


# Labels that only make sense with the text before them: "from ____ to ____"
CONNECTIVES = {"to", "and", "until", "till", "through", "thru", "or", "&", "-", "–", "—", "/"}


def is_connective(label: str) -> bool:
    return label.strip(" :.").lower() in CONNECTIVES


def _tidy(text: str) -> str:
    text = re.sub(r"[_.]{3,}", " ", text)        # fill-in lines and dot leaders
    text = re.sub(r"\s+", " ", text).strip(" \t:")
    return text[:120]


def _clean_name(name: str) -> str:
    last = name.split(".")[-1]
    last = re.sub(r"\[\d+\]$", "", last)
    words = re.sub(r"([a-z])([A-Z])", r"\1 \2", last.replace("_", " ").replace("-", " ")).strip()
    return words[:1].upper() + words[1:] if words else name


# ============================================================ pseudonymizing
_HEALTH_CARD = re.compile(r"\b\d{10}\b")


def pseudonymize(text: str) -> Tuple[str, Dict[str, str]]:
    """
    Replace identifiers in ``text`` with numbered placeholders such as ``{{PHONE-1}}``.

    The same identifier always gets the same placeholder. Returns the new text and
    a placeholder -> original mapping for :func:`restore`.
    """
    spans = []
    try:
        import scrubadub
        for filth in scrubadub.Scrubber().iter_filth(text):
            spans.append((filth.beg, filth.end, str(filth.type).upper(), filth.text))
    except Exception:
        pass
    for match in _HEALTH_CARD.finditer(text):
        spans.append((match.start(), match.end(), "OHIP", match.group()))

    # Keep the earliest, longest span where detections overlap
    spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
    kept, last_end = [], -1
    for span in spans:
        if span[0] >= last_end:
            kept.append(span)
            last_end = span[1]

    mapping: Dict[str, str] = {}
    by_value: Dict[Tuple[str, str], str] = {}
    counters: Dict[str, int] = {}
    pieces, position = [], 0
    for start, end, kind, original in kept:
        key = (kind, original)
        if key not in by_value:
            counters[kind] = counters.get(kind, 0) + 1
            by_value[key] = f"{{{{{kind}-{counters[kind]}}}}}"
            mapping[by_value[key]] = original
        pieces.append(text[position:start])
        pieces.append(by_value[key])
        position = end
    pieces.append(text[position:])
    return "".join(pieces), mapping


def restore(value: str, mapping: Dict[str, str]) -> str:
    """Put the original identifiers back in place of their placeholders."""
    if not value or not mapping:
        return value
    for placeholder, original in mapping.items():
        value = value.replace(placeholder, original)
        # Small models sometimes drop a brace or the number's dash
        bare = placeholder.strip("{}")
        value = re.sub(r"\{+\s*" + re.escape(bare) + r"\s*\}+", lambda _m: original, value)
    return value


# ================================================================== prompting
def compose_source(title: str = "", note: str = "", context: str = "", transcript: str = "",
                   visit_date: Optional[_dt.date] = None, max_chars: int = 12000, dictation: str = "") -> str:
    """
    The visit information the form is filled from, shortened to ``max_chars``.

    ``dictation`` is what the clinician said while talking through this form; it
    comes first and wins over the rest. The note comes next because it is the most
    condensed; when space runs out the transcript is shortened first, then the context.
    """
    visit_date = visit_date or _dt.date.today()
    head = [f"Date of visit: {visit_date.isoformat()}"]
    if title.strip():
        head.append(f"Session title (often the patient's name): {title.strip()}")
    parts = ["\n".join(head)]
    if dictation.strip():
        parts.append(f"{DICTATION_HEADING}\n{dictation.strip()}")
    if note.strip():
        parts.append(f"Clinician's note:\n{note.strip()}")
    if context.strip():
        parts.append(f"Additional context from the clinician:\n{context.strip()}")
    fixed = "\n\n".join(parts)

    transcript = transcript.strip()
    if transcript:
        room = max_chars - len(fixed) - 40
        if room <= 200:
            transcript = ""
        elif len(transcript) > room:
            transcript = transcript[:room].rsplit(" ", 1)[0] + " [transcript shortened]"
    if transcript:
        return f"{fixed}\n\nVisit transcript:\n{transcript}"
    return fixed[:max_chars]


def build_prompts(fields: Sequence[FormField], source: str, form_title: str = "",
                  per_prompt: int = FIELDS_PER_PROMPT) -> List[Tuple[List[FormField], str]]:
    """Split ``fields`` into evenly sized groups of at most ``per_prompt`` and build one prompt per group."""
    batches = []
    count = len(fields)
    groups = max(1, -(-count // per_prompt))
    size = -(-count // groups) if count else per_prompt
    for start in range(0, count, size):
        group = list(fields[start:start + size])
        lines = [f"{i}. {f.describe()}" for i, f in enumerate(group, 1)]
        example = ", ".join(f'"{i}": ""' for i in range(1, min(len(group), 3) + 1))
        prompt = (
            f"You are helping a clinician fill in a form{f' titled “{form_title}”' if form_title else ''}. "
            "Use only the visit information below.\n\n"
            "Rules:\n"
            "- Use only facts stated in the visit information. Never guess or invent anything.\n"
            "- If the visit information does not give the answer, use an empty string \"\".\n"
            "- For a field with choices, answer with exactly one of the listed choices, or \"\".\n"
            "- For a checkbox, answer \"yes\" or \"no\", or \"\" if it is not clear.\n"
            "- Placeholders like {{PHONE-1}} stand for real details. Copy them exactly where they belong.\n"
            "- Write only what each field asks for, as briefly as on a paper form. "
            "Never copy the whole note into a field.\n"
            "- When a line has several fields (for example a date range \"from … to\"), "
            "give each field only its own part.\n"
            "- What the clinician said for this form, when given, comes first.\n\n"
            f"Visit information:\n<<<\n{source}\n>>>\n\n"
            "Fields:\n" + "\n".join(lines) + "\n\n"
            "Reply with only a JSON object that maps each field number to its value, for example "
            f"{{{example}}}."
        )
        batches.append((group, prompt))
    return batches


def parse_answers(reply: str, count: int) -> Dict[int, str]:
    """
    Read the model's reply into {field number: raw answer}.

    Tolerates code fences, text around the JSON, trailing commas, single quotes
    and lists instead of objects.
    """
    if not reply:
        return {}
    text = reply.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()

    data = None
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        candidate = text[start:end + 1]
        for attempt in (candidate, re.sub(r",\s*([}\]])", r"\1", candidate)):
            try:
                data = json.loads(attempt)
                break
            except ValueError:
                continue
        if data is None:
            try:
                data = json.loads(re.sub(r"(?<![A-Za-z])'|'(?![A-Za-z])", '"', candidate))
            except ValueError:
                data = None
    if data is None:
        start, end = text.find("["), text.rfind("]")
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start:end + 1])
            except ValueError:
                data = None

    answers: Dict[int, str] = {}
    if isinstance(data, dict):
        for key, value in data.items():
            match = re.match(r"\s*(?:field\s*)?(\d+)", str(key), flags=re.IGNORECASE)
            if match:
                answers[int(match.group(1))] = _as_text(value)
    elif isinstance(data, list):
        for index, value in enumerate(data, 1):
            answers[index] = _as_text(value)
    else:
        # Last resort: "1": "value" pairs anywhere in the reply
        for number, value in re.findall(r'"?(\d+)"?\s*:\s*"((?:[^"\\]|\\.)*)"', text):
            answers[int(number)] = value
    return {k: v for k, v in answers.items() if 1 <= k <= count}


def _as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple)):
        return ", ".join(_as_text(v) for v in value if v not in (None, ""))
    if isinstance(value, dict):
        return _as_text(value.get("value", ""))
    return str(value)


def normalize(field_: FormField, answer: str) -> str:
    """
    Turn a raw answer into a value the field accepts.

    Text is trimmed and cut to the field's maximum length; checkboxes become
    ``"yes"`` or ``""``; choices are matched to the closest listed option, or left
    blank when nothing matches well.
    """
    answer = (answer or "").strip().strip('"').strip()
    # A copied heading from the visit information is not an answer
    answer = re.sub(r"^(?:what the clinician said for this form|dictated by the clinician[^:]*|visit information"
                    r"|date of visit|clinician's note)\s*:\s*", "", answer, flags=re.IGNORECASE).strip()
    # Placeholders the model made up (real ones were already put back by restore())
    answer = re.sub(r"\{\{[^{}]*\}\}|\[(?:[A-Z][A-Za-z ]*|insert[^\]]*)\]", "", answer).strip(" ,;:-")
    if answer.lower().rstrip(".") in _EMPTY_ANSWERS:
        return ""
    if field_.kind == CHECKBOX:
        lowered = answer.lower().rstrip(".")
        if lowered in _YES:
            return "yes"
        return ""
    if field_.kind in (RADIO, CHOICE):
        return field_.option_value(match_option(answer, field_.labels()))
    if not field_.multiline:
        answer = " ".join(answer.split())
    if field_.line:
        answer = _own_part(field_, answer)
    if field_.max_length:
        answer = answer[:field_.max_length]
    return answer


def _own_part(field_: FormField, answer: str) -> str:
    """
    For a field sharing a printed line ("Unable to work from … to …"), keep only its
    own part of an answer that covers the whole line, such as
    "Unable to work from 2026-10-13 to 2026-10-20".
    """
    lead, short = field_.line.rstrip(" …").split(" … ", 1)
    text = answer
    if text.lower().startswith(lead.lower()):
        text = text[len(lead):].strip(" :")
    pieces = re.split(rf"\s+{re.escape(short)}\s*:?\s+", text, maxsplit=1, flags=re.IGNORECASE)
    if len(pieces) == 2:
        is_first = " … " not in field_.label
        text = pieces[0] if is_first else pieces[1]
    elif text.lower().startswith(short.lower() + " "):
        text = text[len(short):]
    return text.strip(" :,;")


def capacity(field_: FormField, font_size: float = 10.0) -> Optional[int]:
    """Roughly how many characters fit in the field's box, or None when unknown."""
    width, height = field_.box
    if width <= 0 or height <= 0:
        return None
    per_line = max(1, int(width / (font_size * 0.5)))
    lines = max(1, int(height / (font_size * 1.25))) if field_.multiline else 1
    return per_line * lines


def match_option(answer: str, options: Sequence[str]) -> str:
    """The option ``answer`` refers to, or ``""``."""
    if not answer or not options:
        return ""
    lowered = answer.lower().strip(" .")
    for option in options:
        if option.lower() == lowered:
            return option
    for option in options:  # "yes, light duties" -> "Light duties"
        if len(option) > 2 and option.lower() in lowered:
            return option
    for option in options:
        if len(lowered) > 2 and lowered in option.lower():
            return option
    close = difflib.get_close_matches(lowered, [o.lower() for o in options], n=1, cutoff=0.75)
    if close:
        return next(o for o in options if o.lower() == close[0])
    return ""


# =================================================================== writing
def fill_pdf(source_path: str, target_path: str, fields: Sequence[FormField], values: Dict[str, str]) -> int:
    """
    Write ``values`` ({field name: value}) into a copy of ``source_path``.

    Checkbox values are ``"yes"`` (ticked) or ``""``; radio and choice values are
    one of the field's options. Fields not in ``values`` keep what the PDF had.
    The copy stays an editable form. Returns the number of fields written.
    """
    by_name = {f.name: f for f in fields}
    reader = PdfReader(source_path)
    if reader.is_encrypted:
        reader.decrypt("")
    writer = PdfWriter(clone_from=reader)

    prepared = {}   # name -> value as pypdf expects it
    as_name = set()  # fields whose value is a PDF name (/Yes, /Off) rather than text
    for name, value in values.items():
        field_ = by_name.get(name)
        if field_ is None:
            continue
        if field_.kind == CHECKBOX:
            prepared[name] = field_.on_state if value == "yes" else "/Off"
            as_name.add(name)
        elif field_.kind == RADIO:
            prepared[name] = f"/{value}" if value else "/Off"
            as_name.add(name)
        else:
            prepared[name] = value

    # PDF viewers draw the stored appearance, which only breaks lines at line breaks,
    # so long answers in multi-line boxes are wrapped to the box width here
    multiline = {f.name for f in fields if f.kind == TEXT and f.multiline}
    written = set()
    for page in writer.pages:
        names_here = _names_on_page(page)
        page_values = {}
        for name, value in prepared.items():
            if name in names_here:
                if name in multiline and value:
                    width, size = names_here[name + "\0box"]
                    value = wrap_text(value, width, size)
                    prepared[name] = value
                page_values[names_here[name]] = value
                written.add(name)
        if page_values:
            writer.update_page_form_field_values(page, page_values, auto_regenerate=False)
    _sync_parent_values(writer, prepared, as_name)
    writer.set_need_appearances_writer(True)

    with open(target_path, "wb") as handle:
        writer.write(handle)
    return len(written)


def _names_on_page(page) -> Dict[str, object]:
    """
    Fully qualified name -> the name pypdf matches widgets by, for the widgets on
    ``page``; plus ``name + "\\0box"`` -> (usable text width, font size) of each widget.
    """
    names = {}
    for annotation in page.get("/Annots") or []:
        annotation = annotation.get_object()
        if annotation.get("/Subtype") != "/Widget":
            continue
        qualified = _qualified_name(annotation)
        if qualified:
            names[qualified] = qualified
            rect = [float(v) for v in annotation.get("/Rect", [0, 0, 0, 0])]
            names[qualified + "\0box"] = (abs(rect[2] - rect[0]) - 6, _font_size(annotation))
    return names


def _font_size(annotation) -> float:
    """Font size from a widget's (or its field's) default appearance; 12 when automatic."""
    node = annotation
    while node is not None:
        da = node.get("/DA")
        if da:
            match = re.search(r"([\d.]+)\s+Tf", str(da))
            if match and float(match.group(1)) > 0:
                return float(match.group(1))
            break
        parent = node.get("/Parent")
        node = parent.get_object() if parent is not None else None
    return 12.0


def _text_width(text: str, size: float) -> float:
    try:
        import pymupdf
        return pymupdf.get_text_length(text, fontname="helv", fontsize=size)
    except Exception:
        return len(text) * size * 0.52   # Helvetica's average character width


def wrap_text(text: str, width: float, size: float) -> str:
    """Insert line breaks so no line of ``text`` is wider than ``width`` points at ``size``."""
    if width <= 0:
        return text
    lines = []
    for paragraph in text.split("\n"):
        line = ""
        for word in paragraph.split(" "):
            candidate = f"{line} {word}" if line else word
            if _text_width(candidate, size) <= width or not line:
                line = candidate
            else:
                lines.append(line)
                line = word
            while _text_width(line, size) > width and len(line) > 1:   # a single word wider than the box
                cut = len(line)
                while cut > 1 and _text_width(line[:cut], size) > width:
                    cut -= 1
                lines.append(line[:cut])
                line = line[cut:]
        lines.append(line)
    return "\n".join(lines)


def _sync_parent_values(writer, prepared, as_name):
    """Make sure each field dictionary (not only its widgets) carries the new value."""
    acroform = writer._root_object.get("/AcroForm")
    if acroform is None:
        return
    pending = list(acroform.get_object().get("/Fields") or [])
    while pending:
        node = pending.pop().get_object()
        name = _qualified_name(node)
        if name in prepared and "/T" in node:
            value = prepared[name]
            node[NameObject("/V")] = NameObject(value) if name in as_name else TextStringObject(value)
        pending.extend(node.get("/Kids") or [])


def default_output_name(source_path: str, title: str = "") -> str:
    """Suggested file name for the filled copy: ``Form name - Session title - filled.pdf``."""
    import os
    base = os.path.splitext(os.path.basename(source_path))[0]
    title = " ".join(re.sub(r'[\\/:*?"<>|]+', " ", title).split())
    return f"{base}{' - ' + title if title else ''} - filled.pdf"
