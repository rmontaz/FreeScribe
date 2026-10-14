"""
eform_mapping.py

The work behind the "Find fields from PDF" button of the bundled OSCAR eForm Mapper.

The mapper works on page images and finds boxes by their pixels. When the form
came from a PDF that was made on a computer (not scanned), the PDF itself says
exactly where every line, box and tick box is, and what is written next to it.
FreeScribe reads that and returns:

* every field: the PDF's own form fields, or the blanks of a flat form
  (underscores, lines, empty boxes, tick boxes, rows of character boxes; see
  :mod:`services.flat_form`), at their exact positions
* a label for each, from the words printed beside it, and a name made from it
* the OSCAR database tag (``oscarDB``) where the label makes it obvious
  (patient name, date of birth, health card ...), always from the mapper's tag list.
  :func:`suggest_tags` can also ask the AI model about the rest; that is not
  switched on yet.

Coordinates: the mapper stores fields in pixels of its page image
(``naturalW`` x ``naturalH``); PDF pages are measured in points. Each mapper page
says which PDF page it was made from, and the two are scaled into each other.

This software is released under the AGPL-3.0 license.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import pymupdf

from services import flat_form
from services.form_filler import parse_answers

# The mapper's default tag list (eForm_Generator.html, DEFAULT_TAGS)
DEFAULT_TAGS = [
    "patient_name", "first_last_name", "patient_nameF", "patient_nameL",
    "sex", "dob", "dobc2", "dob_year", "dob_month", "dob_day",
    "patient_id", "address", "addressline", "city", "province", "postal",
    "phone", "phone2", "cell", "hin", "hinc", "hinversion", "hc_type",
    "current_user", "current_user_fname_lname", "current_user_ohip_no",
    "current_user_specialty_code", "current_user_cpsid", "current_user_id",
    "current_user_email", "current_user_fax", "current_user_signature_image",
    "clinic_name", "clinic_label", "clinic_phone", "clinic_fax", "clinic_email",
    "clinic_addressLine", "clinic_addressLineFull", "clinic_city", "clinic_province", "clinic_postal",
    "patient_dob", "patient_hin", "patient_phone", "patient_address", "patient_sex",
    "provider_name", "today", "today_date", "patient_age", "label", "email",
]

# What each tag fills in, so the model can choose. Tags not listed are shown by name.
TAG_DESCRIPTIONS = {
    "patient_name": "patient's full name",
    "first_last_name": "patient's first and last name",
    "patient_nameF": "patient's first (given) name",
    "patient_nameL": "patient's last (family) name",
    "sex": "patient's sex",
    "dob": "patient's date of birth",
    "dobc2": "patient's date of birth",
    "dob_year": "patient's year of birth",
    "dob_month": "patient's month of birth",
    "dob_day": "patient's day of birth",
    "patient_id": "patient's chart number",
    "address": "patient's street address",
    "addressline": "patient's full address",
    "city": "patient's city",
    "province": "patient's province",
    "postal": "patient's postal code",
    "phone": "patient's home phone",
    "phone2": "patient's work phone",
    "cell": "patient's cell phone",
    "hin": "patient's health card number",
    "hinc": "patient's health card number with version code",
    "hinversion": "patient's health card version code",
    "hc_type": "province that issued the patient's health card",
    "current_user": "name of the doctor completing the form",
    "current_user_fname_lname": "first and last name of the doctor completing the form",
    "current_user_ohip_no": "doctor's OHIP billing number",
    "current_user_specialty_code": "doctor's specialty code",
    "current_user_cpsid": "doctor's CPSO registration number",
    "current_user_id": "doctor's provider number in OSCAR",
    "current_user_email": "doctor's email",
    "current_user_fax": "doctor's fax number",
    "current_user_signature_image": "doctor's signature",
    "clinic_name": "clinic name",
    "clinic_label": "clinic name and address block",
    "clinic_phone": "clinic phone",
    "clinic_fax": "clinic fax",
    "clinic_email": "clinic email",
    "clinic_addressLine": "clinic street address",
    "clinic_addressLineFull": "clinic full address",
    "clinic_city": "clinic city",
    "clinic_province": "clinic province",
    "clinic_postal": "clinic postal code",
    "patient_dob": "patient's date of birth",
    "patient_hin": "patient's health card number",
    "patient_phone": "patient's phone",
    "patient_address": "patient's address",
    "patient_sex": "patient's sex",
    "provider_name": "name of the doctor completing the form",
    "today": "today's date",
    "today_date": "today's date",
    "patient_age": "patient's age",
    "label": "patient's name and address label",
    "email": "patient's email",
}

# Names the mapper gives fields it found itself; these are replaced by names from the label
GENERIC_NAME_RE = re.compile(r"^(gen_inputid\d+|page\d+_\d+|field_\d+|input\d+|checkbox(_\d+)?|text_\d+|"
                             r"textarea_\d+|xbox_\d+|date_\d+|signature_\d+|p\d+_\d+_\w*)$", re.IGNORECASE)

TAGS_PER_PROMPT = 25
REMOVED_TAGS = {"signature", "current_user_signature", "signature_image"}


class MappingError(Exception):
    """A problem to report to the mapper, written for the user."""


@dataclass
class PageInfo:
    """A mapper page and the PDF page its image was made from."""

    mapper_page: int
    pdf_page: int          # 1-based
    natural_w: float
    natural_h: float


# =================================================================== the PDF
def open_pdf(data: bytes):
    """Open PDF bytes, with every page turned upright the way PDF viewers show it."""
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as error:
        raise MappingError(f"The PDF could not be read ({error}).") from error
    if doc.needs_pass:
        raise MappingError("The PDF is password-protected.")
    for page in doc:
        if page.rotation:
            page.remove_rotation()   # in memory only; coordinates then match the page image
    return doc


def parse_pages(raw, page_count: int) -> List[PageInfo]:
    """Read the mapper's page list: [{page, pdfPage, naturalW, naturalH}, ...]."""
    pages = []
    for item in raw or []:
        try:
            mapper_page = int(item.get("page"))
            pdf_page = int(item.get("pdfPage") or mapper_page)
            width, height = float(item.get("naturalW") or 0), float(item.get("naturalH") or 0)
        except (TypeError, ValueError, AttributeError):
            continue
        if width <= 0 or height <= 0 or not 1 <= pdf_page <= page_count:
            continue
        pages.append(PageInfo(mapper_page, pdf_page, width, height))
    if not pages:
        raise MappingError("None of the mapper's pages could be matched to a page of the PDF.")
    return pages


def _scale(page, info: PageInfo) -> Tuple[float, float]:
    return info.natural_w / page.rect.width, info.natural_h / page.rect.height


def _to_mapper(rect, page, info: PageInfo) -> Dict[str, float]:
    sx, sy = _scale(page, info)
    return {"x": round(rect[0] * sx, 2), "y": round(rect[1] * sy, 2),
            "width": round((rect[2] - rect[0]) * sx, 2), "height": round((rect[3] - rect[1]) * sy, 2)}


def has_text(doc) -> bool:
    return any(page.get_text("text").strip() for page in doc)


# ============================================================== find fields
def find_fields(doc, pages: Sequence[PageInfo]) -> List[dict]:
    """
    The fields of the given pages, in mapper coordinates, with labels and a type.

    The PDF's own form fields are used when it has any on a page; otherwise the
    blanks printed on the page are found.
    """
    found = []
    for info in pages:
        page = doc[info.pdf_page - 1]
        blanks = _widget_blanks(page, info.pdf_page - 1)
        if not blanks:
            blanks = flat_form._page_blanks(page, info.pdf_page - 1, keep_unlabelled=True)
        else:
            flat_form.label_blanks(blanks, flat_form.page_words(page))
        flat_form._make_labels_unique(blanks)
        for blank in blanks:
            field = {"page": info.mapper_page, **_to_mapper(blank.rect, page, info),
                     "label": blank.label, "type": _mapper_type(blank)}
            if blank.comb:
                field["comb"] = blank.comb
            found.append(field)
    return found


def _widget_blanks(page, page_index) -> List[flat_form.Blank]:
    """The PDF's own form fields on ``page`` (radio buttons become one tick box each)."""
    blanks = []
    for widget in page.widgets() or []:
        kind = widget.field_type
        if kind in (pymupdf.PDF_WIDGET_TYPE_BUTTON, pymupdf.PDF_WIDGET_TYPE_SIGNATURE):
            continue
        rect = tuple(widget.rect)
        tooltip = (widget.field_label or "").strip()
        if kind in (pymupdf.PDF_WIDGET_TYPE_CHECKBOX, pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON):
            blanks.append(flat_form.Blank(page_index, rect, flat_form.CHECKBOX, label=tooltip))
        else:
            multiline = bool(widget.field_flags & pymupdf.PDF_TX_FIELD_IS_MULTILINE)
            blanks.append(flat_form.Blank(page_index, rect, flat_form.TEXT, label=tooltip,
                                          multiline=multiline or (rect[3] - rect[1]) >= 30))
    return blanks


def _mapper_type(blank: flat_form.Blank) -> str:
    if blank.kind == flat_form.CHECKBOX:
        return "checkbox"
    label = blank.label.lower()
    if re.search(r"\bsignature\b|\bsigned by\b", label):
        return "signature"
    if blank.multiline:
        return "textarea"
    if re.search(r"\bdate\b|\bd\.?o\.?b\b", label):
        return "date"
    return "text"


# ==================================================================== names
def is_generic_name(name: str) -> bool:
    return not (name or "").strip() or bool(GENERIC_NAME_RE.match(name.strip()))


def name_from_label(label: str, field_type: str = "text", taken: Optional[set] = None) -> str:
    """A field name made from its label, e.g. "Patient last name" -> ``patient_last_name``."""
    base = re.sub(r"[^A-Za-z0-9]+", "_", label.lower()).strip("_")[:40].rstrip("_")
    if not base:
        base = field_type or "field"
    if base[0].isdigit():
        base = f"f_{base}"
    name, counter = base, 2
    while taken is not None and name in taken:
        name = f"{base}_{counter}"
        counter += 1
    if taken is not None:
        taken.add(name)
    return name


# ===================================================================== tags
_PROVIDER = re.compile(r"\b(physician|doctor|dr|provider|practitioner|clinician|prescriber|md|"
                       r"referring|referred by|attending|family doctor|signing|completed by)\b")
_CLINIC = re.compile(r"\b(clinic|office|facility|practice)\b")
_OTHER_PERSON = re.compile(r"\b(employer|company|supervisor|manager|insur\w*|adjuster|claim\w*|"
                           r"witness|emergency|contact person|next of kin|spouse|partner|parent|guardian|relative|mother|"
                           r"father|pharmacy|pharmacist|lawyer|agent|school|teacher|caregiver|"
                           r"specialist|consultant|referred to|recipient)\b")


def _in_catalog(tag: str, catalog: Dict[str, str]) -> str:
    """The catalogue's spelling of ``tag``, or ''."""
    return catalog.get(tag.lower(), "") if tag else ""


def _first(catalog: Dict[str, str], *tags: str) -> str:
    for tag in tags:
        found = _in_catalog(tag, catalog)
        if found:
            return found
    return ""


def rule_tag(label: str, field_type: str, catalog: Dict[str, str]) -> str:
    """
    The tag for labels whose meaning is clear, or '' to leave it to the model.

    ``catalog`` maps lower-case tag -> tag. Who the field is about comes first: the
    doctor, the clinic, someone else (no tag), or else the patient.
    """
    if field_type in ("checkbox", "xbox"):
        return ""      # a tag would type its value ("M", a date ...) into the tick box
    text = " " + re.sub(r"[^a-z0-9#]+", " ", (label or "").lower()) + " "
    if not text.strip():
        return ""
    if _OTHER_PERSON.search(text):
        return ""

    if field_type == "signature" or re.search(r"\bsignature\b", text):
        if re.search(r"\bpatient\b|\bclient\b|\bemployee\b|\bworker\b|\bapplicant\b", text):
            return ""
        return _first(catalog, "current_user_signature_image")

    # Numbers only a doctor has
    if re.search(r"\bcpso\b", text):
        return _first(catalog, "current_user_cpsid")
    if re.search(r"\bbilling (number|no|#)", text):
        return _first(catalog, "current_user_ohip_no")

    if _PROVIDER.search(text):
        if re.search(r"\bcpso\b|\bcollege\b.*\bnumber|\bregistration\b", text):
            return _first(catalog, "current_user_cpsid")
        if re.search(r"\bohip\b|\bbilling\b", text):
            return _first(catalog, "current_user_ohip_no")
        if re.search(r"\bspecialty\b", text):
            return _first(catalog, "current_user_specialty_code")
        if re.search(r"\bfax\b", text):
            return _first(catalog, "current_user_fax")
        if re.search(r"\be ?mail\b", text):
            return _first(catalog, "current_user_email")
        if re.search(r"\bname\b", text) or re.fullmatch(
                r" (attending |referring |family |treating )?(physician|doctor|dr|provider|practitioner|clinician|"
                r"prescriber|md)( s)? ", text):
            return _first(catalog, "current_user", "current_user_fname_lname", "provider_name")
        return ""

    if _CLINIC.search(text):
        for word, tags in (("fax", ("clinic_fax",)), ("phone|telephone|tel", ("clinic_phone",)),
                           ("e ?mail", ("clinic_email",)), ("postal", ("clinic_postal",)),
                           ("city", ("clinic_city",)), ("province", ("clinic_province",)),
                           ("address", ("clinic_addressLineFull", "clinic_addressLine")),
                           ("name", ("clinic_name",))):
            if re.search(rf"\b({word})\b", text):
                return _first(catalog, *tags)
        return ""

    # The patient
    if re.search(r"\b(date of birth|birth ?date|dob|d o b|born)\b", text):
        if re.search(r"\byear\b", text):
            return _first(catalog, "dob_year")
        if re.search(r"\bmonth\b", text):
            return _first(catalog, "dob_month")
        if re.search(r"\bday\b", text) and not re.search(r"\bbirth ?day\b", text):
            return _first(catalog, "dob_day")
        return _first(catalog, "dobc2", "dob", "patient_dob")
    if re.search(r"\b(health card|health insurance|hin|ohip|ohip no|ohip number|hcn|phn)\b", text):
        if re.search(r"\bversion\b|\bvc\b", text):
            return _first(catalog, "hinversion")
        if re.search(r"\bprovince\b|\btype\b", text):
            return _first(catalog, "hc_type")
        return _first(catalog, "hin", "patient_hin")
    if re.search(r"\b(first|given) name\b|\bfirst\b \Z", text):
        return _first(catalog, "patient_nameF")
    if re.search(r"\b(last|family|sur) ?name\b|\bsurname\b", text):
        return _first(catalog, "patient_nameL")
    if re.search(r"\bname\b", text):
        if re.search(r"\b(patient|client|employee|worker|applicant|member|resident|student|child|your)\b", text) \
                or re.fullmatch(r" (full )?name ", text):
            return _first(catalog, "patient_name", "first_last_name")
        return ""
    if re.search(r"\b(sex|gender)\b", text):
        return _first(catalog, "sex", "patient_sex")
    if re.fullmatch(r" (patient s )?age ", text) or re.search(r"\bage of patient\b", text):
        return _first(catalog, "patient_age")
    if re.search(r"\b(cell|mobile)\b", text):
        return _first(catalog, "cell")
    if re.search(r"\b(work|business|daytime)\b.*\bphone\b", text):
        return _first(catalog, "phone2")
    if re.search(r"\b(phone|telephone|tel)\b|\bcontact (number|no|#)", text):
        return _first(catalog, "phone", "patient_phone")
    if re.search(r"\be ?mail\b", text):
        return _first(catalog, "email")
    if re.search(r"\bpostal\b", text):
        return _first(catalog, "postal")
    if re.fullmatch(r" (city|town|city town|municipality) ", text):
        return _first(catalog, "city")
    if re.fullmatch(r" (province|prov) ", text):
        return _first(catalog, "province")
    if re.search(r"\baddress\b", text):
        return _first(catalog, "addressline", "address", "patient_address")
    if re.search(r"\b(chart|mrn|medical record)\b", text):
        return _first(catalog, "patient_id")
    if re.fullmatch(r" (date|today s date|todays date|date signed|date completed|date of completion|"
                    r"form date|date of form|signature date|date of signature) ", text):
        return _first(catalog, "today", "today_date")
    return ""


def _catalog_map(catalog: Optional[Iterable[str]]) -> Dict[str, str]:
    tags = [t for t in (catalog or DEFAULT_TAGS) if t and t.lower() not in REMOVED_TAGS]
    return {t.lower(): t for t in tags}


def build_tag_prompt(items: Sequence[dict], catalog: Dict[str, str]) -> str:
    tag_lines = [f"{tag}: {TAG_DESCRIPTIONS.get(tag, tag.replace('_', ' '))}" for tag in catalog.values()]
    field_lines = [f'{i}. "{item["label"]}" ({item.get("type") or "text"})' for i, item in enumerate(items, 1)]
    example = ", ".join(f'"{i}": ""' for i in range(1, min(len(items), 2) + 1))
    return (
        "You are setting up an electronic form in the OSCAR medical records system. Some fields fill in "
        "automatically from the patient's chart, the doctor's profile or the clinic's details. For each "
        "numbered field label below, choose the tag that fills it in automatically, or \"\" if no tag fits. "
        "Most clinical questions, findings and free-text answers have no tag.\n\n"
        "Tags you may use (tag: what it fills in):\n" + "\n".join(tag_lines) + "\n\n"
        "Rules:\n"
        "- Use only tags from the list, spelled exactly.\n"
        "- A field about someone other than the patient, the doctor or the clinic (an employer, insurer, "
        "emergency contact, family member or another doctor) gets \"\".\n"
        "- Dates other than today's date or the patient's date of birth get \"\".\n"
        "- Tick boxes almost always get \"\".\n\n"
        "Fields:\n" + "\n".join(field_lines) + "\n\n"
        f"Reply with only a JSON object mapping each field number to a tag or \"\", for example {{{example}}}."
    )


def _acceptable(tag: str, label: str) -> bool:
    """Reject model answers that contradict who the label is about."""
    text = " " + re.sub(r"[^a-z0-9]+", " ", (label or "").lower()) + " "
    lowered = tag.lower()
    if _OTHER_PERSON.search(text):
        return False
    if _PROVIDER.search(text) and not (lowered.startswith("current_user") or lowered == "provider_name"):
        return False
    if _CLINIC.search(text) and not _PROVIDER.search(text) and not lowered.startswith("clinic_"):
        return False
    if (lowered.startswith("current_user") or lowered == "provider_name") and not _PROVIDER.search(text) \
            and "signature" not in text:
        return False
    if lowered.startswith("clinic_") and not _CLINIC.search(text):
        return False
    return True


def suggest_tags(items: Sequence[dict], catalog: Optional[Iterable[str]] = None,
                 ask_llm: Optional[Callable[[str], str]] = None,
                 progress: Optional[Callable[[int, int], None]] = None) -> Dict[object, Tuple[str, str]]:
    """
    {id: (tag, source)} for items [{id, label, type}], where source is "rule" or "ai".

    Labels the rules settle are not sent to the model. Without ``ask_llm`` only the
    rules are used. Model answers outside the catalogue are dropped.
    """
    catalog_map = _catalog_map(catalog)
    results: Dict[object, Tuple[str, str]] = {}
    pending = []
    for item in items:
        label = (item.get("label") or "").strip()
        if not label:
            continue
        tag = rule_tag(label, item.get("type") or "text", catalog_map)
        if tag:
            results[item["id"]] = (tag, "rule")
        elif item.get("type") not in ("checkbox", "xbox") and not _OTHER_PERSON.search(label.lower()):
            pending.append(item)

    if ask_llm is None or not pending:
        return results
    for start in range(0, len(pending), TAGS_PER_PROMPT):
        group = pending[start:start + TAGS_PER_PROMPT]
        if progress:
            progress(start, len(pending))
        reply = ask_llm(build_tag_prompt(group, catalog_map))
        answers = parse_answers(reply or "", len(group))
        for number, item in enumerate(group, 1):
            tag = _in_catalog(normalize_tag(answers.get(number, "")), catalog_map)
            if tag and _acceptable(tag, item["label"]):
                results[item["id"]] = (tag, "ai")
    return results


def normalize_tag(tag: str) -> str:
    """The mapper's tag normalisation (normalizeTag in eForm_Generator.html)."""
    tag = re.sub(r"\s+", "_", str(tag or "").strip())
    return re.sub(r"[^A-Za-z0-9_]", "_", tag).strip("_")


# =========================================================== the endpoints
def detect(payload: dict, ask_llm: Optional[Callable[[str], str]] = None) -> dict:
    """``/api/detect``: fields found from the PDF for the requested pages, labelled, named and tagged."""
    doc = open_pdf(_pdf_bytes(payload))
    if not has_text(doc):
        raise MappingError("This PDF is a scanned image with no text to read. Use the mapper's "
                           "Auto Detect for scanned forms, then name and tag the fields by hand.")
    pages = parse_pages(payload.get("pages"), doc.page_count)
    for info in pages:
        page = doc[info.pdf_page - 1]
        if abs(page.rect.width / page.rect.height - info.natural_w / info.natural_h) > 0.03:
            raise MappingError(f"Page {info.pdf_page} of this PDF is not the same shape as mapper page "
                               f"{info.mapper_page}. Is this the PDF the page images were made from?")
    fields = find_fields(doc, pages)
    taken = set(payload.get("takenNames") or [])
    for index, field in enumerate(fields):
        field["id"] = index
        field["name"] = name_from_label(field["label"], field["type"], taken)
    tags = suggest_tags(fields, payload.get("tagCatalog"), ask_llm)
    for field in fields:
        tag, source = tags.get(field["id"], ("", ""))
        field["oscarTag"], field["source"] = tag, source
        if tag == "current_user_signature_image":
            field["type"] = "signature"
        del field["id"]
    return {"fields": fields, "summary": _summary(fields)}


def _summary(fields: Sequence[dict]) -> dict:
    return {
        "fields": len(fields),
        "labelled": sum(1 for f in fields if f.get("label")),
        "taggedByRules": sum(1 for f in fields if f.get("source") == "rule"),
        "taggedByAI": sum(1 for f in fields if f.get("source") == "ai"),
    }


def _pdf_bytes(payload: dict) -> bytes:
    import base64
    data = payload.get("pdf")
    if not data:
        raise MappingError("The mapper did not send the source PDF.")
    if isinstance(data, str) and data.startswith("data:"):
        data = data.split(",", 1)[1]
    try:
        return base64.b64decode(data, validate=False)
    except Exception as error:
        raise MappingError("The PDF sent by the mapper could not be decoded.") from error


def describe_catalog(catalog: Optional[Iterable[str]] = None) -> str:
    """The tag list as the model sees it (for the log and tests)."""
    return json.dumps(_catalog_map(catalog))
