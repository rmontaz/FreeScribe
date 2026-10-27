"""
voice_forms.py

Talking through a form that is open in the EMR (an OSCAR eForm, or any web
form): the browser extension reads the form's boxes from the page, the
clinician talks, and the AI fills the boxes from what was said (and from the
linked visit), the same way FreeScribe fills PDF forms. Nothing is written into
the page until the clinician has reviewed the answers in the extension.

Many eForms are a picture of the paper form with boxes placed on top of it, so
the page has no text saying what a box is for. For those boxes the label is read
from the picture with the built-in text recognition (OCR), as for scanned PDFs.

This software is released under the AGPL-3.0 license.
"""

from __future__ import annotations

import base64
import logging
import math
import re
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from services import form_filler as ff

logger = logging.getLogger("freescribe")

MAX_FIELDS = 400
MAX_PICTURES = 6

# Names a form builder gives a box when nobody named it ("Text12", "input_3", "f5", "cb2")
_GENERIC_NAME = re.compile(r"^(?:text|textbox|input|field|box|txt|cb|chk|check|checkbox|radio|rb|select|"
                           r"textarea|ta|t|f|c|r|q|a|b|x)?[\s_\-.]*\d*$", re.IGNORECASE)
# A tick box's edge read as letters in front of its option ("LINo", "[]Yes", "O Yes")
_BOX_EDGE = re.compile(r"^(?:[LlI|\[\]()]{1,3}|[O0]\s)(?=[A-Z])")
# A box called only "Physician:" or "Doctor" on a web form is for the clinician's own name
_BARE_CLINICIAN = re.compile(r"(?:attending |treating |family |most responsible )?(?:physician|doctor|clinician|"
                             r"practitioner|provider|md|physician name|doctor name)\s*:?", re.IGNORECASE)
_KINDS = {"text": ff.TEXT, "checkbox": ff.CHECKBOX, "radio": ff.RADIO, "choice": ff.CHOICE}


class VoiceFormError(Exception):
    """The form could not be read."""


def name_label(name: str) -> str:
    """A label from a box's name ("patient_dob" -> "Patient dob"); "" for names that say nothing."""
    name = (name or "").strip()
    if not name or _GENERIC_NAME.fullmatch(name):
        return ""
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
    text = re.sub(r"[_\-.\[\]]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not re.search(r"[A-Za-z]{3}", text):
        return ""
    return text[0].upper() + text[1:].lower()


def fields_from_page(items: Sequence[dict]) -> List[ff.FormField]:
    """The boxes the extension read from the page, as form fields (``name`` is the extension's id)."""
    if not items:
        raise VoiceFormError("No boxes to fill were found on this page. Open the form first.")
    fields = []
    for item in list(items)[:MAX_FIELDS]:
        kind = _KINDS.get(str(item.get("kind", "text")).lower())
        if kind is None or not str(item.get("id", "")):
            continue
        options = [str(o) for o in item.get("options") or []]
        option_labels = [str(o) for o in item.get("option_labels") or []]
        if kind in (ff.RADIO, ff.CHOICE) and not options:
            continue
        label = " ".join(str(item.get("label") or "").split())[:200]
        try:
            max_length = int(item.get("max_length") or 0) or None
        except (TypeError, ValueError):
            max_length = None
        fields.append(ff.FormField(
            name=str(item["id"]), kind=kind, label=label or name_label(str(item.get("name") or "")),
            options=options, option_labels=option_labels if len(option_labels) == len(options) else [],
            multiline=bool(item.get("multiline")), max_length=max_length,
            existing=str(item.get("existing") or "")[:500] if kind == ff.TEXT else "",
            top=float(item.get("top") or 0)))
    if not fields:
        raise VoiceFormError("No boxes to fill were found on this page. Open the form first.")
    return fields


def labels_from_pictures(fields: List[ff.FormField], items: Sequence[dict], pictures: Sequence[dict]) -> int:
    """
    Read the labels of unlabelled boxes from the form's picture(s). ``pictures`` are
    ``{"data": base64 image, "width": natural width in pixels}``; each item may carry
    ``picture`` (an index into ``pictures``) and ``rect`` (x0, y0, x1, y1 in that
    picture's pixels; for a radio group, ``option_rects``, one per option).
    Returns the number of labels found.
    """
    from services import flat_form, scan_form
    by_id = {str(i.get("id")): i for i in items}
    wanted: Dict[int, List[ff.FormField]] = {}
    for f in fields:
        item = by_id.get(f.name) or {}
        needs = not f.label or (f.kind in (ff.RADIO,) and not f.option_labels)
        if needs and isinstance(item.get("picture"), int) and 0 <= item["picture"] < min(len(pictures), MAX_PICTURES):
            wanted.setdefault(item["picture"], []).append(f)
    if not wanted or not scan_form.available():
        return 0
    found = 0
    for index, group in wanted.items():
        try:
            data = base64.b64decode(pictures[index].get("data") or "")
            doc = scan_form.image_document([data])
            page = doc[0]
            scanned = scan_form.read_page(page)
            natural = float(pictures[index].get("width") or 0) or None
        except Exception as e:
            logger.warning(f"The form's picture could not be read: {type(e).__name__}")
            continue
        if not natural:
            continue
        scale = page.rect.width / natural
        words = scanned.words
        blanks, owners = [], []
        for f in group:
            item = by_id[f.name]
            if f.kind == ff.RADIO and item.get("option_rects"):
                rects = [tuple(v * scale for v in r) if r else None for r in item["option_rects"]]
                names, question = _radio_words(rects, words)
                if len(names) == len(f.options) and all(names):
                    f.option_labels = names
                if question and not f.label:
                    f.label = question
                    found += 1
            elif item.get("rect"):
                kind = ff.CHECKBOX if f.kind == ff.CHECKBOX else ff.TEXT
                blanks.append(flat_form.Blank(0, tuple(v * scale for v in item["rect"]), kind))
                owners.append(f)
        flat_form.label_blanks(blanks, words)
        for blank, f in zip(blanks, owners):
            label = " ".join((blank.label or "").split()).strip(" :")
            if label and not f.label:
                f.label = label
                found += 1
    return found


def _radio_words(rects, words) -> Tuple[List[str], str]:
    """The option names written beside each radio box, and the question before the first box on the line."""
    names = []
    present = [r for r in rects if r]
    for n, rect in enumerate(rects):
        if not rect:
            names.append("")
            continue
        x0, y0, x1, y1 = rect
        mid = (y0 + y1) / 2
        following = [r[0] for r in present if r[0] > x1 and abs((r[1] + r[3]) / 2 - mid) <= 8]
        limit = min(following + [x1 + 160])
        beside = sorted((w for w in words if abs(w.mid - mid) <= max(6, (y1 - y0)) and w.x1 > x1 + 2
                         and x0 - 2 <= w.x0 < limit - 2), key=lambda w: w.x0)
        parts = []
        for w in beside:
            text = w.text
            if w.x0 < x1 and w.x1 > w.x0:
                # The word starts inside the box: its first letters are the box's edge read as text
                inside = math.ceil(len(text) * (x1 - w.x0) / (w.x1 - w.x0) - 0.25)
                text = _BOX_EDGE.sub("", text[max(0, inside):]) if inside < len(text) else ""
            parts.append(text)
        names.append(" ".join(p for p in parts if p).strip(" :"))
    first = min(present, key=lambda r: r[0]) if present else None
    question = ""
    if first:
        mid = (first[1] + first[3]) / 2
        before = sorted((w for w in words if abs(w.mid - mid) <= 8 and w.x1 <= first[0] + 1 and first[0] - w.x1 < 250),
                        key=lambda w: w.x0)
        question = " ".join(w.text for w in before).strip(" :")
    return names, question


def fill(fields: List[ff.FormField], ask: Callable[[List[ff.FormField]], Tuple[Dict[str, str], List[str]]],
         patient: Optional[dict] = None, profile: Optional[dict] = None) -> Tuple[Dict[str, str], List[str]]:
    """
    Values for ``fields``: the patient's demographics from the linked chart, the
    clinician's details from their saved profile, and the rest from ``ask`` (the AI,
    given the boxes it should answer). Boxes without a label are not asked: the AI
    would only guess.
    """
    from services import patient_details
    ff.assign_roles(fields)
    values: Dict[str, str] = {}
    for f in fields:
        if f.kind == ff.TEXT and not f.existing and _BARE_CLINICIAN.fullmatch(f.label.strip()):
            f.role = ff.CLINICIAN
            if profile and profile.get("name"):
                values[f.name] = profile["name"]
    if patient:
        values.update(patient_details.patient_values(fields, patient))
    if profile:
        values.update({k: v for k, v in ff.clinician_values(fields, profile).items() if k not in values})
    asked = [f for f in fields if f.needs_answer and f.label and f.name not in values]
    problems: List[str] = []
    if asked:
        answers, problems = ask(asked)
        values.update({k: v for k, v in answers.items() if v and k not in values})
    return {k: v for k, v in values.items() if v}, problems
