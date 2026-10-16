"""
patient_details.py

The patient's details as the EMR holds them (name, date of birth, sex, health
card, address, phones), received from the OSCAR chart through the Chrome
extension, and the code that puts them into a form's demographic boxes.

These boxes are filled from the chart, never by the AI: the chart is right and
the AI would only be guessing from what was said aloud.

This software is released under the AGPL-3.0 license.
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Dict, Optional, Sequence

KEYS = ("first_name", "last_name", "dob", "sex", "hin", "hin_version", "hc_province", "sin",
        "street", "city", "province", "postal_code", "phone", "cell", "work_phone", "email",
        "chart_no", "source")

_MAX = 200


def clean(raw: dict) -> Dict[str, str]:
    """Keep the known keys as short strings; the date of birth as YYYY-MM-DD."""
    if not isinstance(raw, dict):
        return {}
    out = {}
    for key in KEYS:
        value = raw.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            value = str(value)
        if isinstance(value, str) and value.strip():
            out[key] = re.sub(r"\s+", " ", value.strip())[:_MAX]
    if "dob" in out:
        out["dob"] = _iso_date(out["dob"]) or ""
        if not out["dob"]:
            del out["dob"]
    if "sex" in out:
        out["sex"] = {"m": "M", "male": "M", "f": "F", "female": "F"}.get(out["sex"].lower(), out["sex"])
    if "sin" in out:
        digits = re.sub(r"\D", "", out["sin"])
        if len(digits) == 9:
            out["sin"] = digits
        else:
            del out["sin"]      # placeholders like 000000000 or partial numbers
    return out


def _iso_date(text: str) -> Optional[str]:
    match = re.match(r"\s*(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if not match:
        return None
    try:
        return _dt.date(int(match.group(1)), int(match.group(2)), int(match.group(3))).isoformat()
    except ValueError:
        return None


def display_name(patient: Dict[str, str]) -> str:
    """"Raman, Priya" — as OSCAR shows patients."""
    last, first = patient.get("last_name", ""), patient.get("first_name", "")
    return ", ".join(p for p in (last, first) if p)


def full_name(patient: Dict[str, str]) -> str:
    """"Priya Raman"."""
    return " ".join(p for p in (patient.get("first_name", ""), patient.get("last_name", "")) if p)


def age(patient: Dict[str, str], on: Optional[_dt.date] = None) -> Optional[int]:
    dob = _iso_date(patient.get("dob", ""))
    if not dob:
        return None
    born, on = _dt.date.fromisoformat(dob), on or _dt.date.today()
    return on.year - born.year - ((on.month, on.day) < (born.month, born.day))


def summary(patient: Dict[str, str]) -> str:
    """One line for the AI's visit information, so it knows who the patient is."""
    parts = [full_name(patient)]
    if patient.get("dob"):
        parts.append(f"date of birth {patient['dob']}")
    if patient.get("sex") in ("M", "F"):
        parts.append({"M": "male", "F": "female"}[patient["sex"]])
    return ", ".join(p for p in parts if p)


# Boxes about someone other than the patient
_OTHER_PERSON = re.compile(
    r"employer|company|physician|phys\b|phys\d|doctor|\bdr\b|specialist|provider|practitioner|clinician|nurse|"
    r"facility|institution|hospital|hosp\b|clinic|witness|insurer|insurance company|representative|"
    r"spouse|partner|parent|guardian|contact person|emergency|school|program|supervisor|manager|"
    r"medication|drug|therap|pharmac|referr|consult|requestor|attending|treating|signature|"
    r"at birth|maiden|previous|former|beneficiar|agent|broker|advisor|lawyer|counsel", re.IGNORECASE)


def patient_values(fields: Sequence, patient: Dict[str, str], date_layout=None, format_dates=None) -> Dict[str, str]:
    """
    Values for the patient's demographic boxes from ``patient`` ({} when the chart
    did not say). ``fields`` are form_filler.FormField; ``date_layout(label, max_length)``
    and ``format_dates(text, layout)`` convert the date of birth to the box's layout.
    Boxes that belong to the clinician, a witness or anyone else are left alone.
    """
    from services import form_filler as ff
    date_layout = date_layout or ff.date_format
    format_dates = format_dates or ff.format_dates
    patient = clean(patient)
    if not patient:
        return {}
    values: Dict[str, str] = {}
    first, last = patient.get("first_name", ""), patient.get("last_name", "")
    street, city = patient.get("street", ""), patient.get("city", "")
    province, postal = patient.get("province", ""), patient.get("postal_code", "")
    place = ", ".join(p for p in (city, " ".join(p for p in (province, postal) if p)) if p)
    full_address = ", ".join(p for p in (street, place) if p)
    has_city = any(re.search(r"\bcity\b", f.label, re.IGNORECASE) for f in fields)

    for f in fields:
        if f.role == ff.CLINICIAN or getattr(f, "third_party", False) or f.existing:
            continue
        label = f.label
        low = label.lower()
        if _OTHER_PERSON.search(low):
            continue
        value = ""
        if f.kind == ff.TEXT:
            value = _text_value(low, patient, first, last, street, place, full_address, has_city,
                                date_layout(label, f.max_length), format_dates)
        elif f.kind in (ff.RADIO, ff.CHOICE) and re.search(r"\bsex\b|gender", low) and patient.get("sex"):
            want = {"M": "male", "F": "female"}.get(patient["sex"], patient["sex"].lower())
            for option, shown in zip(f.options, f.labels()):
                if shown.strip().lower() in (want, want[0]) or option.strip().lower() in (want, want[0]):
                    value = option
                    break
        if value:
            values[f.name] = value[:f.max_length] if f.max_length else value
    return values


def _text_value(low, patient, first, last, street, place, full_address, has_city, layout, format_dates):
    if re.search(r"date of birth|birth ?date|\bdob\b|born", low):
        dob = patient.get("dob", "")
        if not dob:
            return ""
        return format_dates(dob, layout) if layout else dob
    if re.search(r"middle", low) and not re.search(r"last|first", low):
        return ""
    names = re.search(r"\bname|\bee name", low)
    if names and re.search(r"\b(?:last|family|sur)", low) and re.search(r"\b(?:first|given)", low):
        # "Name (Last, First, Middle Initial)" or "(First, Last)": the order the label gives
        last_at = re.search(r"\b(?:last|family|sur)", low).start()
        first_at = re.search(r"\b(?:first|given)", low).start()
        return ", ".join(p for p in (last, first) if p) if last_at < first_at else \
            " ".join(p for p in (first, last) if p)
    if re.search(r"(?:last|family|sur) ?name", low):
        return last
    if re.search(r"(?:first|given) ?name", low):
        return first
    if names:
        return " ".join(p for p in (first, last) if p)
    if re.search(r"social insurance|\bsin\b|\bnas\b", low):
        return patient.get("sin", "")
    if re.search(r"health (?:card|insurance)|\bhin\b|\bphn\b|ohip|ramq|personal health|medical services plan|"
                 r"health number", low):
        # The version code goes with the number unless the form has its own box for it
        hin, version = patient.get("hin", ""), patient.get("hin_version", "")
        if hin and version and not re.search(r"version", low):
            return f"{hin} {version}"
        return hin
    if re.search(r"version", low) and re.search(r"health|card|hin", low) is None:
        return patient.get("hin_version", "")
    if re.search(r"e-?mail", low):
        return patient.get("email", "")
    if re.search(r"\bcell\b|mobile", low):
        return patient.get("cell", "") or ""
    if re.search(r"work|business|daytime", low) and re.search(r"phone|tel", low):
        return patient.get("work_phone", "")
    if re.search(r"alternate|alternative|other", low) and re.search(r"phone|tel", low):
        return patient.get("cell", "") or patient.get("work_phone", "")
    if re.search(r"tele?phone|\bphone\b|\btel\b", low):
        return patient.get("phone", "") or patient.get("cell", "")
    if re.search(r"address|street", low):
        if re.search(r"city|postal|province|town", low) or not has_city:
            return full_address
        return street
    if re.search(r"postal|\bzip\b", low):
        return patient.get("postal_code", "")
    if re.search(r"\bcity\b|town", low) and "address" not in low:
        return patient.get("city", "")
    if re.search(r"province|\bstate\b", low) and "address" not in low:
        return patient.get("province", "")
    return ""
