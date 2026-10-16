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

# Field roles: who fills a field instead of the AI
CLINICIAN = "clinician"   # the clinician's own details, from their saved profile
PRIVATE = "private"       # identifiers, signatures, consent and contact details: left for a person

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
    role: str = ""            # CLINICIAN (filled from the clinician's saved details), PRIVATE (left
                              # for a person: IDs, signatures, contact details) or "" (asked of the AI)
    third_party: bool = False # about someone other than the patient or clinician (a witness)
    section: str = ""         # the heading the field sits under ("Section 5 - Medical conditions")

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
        if self.role:
            return False      # never asked of the AI: see :func:`assign_roles`
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
    mupdf = _MuPdfText(path)

    def lines_for(page_index):
        if page_index not in lines_by_page:
            lines = mupdf.lines(page_index)
            lines_by_page[page_index] = lines if lines else _text_lines(reader.pages[page_index])
        return lines_by_page[page_index]

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
        page_rects = [r for p, r in _all_rects(widgets) if p == page]

        kind, options, option_labels, on_state = _kind_of(data, flags)
        widget_states = {state for _p, _r, state in own_widgets if state}
        if kind == CHECKBOX and len(widget_states) > 1:
            # Several tick boxes that are really one question (Male / Female, Y / N / U)
            kind, options = RADIO, sorted({state.lstrip("/") for state in widget_states})
        label = _label_for(name, data, kind, rect, lines_for(page), page_rects)
        if kind == RADIO:
            options, option_labels = _radio_options(own_widgets, options, lines_for)
        elif kind == CHECKBOX:
            # A tick box often stores what it means ("Workplace Issues") while its tooltip
            # is the shared question; say both
            state = (on_state or "").lstrip("/")
            if (_meaningful(state) and state.lower() not in _PLAIN_TICKS
                    and state.lower() not in label.lower()):
                label = f"{label.rstrip(' :')}: {state}"
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

    _fix_grid_options(fields, widgets)
    _assign_sections(fields, lines_for)

    fields.sort(key=lambda f: (f.page, round(f.top / 6), _left_of(widgets, f.name)))
    _number_repeats(fields)
    assign_roles(fields)
    return fields


_ROW_NUMBER = re.compile(r"\s*\((\d{1,2})\)\s*$")

# Section headings as forms print them: "Section 5 - Medical conditions", "Section B: …",
# "8. Treatment Details - Psychological", "Part 2 …"
_HEADING = re.compile(
    r"^(?:section|part)\s+(?:\d{1,2}[a-z]?|[a-h])\b\s*[-–—:.]?\s*\S"
    r"|^\d{1,2}\.\s+[A-Z][A-Za-z]", re.IGNORECASE)


def _assign_sections(fields, lines_for):
    """Give each field the last section heading printed above it (on its page or an earlier one)."""
    headings: Dict[int, List[Tuple[float, str]]] = {}
    for page in sorted({f.page for f in fields}):
        found = []
        for line in lines_for(page) or []:
            text = _tidy(" ".join(t for _x, t, _s in line["fragments"]))
            if not (3 < len(text) <= 160 and _HEADING.match(text)):
                continue
            # "6. Medications (please attach a separate list…)" -> "6. Medications"
            text = re.sub(r"\s*\((?:e\.g\.|please\b|if\b|see\b).*$", "", text, flags=re.IGNORECASE).strip()
            # A numbered question ("3. If yes, please indicate…") is not a heading
            if re.search(r"\b(?:if yes|if no|please|do you|did you|will you|have you|has the)\b|\?$", text,
                         re.IGNORECASE):
                continue
            found.append((line["y"], text[:80]))
        headings[page] = found
    for f in fields:
        y_top = -f.top          # PDF y grows upwards; a heading above the field has a larger y
        current = ""
        for page in sorted(p for p in headings if p <= f.page):
            above = [(y, t) for y, t in headings[page] if page < f.page or y >= y_top - 2]
            if above:
                current = min(above)[1]     # the lowest of them: the nearest above the field
        f.section = current[:80]


def _number_repeats(fields):
    """
    Number fields that repeat the same label, as on forms with one block per
    condition ("Medical condition:" three times), so each copy reads "(1)", "(2)"…
    like the rows of a table. A label that already ends in a row number and
    repeats (a second table with the same columns) is marked as a later copy.
    """
    counts: Dict[str, int] = {}
    for f in fields:
        counts[f.label] = counts.get(f.label, 0) + 1
    seen: Dict[str, int] = {}
    for f in fields:
        if counts[f.label] < 2 or f.line:
            continue
        seen[f.label] = seen.get(f.label, 0) + 1
        k = seen[f.label]
        if _ROW_NUMBER.search(f.label):
            if k > 1:
                f.label = f"{f.label}, table {k}"
        else:
            f.label = f"{f.label.rstrip()} ({k})"


def row_of(field_: "FormField") -> Tuple[str, Optional[int]]:
    """(label without its row number, row number) — e.g. ("Medication Name", 2); (label, None) otherwise."""
    label = field_.label
    table = ""
    match = re.search(r", table (\d+)$", label)
    if match:
        table, label = f" [table {match.group(1)}]", label[:match.start()]
    match = _ROW_NUMBER.search(label)
    if not match:
        return field_.label, None
    return label[:match.start()].strip() + table, int(match.group(1))


# ------------------------------------------------------------------ roles
_PRIVATE_LABEL = re.compile(
    r"social insurance|\bsin\b|social security|\bssn\b|health (?:card|insurance) (?:no|number|#)"
    r"|\b(?:phn|ohip|ramq)\b|\bpolicy (?:no|number|#)|policy or group|group (?:plan |policy )?(?:no\b|number|#)"
    r"|certificate (?:no|number|#)|certificate or|employee (?:identification|id)\b|identification (?:no|number|#)"
    r"|member (?:id|no|number)|contract (?:no|number)|claim (?:no|number|#)|signature|consent|witness"
    r"|https?://|\blink to\b|\bstamp\b|tax id|date signed|signed on", re.IGNORECASE)
_CONTACT_LABEL = re.compile(
    r"address|street|\bcity\b|province|\bstate\b|postal|\bzip\b|tele?phone|\bphone\b|\bcell\b|\bfax\b"
    r"|e-?mail", re.IGNORECASE)
_CLINICIAN_NAME = re.compile(
    r"(?:attending|treating) physician(?!'?s? (?:questionnaire|statement|report))(?! \(\d)"
    r"|physician'?s? (?:full )?name|name of (?:the )?(?:attending |treating )?(?:physician|doctor|clinician|"
    r"practitioner|health (?:care )?professional)(?![\s_]*\d)|doctor'?s name|clinician'?s name",
    re.IGNORECASE)
_CLINICIAN_DETAIL = re.compile(
    r"certified specialty|degree/specialty|^specialty$|specialty \(if|licen[cs]e (?:no|number|#)|"
    r"registration (?:no|number|#)|billing (?:no|number|#)|\bcpso\b|college (?:no|number|#)|"
    r"^(?:last|first|given|family) name(?:\(s\))?$|^(?:full )?name(?: \(print\))?$", re.IGNORECASE)


def assign_roles(fields: Sequence["FormField"]) -> None:
    """
    Mark the text fields the AI must not fill. The clinician's name, specialty,
    licence number and contact details (the block that follows "Attending
    physician" and the like) come from the clinician's saved details; identifiers
    (SIN, policy and certificate numbers), signatures, consent and other contact
    details are left for a person. Small models invent these; the visit rarely
    contains them.
    """
    block = 0          # fields left in the clinician's block
    witness = 0        # fields left in a witness's block
    previous = ""
    for f in fields:
        label = f.label
        bare = _ROW_NUMBER.sub("", label).strip(" :")
        if f.kind != TEXT:
            previous = label
            block = witness = 0
            continue
        declaration = re.match(r"\s*i am (?:a|the)\b", previous, re.IGNORECASE) and \
            re.fullmatch(r"(?:full )?name(?: \(print\))?", bare, re.IGNORECASE)
        person_detail = re.search(r"\bname|tele?phone|\bphone\b|\bdate\b", label, re.IGNORECASE)
        if _CLINICIAN_NAME.search(label) or declaration:
            f.role, block, witness = CLINICIAN, 12, 0
        elif block and _CONTACT_LABEL.search(label):
            f.role = CLINICIAN          # "Address and telephone number (or use a stamp)" too
            block -= 1
        elif block and _PRIVATE_LABEL.search(label):
            f.role = PRIVATE            # "Tax ID", "Date signed" inside the block: keep going
            block -= 1
        elif block and _CLINICIAN_DETAIL.search(bare):
            f.role = CLINICIAN
            block -= 1
        elif witness and person_detail:
            f.role = PRIVATE            # the witness's other names, phone and date
            f.third_party = True
            witness -= 1
        else:
            block = witness = 0
            if _PRIVATE_LABEL.search(label) or _CONTACT_LABEL.search(label):
                f.role = PRIVATE
                if re.search(r"witness", label, re.IGNORECASE):
                    f.third_party = True
                    witness = 6
            elif re.search(r"certified specialty|degree/specialty", label, re.IGNORECASE):
                f.role = CLINICIAN
        previous = label


PROFILE_KEYS = ("name", "specialty", "licence", "street", "city", "province", "postal_code",
                "phone", "fax", "email")


def clinician_values(fields: Sequence["FormField"], profile: Dict[str, str]) -> Dict[str, str]:
    """Values for the clinician's fields from their saved details (``PROFILE_KEYS``)."""
    profile = {k: str((profile or {}).get(k) or "").strip() for k in PROFILE_KEYS}
    names = [f for f in fields if f.role == CLINICIAN]
    has_city = any(re.search(r"\bcity\b", f.label, re.IGNORECASE) for f in names)
    place = ", ".join(p for p in (profile["city"], " ".join(
        p for p in (profile["province"], profile["postal_code"]) if p)) if p)
    full_address = ", ".join(p for p in (profile["street"], place) if p)
    values = {}
    for f in names:
        label = f.label.lower()
        parts = profile["name"].split()
        if "address" in label and re.search(r"tele?phone|phone", label):
            value = "\n".join(p for p in (full_address, profile["phone"] and f"Tel. {profile['phone']}",
                                          profile["fax"] and f"Fax {profile['fax']}") if p)
        elif "address" in label and "e-mail" not in label and "email" not in label:
            # "Address (street, city, province, postal code)" wants all of it
            value = full_address if (re.search(r"city|postal|province", label) or not has_city) \
                else profile["street"]
        elif re.search(r"e-?mail", label):
            value = profile["email"]
        elif re.search(r"\bfax\b", label):
            value = profile["fax"]
        elif re.search(r"tele?phone|\bphone\b|\bcell\b", label):
            value = profile["phone"]
        elif re.search(r"postal|\bzip\b", label):
            value = profile["postal_code"]
        elif re.search(r"\bcity\b", label):
            value = profile["city"]
        elif re.search(r"province|\bstate\b", label):
            value = profile["province"]
        elif re.search(r"address|street", label):
            value = profile["street"] if has_city else full_address
        elif re.search(r"specialty|degree", label):
            value = profile["specialty"]
        elif re.search(r"licen[cs]e|registration|billing|cpso|college", label):
            value = profile["licence"]
        elif re.match(r"\s*(?:last|family) name", label):
            value = parts[-1] if parts else ""
        elif re.match(r"\s*(?:first|given) name", label):
            value = parts[0] if parts else ""
        else:
            value = profile["name"]
        if value:
            values[f.name] = value[:f.max_length] if f.max_length else value
    return values


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


# Option values that say nothing about the option ("0", "Choice1", "On")
_OPAQUE = re.compile(r"^(?:\d+|choice\s*\d+|opt(?:ion)?\s*\d+|(?:button|radio|group|check)\s*\d*|on|off|[a-z])$",
                     re.IGNORECASE)
# One-letter values that are clear when the whole set is one of these
_ABBREVIATIONS = {"y": "Yes", "n": "No", "u": "Unknown", "m": "Male", "f": "Female"}
# Tick-box values that only mean "ticked"
_PLAIN_TICKS = {"yes", "no", "true", "false", "x", "checked", "oui", "si", "ok", "selected"}


def _meaningful(value: str) -> bool:
    value = (value or "").strip()
    return bool(value) and not _OPAQUE.match(value)


def _radio_options(own_widgets, options, lines_for):
    """Each radio button's stored value and what it is called."""
    values, printed = [], []
    for page, rect, state in own_widgets:
        if not state:
            continue
        value = state.lstrip("/")
        if value in values:
            continue
        same_line = [line for line in lines_for(page) if rect[1] - 3 <= line["y"] <= rect[3] + 1]
        values.append(value)
        printed.append((_right_text(same_line, rect[2]) or "")[:40])
    if not values:
        return options, list(options)
    if all(_meaningful(v) for v in values):
        # The PDF's own names ("Yes", "No", "Moderate") beat nearby page words,
        # which often belong to the next column
        return values, list(values)
    if all(v.lower() in _ABBREVIATIONS for v in values) and len({v.lower() for v in values}) == len(values):
        return values, [_ABBREVIATIONS[v.lower()] for v in values]
    # Opaque values ("0", "1"): the words printed beside each button, when they tell them apart
    if all(printed) and len(set(printed)) == len(printed):
        return values, printed
    return values, list(values)


def _fix_grid_options(fields, widgets):
    """
    In a rating grid ("No impact / Mild / Moderate / Severe" for each row), a row's
    first option is sometimes stored under the row's own name ("Decision Making").
    Name such an option after the option in the same column of the other rows.
    """
    columns: Dict[Tuple[int, int], Dict[str, int]] = {}
    positions = {}
    for f in fields:
        if f.kind != RADIO:
            continue
        for page, rect, state in widgets.get(f.name, []):
            if not state:
                continue
            value = state.lstrip("/")
            key = (page, round(rect[0] / 6))
            positions[(f.name, value)] = key
            label = f.option_label(value)
            counts = columns.setdefault(key, {})
            counts[label] = counts.get(label, 0) + 1
    for f in fields:
        if f.kind != RADIO or len(f.option_labels) != len(f.options):
            continue
        own = f.label.strip(" :").lower()
        for i, label in enumerate(f.option_labels):
            if label.strip(" :").lower() != own:
                continue
            counts = dict(columns.get(positions.get((f.name, f.options[i])), {}))
            counts.pop(label, None)
            if counts:
                best = max(counts, key=counts.get)
                if counts[best] >= 2 and best not in f.option_labels:
                    f.option_labels[i] = best


def _existing_value(data, kind) -> str:
    value = data.get("/V")
    if value is None:
        return ""
    value = str(value)
    if kind in (CHECKBOX, RADIO):
        return "" if value in ("/Off", "") else value.lstrip("/")
    return value.strip()


# --------------------------------------------------------------- page labels
class _MuPdfText:
    """
    Page words with their positions, from PyMuPDF. Unlike pypdf's text visitor it
    places words drawn inside embedded drawings (Form XObjects) correctly, which
    government forms such as the CPP disability report use for all their text.
    """

    def __init__(self, path):
        self.doc = None
        try:
            import pymupdf
            try:
                pymupdf.TOOLS.mupdf_display_errors(False)
            except Exception:
                pass
            doc = pymupdf.open(path)
            if doc.needs_pass and not doc.authenticate(""):
                return
            self.doc = doc
        except Exception:
            self.doc = None

    def lines(self, index) -> Optional[List[dict]]:
        if self.doc is None or index >= len(self.doc):
            return None
        try:
            import pymupdf
            page = self.doc[index]
            words = page.get_text("words")
            to_pdf = ~page.transformation_matrix   # PyMuPDF's top-left coordinates -> PDF user space
        except Exception:
            return None
        runs: Dict[tuple, list] = {}
        for x0, y0, x1, y1, text, block, line_no, _word in words:
            box = pymupdf.Rect(x0, y0, x1, y1) * to_pdf
            runs.setdefault((block, line_no), []).append((box, text))
        fragments = []
        for items in runs.values():
            items.sort(key=lambda item: item[0].x0)
            current = None
            for box, text in items:
                height = box.height or 10.0
                if current and box.x0 - current[1] <= height * 0.6 and abs(box.y0 - current[2]) <= height * 0.5:
                    current[3] += " " + text
                    current[1] = box.x1
                else:
                    if current:
                        fragments.append(current)
                    current = [box.x0, box.x1, box.y0, text, height]
            if current:
                fragments.append(current)
        # (x, baseline y, text, font size) as pypdf's visitor reports them
        return _group_lines([(x0, y0 + 0.2 * h, text, h / 1.2) for x0, _x1, y0, text, h in fragments])


def _group_lines(fragments) -> List[dict]:
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

    return _group_lines(fragments)


def _approx_width(text, size):
    return len(text) * size * 0.5


def _label_for(name, data, kind, rect, lines, page_rects) -> str:
    tooltip = str(data.get("/TU") or "").strip()
    if tooltip:
        return tooltip
    return _printed_label(kind, rect, lines, page_rects) or _clean_name(name)


def _printed_label(kind, rect, lines, page_rects) -> str:
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
    return ""


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
                   visit_date: Optional[_dt.date] = None, max_chars: int = 12000, dictation: str = "",
                   patient: str = "") -> str:
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
    if patient.strip():
        head.append(f"Patient (from the chart): {patient.strip()}")
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


def plan_batches(fields: Sequence[FormField], per_prompt: int = FIELDS_PER_PROMPT) -> List[List[FormField]]:
    """
    Split ``fields`` into groups of about ``per_prompt``, never splitting the
    row of a table ("Medication Name (2)", "Dosage (2)", …), so the model sees a
    whole row at once.
    """
    units: List[List[FormField]] = []
    for f in fields:
        row = row_of(f)[1]
        if units and row is not None and row_of(units[-1][-1])[1] == row and len(units[-1]) < 12:
            units[-1].append(f)
        else:
            units.append([f])
    batches: List[List[FormField]] = []
    for unit in units:
        if batches and len(batches[-1]) + len(unit) <= per_prompt:
            batches[-1].extend(unit)
        else:
            batches.append(list(unit))
    return batches


def earlier_answers(group: Sequence[FormField], fields: Sequence[FormField],
                    values: Dict[str, str], limit: int = 14) -> List[Tuple[str, str]]:
    """
    Answers already given to earlier rows of the tables ``group`` belongs to, as
    (label, value), so the model puts a different item in each row.
    """
    tables = {row_of(f)[0] for f in group if row_of(f)[1] is not None}
    if not tables:
        return []
    inside = {f.name for f in group}
    found = []
    for f in fields:
        if f.name in inside or not values.get(f.name):
            continue
        base, row = row_of(f)
        if row is not None and base in tables:
            shown = f.option_label(values[f.name]) if f.kind in (RADIO, CHOICE) else values[f.name]
            found.append((f.label, shown if f.kind != CHECKBOX else "yes"))
    return found[-limit:]


# ============================================================== visit facts
# Before a form is filled the model reads the visit once per topic and lists the
# facts; the fields are then filled from that short list instead of the whole
# conversation. A small model finds "the medications in this visit" far more
# reliably than it fills six unrelated boxes from a long transcript, and there are
# no conversation sentences left to copy into the form.
FACT_TOPICS: Tuple[Tuple[str, str], ...] = (
    ("Diagnoses",
     "the diagnoses (main and secondary), diagnosis codes (ICD), when the symptoms started, what caused the "
     "condition (an injury at work, a car accident, an illness), and earlier episodes of the same problem "
     "with when and by whom they were treated"),
    ("Symptoms and examination",
     "the current symptoms with how often and how severe they are, what the clinician found on examination, "
     "test or questionnaire scores, and any rating the clinician gave for a specific area (no impact, mild, "
     "moderate, severe)"),
    ("Functional limitations and restrictions",
     "what the patient cannot do or must not do (lifting, bending, sitting, standing, walking, stairs, "
     "concentrating, driving…), with the limits and the dates or periods they apply to"),
    ("Work",
     "the patient's job and employer, the last day worked, the first day off work, whether the clinician "
     "advised the patient to stop working and from when, the return-to-work plan (gradual, modified duties, "
     "hours) with the expected dates, or that a return to work was not discussed or is not expected"),
    ("Medications",
     "each medication with its name, dose, how often, the date it was started, dose changes with their dates, "
     "and the response or side effects; one medication per line"),
    ("Treatment, referrals and hospital",
     "treatments other than medication (therapy, counselling, physiotherapy, rehabilitation, oxygen…) with the "
     "provider's name and place, how often, the start date, the last visit and the response; referrals and "
     "specialist appointments with the name, specialty and date; emergency visits, hospital stays and surgery "
     "with the place and dates; one item per line"),
    ("Investigations",
     "tests and investigations done with their dates and results, tests or reports still pending, and reports "
     "being sent with the form"),
    ("Response, prognosis and plan",
     "the overall response to treatment, whether the patient follows the treatment plan, planned changes to "
     "treatment, the prognosis, how long the condition is expected to last and whether it is continuous or "
     "comes and goes"),
    ("Other details",
     "how long the patient has been in the clinician's care, the number of visits, the first visit for this "
     "problem, the last and next visit dates, height and weight, factors that complicate recovery (work, "
     "family, money or legal problems, alcohol or drugs, coping), the patient's supports, driving or "
     "professional licences, and anything else the clinician stated that a form might ask about"),
)
NONE_STATED = "None stated."


def build_fact_prompt(topic: str, what: str, source: str) -> str:
    """Asks the model to list the facts on one topic from the visit information."""
    return (
        f"Read the visit information below and list only {what}.\n\n"
        "Rules:\n"
        "- Only facts stated in the visit information. Never guess or add anything.\n"
        "- One fact per line, each starting with \"- \". Short clinical wording: no sentences from the "
        "conversation and no \"you\" or \"I\".\n"
        "- Write dates as YYYY-MM-DD, or YYYY-MM when only the month is known.\n"
        "- Keep names, doses, numbers and codes exactly as stated. Placeholders like {{PHONE-1}} stand "
        "for real details: copy them as they are.\n"
        "- What the clinician said for this form, when given, comes first.\n"
        f"- If the visit information says nothing about this, reply with exactly: {NONE_STATED}\n\n"
        f"Visit information:\n<<<\n{source}\n>>>\n\n"
        f"{topic}:"
    )


def parse_facts(reply: str, limit: int = 1500) -> str:
    """The model's list as clean "- " lines ("" when it found nothing)."""
    text = re.sub(r"^```\w*|```$", "", (reply or "").strip(), flags=re.MULTILINE)
    lines = []
    for line in text.splitlines():
        line = re.sub(r"\*\*(.+?)\*\*", r"\1", line).strip()
        line = re.sub(r"^\s*(?:[-*•]|\d{1,2}[.)])\s*", "", line).strip()
        if not line or line.endswith(":") and len(line) < 40:
            continue
        if re.fullmatch(r"(?:none|nothing|no information)(?: stated| mentioned| given)?\.?", line, re.IGNORECASE):
            continue
        if line not in lines:
            lines.append(line)
    out = "\n".join(f"- {line}" for line in lines)
    return out[:limit].rsplit("\n", 1)[0] if len(out) > limit else out


def read_visit_facts(source: str, ask, progress=None, cancelled=None) -> str:
    """
    Reads the visit's facts topic by topic and returns the fact sheet the form is
    filled from, or ``source`` itself when no facts could be read (the model
    failed or the visit says nothing).

    :param ask: ``ask(prompt) -> reply`` ("" on failure)
    :param progress: called with a short text before each topic
    :param cancelled: returns True to stop early (``source`` is returned)
    """
    facts = []
    for number, (topic, what) in enumerate(FACT_TOPICS, 1):
        if cancelled is not None and cancelled():
            return source
        if progress is not None:
            progress(f"Reading the visit: {topic.lower()} ({number} of {len(FACT_TOPICS)})…")
        facts.append((topic, parse_facts(ask(build_fact_prompt(topic, what, source)) or "")))
    if not any(text for _topic, text in facts):
        return source
    return fact_sheet(source, facts)


def fact_sheet(source: str, facts: Sequence[Tuple[str, str]]) -> str:
    """
    The visit information the fields are filled from: the opening lines of
    ``source`` (visit date, patient), then each topic's facts. Topics with no
    facts are listed as such, so the model knows to leave those fields empty.
    """
    head = source.split("\n\n", 1)[0].strip()
    parts = [head] if head else []
    for topic, text in facts:
        parts.append(f"{topic}:\n{text.strip() or NONE_STATED}")
    return "\n\n".join(parts)


PROMPT_RULES = (
    "How to answer:\n"
    "1. Use only facts stated in the visit information. If it does not give the answer, use \"\". "
    "An empty answer is always better than a guess.\n"
    "2. Write each answer as a clinician would on a paper form: short clinical wording. Do not copy "
    "sentences from the conversation and do not write \"you\" or \"I\".\n"
    "3. Put each fact only in the field that asks for it. Read each field's label carefully: a "
    "diagnosis field gets only the diagnosis, a date field only the date.\n"
    "4. For a field with choices, answer with exactly one of the listed choices, or \"\". For a "
    "checkbox, answer \"yes\" only when the visit information clearly says it applies; otherwise \"\".\n"
    "5. Write dates as YYYY-MM-DD, or YYYY-MM when only the month is known.\n"
    "6. Fields numbered (1), (2), (3)… are rows of one table or copies of one section: put a different "
    "item in each, in order, and leave the extra ones \"\". Never repeat an item that is already in "
    "an earlier row.\n"
    "7. When a field asks for two things (for example a dose and a date), give both.\n"
    "8. Placeholders like {{PHONE-1}} stand for real details. Copy them exactly where they belong.\n"
    "9. What the clinician said for this form, when given, comes first.\n"
    "10. The part of the form a field is in is shown in [brackets]; use it to understand what the field asks."
)


def build_prompt(group: Sequence[FormField], source: str, form_title: str = "",
                 earlier: Sequence[Tuple[str, str]] = ()) -> str:
    """The prompt that asks the model for the fields in ``group``."""
    lines = [f"{i}. {f'[{f.section}] ' if f.section else ''}{f.describe()}" for i, f in enumerate(group, 1)]
    example = ", ".join(f'"{i}": ""' for i in range(1, min(len(group), 3) + 1))
    done = ""
    if earlier:
        done = ("Already filled in earlier rows (do not repeat these items):\n"
                + "\n".join(f"- {label}: {value}" for label, value in earlier) + "\n\n")
    title = f" titled “{form_title}”" if form_title else ""
    return (
        f"You are filling in a medical form{title} for a clinician, using only the visit "
        "information below.\n\n"
        f"{PROMPT_RULES}\n\n"
        f"Visit information:\n<<<\n{source}\n>>>\n\n"
        f"{done}"
        "Fields:\n" + "\n".join(lines) + "\n\n"
        "Reply with only a JSON object that maps each field number to its answer, for example "
        f"{{{example}}}."
    )


def build_prompts(fields: Sequence[FormField], source: str, form_title: str = "",
                  per_prompt: int = FIELDS_PER_PROMPT) -> List[Tuple[List[FormField], str]]:
    """One prompt per group of fields (see :func:`plan_batches`), without earlier answers."""
    return [(group, build_prompt(group, source, form_title)) for group in plan_batches(fields, per_prompt)]


def drop_repeats(fields: Sequence[FormField], values: Dict[str, str]) -> Dict[str, str]:
    """
    Blank rows that repeat an earlier row. Small models fill every row of a
    table with the same medication; a later row whose first answer matches an
    earlier row's first answer (same column) is cleared as a whole.
    """
    def key(value):
        return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()

    values = dict(values)
    # A row is an unbroken run of numbered fields; it ends where a column repeats
    # ("Medication (2)" after "Medication (1)"), so a repeated section ("Medical
    # condition (2)" and everything under it) is one row
    rows: List[List[FormField]] = []
    current: List[FormField] = []
    starts = set()      # columns that begin a row
    for f in fields:
        base, row = row_of(f)
        if row is None:
            current = []
            continue
        if current and base not in starts and base not in {row_of(g)[0] for g in current}:
            current.append(f)
        else:
            current = [f]
            rows.append(current)
            starts.add(base)
    seen: Dict[str, List[str]] = {}     # every earlier answer, by column
    for row in rows:
        first = next((f for f in row if f.kind == TEXT), None)
        if first is None:
            continue
        value = key(values.get(first.name))
        earlier = seen.get(row_of(first)[0].split(" [table")[0], [])
        # Only an item name tells rows apart; two treatments can share a start date or a dose
        named = bool(re.search(r"[a-z]{3}", value))
        if named and any(value == e or (len(value) > 3 and (value in e or e in value)) for e in earlier):
            for f in row:
                values[f.name] = ""
            continue
        for f in row:
            if f.kind == TEXT and key(values.get(f.name)):
                seen.setdefault(row_of(f)[0].split(" [table")[0], []).append(key(values[f.name]))
    return values


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
    answer = format_dates(answer, date_format(field_.label, field_.max_length))
    if field_.max_length:
        answer = answer[:field_.max_length]
    return answer


_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                       "nov", "dec"), 1)}


def date_format(label: str, max_length: Optional[int] = None) -> Optional[str]:
    """
    The date layout a field asks for, as a ``strftime`` pattern ("%d/%m/%Y"), from
    hints such as "(dd/mm/yyyy)" or "Enter 4 digit year and 2 digit month"; None
    when the label says nothing.
    """
    low = label.lower()
    match = re.search(r"\b(dd|mm)\s*([/\-. ])\s*(mm|dd)\s*[/\-. ]\s*y{2,4}\b", low)
    if match:
        sep = match.group(2)
        return f"%d{sep}%m{sep}%Y" if match.group(1) == "dd" else f"%m{sep}%d{sep}%Y"
    match = re.search(r"\by{4}\s*([/\-. ]?)\s*mm(?:\s*[/\-. ]?\s*dd)?\b", low)
    if match:
        sep = match.group(1) or ("" if max_length in (6, 8) else "-")
        return f"%Y{sep}%m{sep}%d" if "dd" in match.group(0) else f"%Y{sep}%m"
    if re.search(r"4[- ]digit year,? (?:and )?2[- ]digit month(?:,? and 2[- ]digit day)?|year,? month,? (?:and )?day",
                 low):
        sep = "" if max_length in (6, 8) else "-"
        return f"%Y{sep}%m{sep}%d" if re.search(r"day", low) else f"%Y{sep}%m"
    return None


def format_dates(text: str, layout: Optional[str]) -> str:
    """
    Rewrite the dates in ``text`` (as the model writes them: 2026-10-15, 2026-10,
    "October 15, 2026") in ``layout``. Text without such dates is left alone, and
    so is everything when ``layout`` is None.
    """
    if not layout or not text:
        return text
    month_only = "%d" not in layout

    def out(year, month, day):
        try:
            if day is None or month_only:
                if month_only:
                    return _dt.date(year, month, 1).strftime(layout)
                return None   # the field wants a day the answer does not have
            return _dt.date(year, month, day).strftime(layout)
        except ValueError:
            return None

    def iso(match):
        year, month, day = int(match.group(1)), int(match.group(2)), match.group(3)
        return out(year, month, int(day) if day else None) or match.group(0)

    text = re.sub(r"\b(\d{4})-(\d{1,2})(?:-(\d{1,2}))?\b", iso, text)

    # Numeric dates in the wrong order, when the numbers make that certain:
    # "03/14/1984" in a day-first box can only be 14 March
    day_first = layout.startswith("%d")
    month_first = layout.startswith("%m")
    if day_first or month_first:
        def swap(match):
            a, b, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
            if day_first and a <= 12 < b <= 31:
                return out(year, a, b) or match.group(0)
            if month_first and b <= 12 < a <= 31:
                return out(year, b, a) or match.group(0)
            return match.group(0)
        text = re.sub(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})\b", swap, text)

    def words(match):
        month = _MONTHS.get(match.group(1)[:3].lower())
        return (out(int(match.group(3)), month, int(match.group(2))) if month else None) or match.group(0)

    text = re.sub(r"\b([A-Za-z]{3,9})\.? (\d{1,2})(?:st|nd|rd|th)?,? (\d{4})\b", words, text)
    return text


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
