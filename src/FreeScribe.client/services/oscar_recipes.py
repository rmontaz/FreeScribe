"""
oscar_recipes.py

Ready-made, tested queries for the questions clinicians usually ask of OSCAR's records, so the
answer doesn't depend on the AI writing SQL (a small on-device model can't do that reliably).

:func:`recognize` reads a question such as "How many active diabetics had an A1C over 8 in the
last year?" or "women 50-74 without a mammogram in 2 years" and builds one counts-only query
from parts: a condition (disease registry + billing codes), a lab or measurement (above/below a
value, or done / not done in a period), a medication, a prevention, age, sex and roster status,
optionally grouped by sex or age group. The names come from the clinic's own OSCAR when it has
been learned (see oscar_query.DISCOVERY), so 'A1C' or 'HBA1C', 'TRIG' or 'TG' are matched.

Questions it doesn't recognise return None and go to the AI as before.

This software is released under the AGPL-3.0 license.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# ------------------------------------------------------------------ what can be asked about
# Conditions: words that name them, ICD-9 prefixes (disease registry and OHIP billing diagnostic codes,
# which are ICD-9 based) and ICD-10 prefixes (registries that use ICD-10).
CONDITIONS: List[Tuple[str, Sequence[str], Sequence[str], Sequence[str], Sequence[str]]] = [
    # (name, words, registry ICD-9 prefixes, registry ICD-10 prefixes, OHIP billing diagnostic codes)
    ("diabetes", ("diabetes", "diabetic", "diabetics", "dm", "t2dm", "dm2", "type 2 diabetes"), ("250",), ("E10", "E11", "E13", "E14"), ("250",)),
    ("hypertension", ("hypertension", "hypertensive", "hypertensives", "high blood pressure", "htn"), ("401", "402", "403", "404", "405"), ("I10", "I11", "I12", "I13", "I15"), ("401", "402", "403", "404", "405")),
    ("high cholesterol", ("dyslipidemia", "hyperlipidemia", "high cholesterol", "hypercholesterolemia"), ("272",), ("E78",), ("272",)),
    ("hypothyroidism", ("hypothyroid", "hypothyroidism"), ("244",), ("E03",), ("244",)),
    ("hyperthyroidism", ("hyperthyroid", "hyperthyroidism"), ("242",), ("E05",), ("242",)),
    ("asthma", ("asthma", "asthmatic", "asthmatics"), ("493",), ("J45",), ("493",)),
    ("COPD", ("copd", "emphysema", "chronic bronchitis", "chronic obstructive"), ("491", "492", "496"), ("J43", "J44"), ("491", "492", "496")),
    ("heart failure", ("heart failure", "chf", "hf"), ("428",), ("I50",), ("428",)),
    ("atrial fibrillation", ("atrial fibrillation", "afib", "a fib", "a-fib", "af"), ("4273",), ("I48",), ("427",)),
    ("coronary artery disease", ("coronary artery disease", "cad", "ischemic heart disease", "ischaemic heart disease", "ihd", "angina"), ("410", "411", "412", "413", "414"), ("I20", "I21", "I22", "I24", "I25"), ("410", "412", "413", "414")),
    ("stroke or TIA", ("stroke", "cva", "tia"), ("430", "431", "433", "434", "435", "436", "438"), ("I63", "I64", "G45", "I69"), ("435", "436", "438")),
    ("chronic kidney disease", ("chronic kidney disease", "ckd", "chronic renal failure", "kidney disease"), ("585", "586"), ("N18",), ("585",)),
    ("depression", ("depression", "depressed", "depressive"), ("311", "2962", "2963"), ("F32", "F33"), ("311",)),
    ("anxiety", ("anxiety", "anxious", "panic disorder", "gad"), ("300",), ("F41",), ("300",)),
    ("bipolar disorder", ("bipolar",), ("2964", "2965", "2966", "2967", "2968"), ("F31",), ("296",)),
    ("ADHD", ("adhd", "attention deficit"), ("314",), ("F90",), ("314",)),
    ("dementia", ("dementia", "alzheimer", "alzheimers", "alzheimer's", "cognitive impairment"), ("290", "294", "331"), ("F00", "F01", "F02", "F03", "G30"), ("290", "331")),
    ("osteoporosis", ("osteoporosis", "osteoporotic"), ("7330",), ("M80", "M81"), ("733",)),
    ("osteoarthritis", ("osteoarthritis",), ("715",), ("M15", "M16", "M17", "M18", "M19"), ("715",)),
    ("obesity", ("obesity", "obese"), ("278",), ("E66",), ("278",)),
    ("gout", ("gout",), ("274",), ("M10",), ("274",)),
    ("GERD", ("gerd", "reflux", "heartburn"), ("53081", "5301"), ("K21",), ("530",)),
    ("migraine", ("migraine", "migraines"), ("346",), ("G43",), ("346",)),
    ("epilepsy", ("epilepsy", "seizure disorder"), ("345",), ("G40",), ("345",)),
    ("Parkinson's disease", ("parkinson", "parkinsons", "parkinson's"), ("332",), ("G20",), ("332",)),
    ("sleep apnea", ("sleep apnea", "sleep apnoea", "osa"), ("32723", "78057"), ("G473",), ()),
    ("enlarged prostate", ("bph", "benign prostatic", "enlarged prostate"), ("600",), ("N40",), ("600",)),
    ("psoriasis", ("psoriasis",), ("696",), ("L40",), ("696",)),
    ("anemia", ("anemia", "anaemia", "anemic"), ("280", "281", "285"), ("D50", "D51", "D53", "D64"), ("280", "281", "285")),
    ("alcohol use disorder", ("alcohol use disorder", "alcoholism", "alcohol dependence"), ("303",), ("F10",), ("303",)),
    ("substance use disorder", ("substance use disorder", "drug dependence", "opioid use disorder", "oud"), ("304",), ("F11", "F12", "F13", "F14", "F15", "F19"), ("304",)),
]

# Labs and measurements: words, the usual OSCAR type names, and how to read the value
@dataclass(frozen=True)
class Measure:
    name: str
    words: Tuple[str, ...]
    types: Tuple[str, ...]
    part: str = ""            # "systolic" / "diastolic" for BP stored as "130/80"
    unit: str = ""


MEASURES: List[Measure] = [
    Measure("A1C", ("a1c", "hba1c", "hemoglobin a1c", "haemoglobin a1c", "glycated hemoglobin", "glycosylated"),
            ("A1C", "HBA1C", "HbA1c", "A1c", "HGBA1C"), unit="%"),
    Measure("LDL", ("ldl",), ("LDL", "LDLC"), unit="mmol/L"),
    Measure("HDL", ("hdl",), ("HDL",), unit="mmol/L"),
    Measure("total cholesterol", ("total cholesterol", "cholesterol", "tchl"), ("TCHL", "CHOL", "TC"), unit="mmol/L"),
    Measure("cholesterol ratio", ("cholesterol ratio", "tc/hdl", "tchd"), ("TCHD",)),
    Measure("triglycerides", ("triglycerides", "triglyceride", "trig", "tg"), ("TG", "TRIG"), unit="mmol/L"),
    Measure("eGFR", ("egfr", "gfr", "kidney function"), ("EGFR", "GFR"), unit="mL/min"),
    Measure("creatinine", ("creatinine", "scr"), ("SCR", "CREA", "CR"), unit="µmol/L"),
    Measure("urine ACR", ("acr", "albumin creatinine", "albumin/creatinine", "microalbumin", "uacr"), ("ACR", "UACR"), unit="mg/mmol"),
    Measure("fasting glucose", ("fasting glucose", "fasting blood sugar", "fbs", "fasting sugar"), ("FBS", "FBG"), unit="mmol/L"),
    Measure("TSH", ("tsh", "thyroid stimulating"), ("TSH",)),
    Measure("vitamin B12", ("b12", "vitamin b12"), ("VB12", "B12")),
    Measure("ferritin", ("ferritin",), ("Fer", "FERR", "FERRITIN")),
    Measure("hemoglobin", ("hemoglobin", "haemoglobin", "hgb", "hb"), ("Hb", "HGB", "HB")),
    Measure("ALT", ("alt",), ("ALT",)),
    Measure("potassium", ("potassium",), ("Kpl", "K")),
    Measure("sodium", ("sodium",), ("Napl", "NA")),
    Measure("INR", ("inr",), ("INR",)),
    Measure("systolic blood pressure", ("systolic", "blood pressure", "bp", "sbp"), ("BP",), part="systolic", unit="mmHg"),
    Measure("diastolic blood pressure", ("diastolic", "dbp"), ("BP",), part="diastolic", unit="mmHg"),
    Measure("BMI", ("bmi", "body mass index"), ("BMI",)),
    Measure("weight", ("weight",), ("WT",), unit="kg"),
    Measure("waist circumference", ("waist",), ("WAIS", "WC")),
]

# Preventions: words and the usual OSCAR prevention names
PREVENTIONS: List[Tuple[str, Sequence[str], Sequence[str]]] = [
    ("flu shot", ("flu shot", "flu vaccine", "influenza", "flu"), ("Flu", "FLU", "Influenza")),
    ("mammogram", ("mammogram", "mammography", "mammo", "breast screening"), ("MAM", "Mammogram")),
    ("Pap test", ("pap", "pap test", "pap smear", "cervical screening"), ("PAP", "HPV", "HPV-CERVIX", "HPV Test")),
    ("colorectal screening (FIT/FOBT or colonoscopy)", ("fobt", "fit test", "colorectal screening",
                                                       "colon cancer screening", "colon screening", "colonoscopy"),
     ("FOBT", "FIT", "COLONOSCOPY", "Colonoscopy", "iFOBT")),
    ("pneumococcal vaccine", ("pneumococcal", "pneumovax", "pneumonia vaccine", "prevnar", "pneu"), ("Pneumovax", "Pneu-C", "Pneu-P-23", "PNEU", "Prevnar")),
    ("shingles vaccine", ("shingles", "shingrix", "zoster"), ("Zostavax", "HZV", "Shingrix", "ZOS")),
    ("COVID vaccine", ("covid", "covid-19", "covid vaccine"), ("COVID", "COVID-19", "COVID19")),
    ("tetanus vaccine", ("tetanus", "tdap", "td"), ("Td", "Tdap", "TdP", "dTap")),
    ("bone density scan", ("bone density", "bmd", "dexa", "dxa"), ("BMD",)),
]

AGE_GROUPS = ("0-17", "18-39", "40-64", "65-79", "80+")


# ------------------------------------------------------------------ reading the question
@dataclass
class Period:
    sql: str            # a condition on the date column, with {col} for it
    text: str           # how it is said in the explanation


@dataclass
class Plan:
    conditions: List[Tuple[str, Sequence[str], Sequence[str], Sequence[str]]] = field(default_factory=list)
    measure: Optional[Measure] = None
    compare: Optional[Tuple[str, float]] = None       # (">", 8.0)
    diastolic: Optional[float] = None                   # blood pressure "over 140/90": the second number
    measure_absent: bool = False                        # "without an A1C"
    drug: str = ""
    prevention: Optional[Tuple[str, Sequence[str], Sequence[str]]] = None
    prevention_absent: bool = False
    period: Optional[Period] = None
    sex: str = ""
    age: Tuple[Optional[int], Optional[int]] = (None, None)
    status: str = "active"                              # "active", "inactive", "all"
    rostered: bool = False
    group: str = ""                                     # "sex", "age", "condition"
    appointments: str = ""                              # "", "all", "no-show", "cancelled"
    seen: str = ""                                      # "present"/"absent": had a visit in the period, or not
    seen_by: bool = False                               # "seen by Dr X": the provider is the one who saw them
    provider: Optional[dict] = None                     # {"last", "first"} | {"number"} | {"mine": True}
    wants_list: bool = False                            # "list the patients", "who are they"

    def empty(self) -> bool:
        return not (self.conditions or self.measure or self.drug or self.prevention or self.appointments or self.seen
                    or self.sex or self.age != (None, None) or self.rostered or self.group or self.provider)


_NUMBER = r"(\d+(?:\.\d+)?)"


def _has(text: str, words: Iterable[str]) -> Optional[re.Match]:
    best = None
    for w in sorted(words, key=len, reverse=True):
        m = re.search(r"(?<![\w-])" + re.escape(w) + r"(?![\w-])", text)
        if m and (best is None or m.start() < best.start()):
            best = m
    return best


def _period(text: str) -> Optional[Period]:
    m = re.search(r"\b(?:in|within|over|during)?\s*the\s+(?:last|past|previous)\s+(\d+)\s+(day|week|month|year)s?\b", text) \
        or re.search(r"\b(?:last|past|previous)\s+(\d+)\s+(day|week|month|year)s?\b", text)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        return Period(f"{{col}} >= DATE_SUB(CURDATE(), INTERVAL {n} {unit.upper()})", f"in the last {n} {unit}{'s' if n != 1 else ''}")
    m = re.search(r"\b(?:in|within|over|during)\s+(\d+)\s+(day|week|month|year)s?\b", text)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        return Period(f"{{col}} >= DATE_SUB(CURDATE(), INTERVAL {n} {unit.upper()})", f"in the last {n} {unit}{'s' if n != 1 else ''}")
    m = re.search(r"\b(?:the\s+)?(?:last|past)\s+(year|month|week)\b", text) or re.search(r"\b(?:in\s+the\s+)?past\s+(year|month|week)\b", text)
    if m:
        unit = m.group(1)
        return Period(f"{{col}} >= DATE_SUB(CURDATE(), INTERVAL 1 {unit.upper()})", f"in the last 12 months" if unit == "year" else f"in the last {unit}")
    if re.search(r"\bthis\s+year\b|\byear to date\b|\bytd\b", text):
        return Period("YEAR({col}) = YEAR(CURDATE())", "this calendar year")
    if re.search(r"\bthis\s+month\b", text):
        return Period("YEAR({col}) = YEAR(CURDATE()) AND MONTH({col}) = MONTH(CURDATE())", "this month")
    m = re.search(r"\b(?:in|during|for)\s+((?:19|20)\d\d)\b", text)
    if m:
        return Period(f"YEAR({{col}}) = {m.group(1)}", f"in {m.group(1)}")
    m = re.search(r"\bsince\s+((?:19|20)\d\d)(?:-(\d\d)-(\d\d))?\b", text)
    if m:
        date = f"{m.group(1)}-{m.group(2) or '01'}-{m.group(3) or '01'}"
        return Period(f"{{col}} >= '{date}'", f"since {date}")
    if re.search(r"\b(?:ever|any time|anytime|all time)\b", text):
        return Period("1 = 1", "at any time")
    return None


def _age(text: str) -> Tuple[Optional[int], Optional[int]]:
    m = re.search(r"\b(?:aged?|ages|between)\s+(\d{1,3})\s*(?:-|–|to|and)\s*(\d{1,3})\b", text) \
        or re.search(r"\b(\d{1,3})\s*(?:-|–|to)\s*(\d{1,3})\s*(?:years?|yrs?|y/?o|year[- ]olds?)\b", text)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"\b(?:over|older than|above|aged over|>)\s*(\d{1,3})\b(?!\s*(?:%|mmol|kg|mmhg|/|\.\d))", text)
    if m and not re.search(r"(a1c|ldl|hdl|bp|bmi|weight|tsh|egfr|gfr|pressure|cholesterol|acr|glucose|creatinine)\D{0,25}$",
                           text[:m.start()]):
        return int(m.group(1)), None              # "over 65" as clinicians mean it: 65 and older
    m = re.search(r"\b(\d{1,3})\s*(?:\+|and (?:over|older|up)|or older|years? and older)", text)
    if m:
        return int(m.group(1)), None
    m = re.search(r"\b(?:under|younger than|below|aged under|<)\s*(\d{1,3})\b(?!\s*(?:%|mmol|kg|mmhg|/|\.\d))", text)
    if m and not re.search(r"(a1c|ldl|hdl|bp|bmi|weight|tsh|egfr|gfr|pressure|cholesterol|acr|glucose|creatinine)\D{0,25}$",
                           text[:m.start()]):
        return None, int(m.group(1)) - 1
    if re.search(r"\b(?:seniors?|elderly|older adults)\b", text):
        return 65, None
    if re.search(r"\b(?:children|kids|pediatric|paediatric|minors)\b", text):
        return None, 17
    if re.search(r"\badults\b", text):
        return 18, None
    return None, None


def _compare(after: str) -> Optional[Tuple[str, float]]:
    """A comparison right after a lab's name: "over 8", "> 3.5", "of 140 or more", "below 60"."""
    after = after[:60]
    rules = [(r"^\D{0,25}?(?:>=|≥|at least|or more|or higher)\s*" + _NUMBER, ">="),
             (r"^\D{0,25}?(?:<=|≤|at most)\s*" + _NUMBER, "<="),
             (r"^\D{0,25}?(?:>|over|above|greater than|more than|higher than|exceeding|exceeds)\s*" + _NUMBER, ">"),
             (r"^\D{0,25}?(?:<|under|below|less than|lower than)\s*" + _NUMBER, "<"),
             (r"^\D{0,10}?" + _NUMBER + r"\s*(?:%|mmol/l|mmhg|kg)?\s*(?:or (?:more|higher|above|greater))", ">="),
             (r"^\D{0,10}?" + _NUMBER + r"\s*(?:%|mmol/l|mmhg|kg)?\s*(?:or (?:less|lower|below))", "<=")]
    for pattern, op in rules:
        m = re.search(pattern, after)
        if m:
            return op, float(m.group(1))
    return None


def _absent_before(text: str, start: int) -> bool:
    """ "without an A1C", "no mammogram", "not had a flu shot", "overdue for", "missing" right before."""
    before = text[max(0, start - 40):start]
    return bool(re.search(r"\b(?:without|no|not|never|missing|overdue(?: for)?|lacking|haven't|hasn't|didn't|"
                          r"did not|have not|has not|due for)\b[\w\s']{0,22}$", before))


_NOT_NAMES = {"the", "a", "an", "my", "our", "your", "their", "his", "her", "office", "note", "notes", "visit", "visits",
              "list", "number", "no", "team", "patients", "patient", "appointment", "appointments", "who", "with", "on",
              "of", "and", "for", "in", "at", "is", "are", "has", "have", "had", "roster", "panel", "practice", "only",
              "just", "assistant", "group", "order", "orders", "said", "says", "visit", "seen", "saw", "by", "to", "from",
              "s", "md", "do", "np", "rn", "same"}


def _provider(text: str) -> Optional[dict]:
    """The provider the question is about: "my patients", "Dr. Montazeri's patients", "provider 101"."""
    if re.search(r"\bmy (?:own )?(?:patients|roster|panel|practice|diabetics|caseload)\b|\bunder me\b|"
                 r"\bassigned to me\b|\b(?:i|me) (?:see|follow|look after)\b|\bseen by me\b", text):
        return {"mine": True}
    m = re.search(r"\bprovider\s*(?:#|no\.?|number|id)?\s*(\d{1,6})\b", text)
    if m:
        return {"number": m.group(1)}
    m = re.search(r"\b(?:dr|doctor|np|provider|physician|nurse practitioner)\.?\s+([a-z][a-z'\-]+)(?:\s+([a-z][a-z'\-]+))?", text)
    if m:
        words = [re.sub(r"'s$|'$", "", w) for w in m.groups() if w]
        words = [w for w in words if w and w not in _NOT_NAMES]
        if len(words) == 2:
            return {"first": words[0], "last": words[1]}
        if words:
            return {"last": words[0]}
    m = re.search(r"\b([a-z][a-z\-]{2,})'s (?:patients|roster|panel|diabetics|practice)\b", text)
    if m and m.group(1) not in _NOT_NAMES:
        return {"last": m.group(1)}
    return None


def recognize(question: str) -> Optional[Plan]:
    """The question as a :class:`Plan`, or None when it isn't one of the usual kinds."""
    text = " " + re.sub(r"\s+", " ", (question or "").lower()).strip() + " "
    text = text.replace("’", "'")
    if not text.strip():
        return None
    plan = Plan()
    plan.provider = _provider(text)
    plan.wants_list = bool(re.search(r"\b(?:list|lists|listing|who are|who is|who's|who has|who have|which patients|"
                                     r"which of|which ones|names?|"
                                     r"show me (?:the |all )?patients|patient list|pull (?:a |the )?(?:list|patients))\b",
                                     text))
    # Questions about something else entirely go to the AI
    if re.search(r"\b(?:referral|consult|tickler|allerg|billing|billed|invoice|revenue|fee|document|note|letter|eform)\w*",
                 text) and not re.search(r"\bappointments?\b", text):
        return None

    counting = re.search(r"\b(?:how many|number of|count of|total)\s+(?:\w+\s+){0,2}(?:appointments|visits|bookings|"
                         r"no[- ]?shows|cancell?ations)\b|\b(?:appointments|visits|no[- ]?shows)\s+(?:were there|did)\b", text)
    if counting:
        plan.appointments = ("no-show" if re.search(r"no[- ]?shows?", text) else
                             "cancelled" if re.search(r"cancel", text) else "all")
        plan.period = _period(text) or Period("{col} >= DATE_SUB(CURDATE(), INTERVAL 1 YEAR)", "in the last 12 months")
        return plan
    seen = re.search(r"\b(?:seen|visited|visit|visits|appointment|appointments|came in)\b", text)
    plan.seen_by = bool(re.search(r"\b(?:seen|visited) by\b|\bvisits? with\b|\bappointments? with\b", text))
    if seen:
        plan.seen = "absent" if _absent_before(text, seen.start()) or re.search(r"\bnot (?:been )?seen\b|"
                                                                                r"\bhaven't been seen\b", text) else "present"

    for name, words, icd9, icd10, ohip in CONDITIONS:
        m = _has(text, words)
        if m and not _absent_before(text, m.start()):
            if name == "hypertension" and m.group(0) == "high blood pressure" and _compare(text[m.end():]):
                continue                       # "blood pressure over 140" is a reading, not the diagnosis
            plan.conditions.append((name, icd9, icd10, ohip))

    for measure in MEASURES:
        m = _has(text, measure.words)
        if not m:
            continue
        if measure.name == "hemoglobin" and _has(text, ("a1c", "hba1c")):
            continue
        if measure.name == "total cholesterol" and _has(text, ("high cholesterol",)) and not _compare(text[m.end():]):
            continue
        if measure.name == "systolic blood pressure" and _has(text, ("high blood pressure",)) and not _compare(text[m.end():]):
            continue
        plan.measure = measure
        plan.compare = _compare(text[m.end():])
        plan.measure_absent = plan.compare is None and _absent_before(text, m.start())
        # A blood pressure "over 140/90": the systolic number
        if measure.part == "systolic":
            bp = re.search(r"(\d{2,3})\s*/\s*(\d{2,3})", text[m.end():m.end() + 40])
            if bp:
                op = plan.compare[0] if plan.compare else (">=" if re.search(r"or (?:more|higher)|at least|>=", text) else ">")
                plan.compare = (op, float(bp.group(1)))
                plan.diastolic = float(bp.group(2))
        break

    m = re.search(r"\b(?:on|taking|prescribed|using|takes|take|with a prescription for|rx for|prescriptions? for)\s+"
                  r"([a-z][a-z0-9\-]{3,})", text)
    if m and m.group(1) not in {"the", "their", "this", "that", "any", "file", "list", "roster", "active", "insulin?"}:
        word = m.group(1)
        if not _has(word, [w for _, ws, _a, _b, _c in CONDITIONS for w in ws]) and word not in {"average", "dialysis"}:
            plan.drug = word
    if not plan.drug:
        m = re.search(r"\b(insulin|metformin|statins?|warfarin|apixaban|rivaroxaban|levothyroxine|semaglutide|ozempic|"
                      r"empagliflozin|jardiance|dapagliflozin|sitagliptin|atorvastatin|rosuvastatin|opioids?|"
                      r"methadone|buprenorphine|suboxone|benzodiazepines?)\b", text)
        if m:
            plan.drug = m.group(1)

    for prevention in PREVENTIONS:
        m = _has(text, prevention[1])
        if m:
            if prevention[0] == "tetanus vaccine" and not _has(text, ("tetanus", "tdap")):
                continue
            plan.prevention = prevention
            plan.prevention_absent = _absent_before(text, m.start())
            break

    plan.period = _period(text)
    if re.search(r"\b(?:women|woman|female|females|ladies)\b", text):
        plan.sex = "F"
    elif re.search(r"\b(?:men|man|male|males)\b", text):
        plan.sex = "M"
    plan.age = _age(text)
    if re.search(r"\binactive\b", text):
        plan.status = "inactive"
    elif re.search(r"\b(?:all|every|including inactive|ever registered)\b.{0,20}\bpatients\b|\bincluding inactive\b", text) \
            and not re.search(r"\bactive\b", text):
        plan.status = "all"
    plan.rostered = bool(re.search(r"\brostered\b|\benrolled\b", text))
    if re.search(r"\bby (?:sex|gender)\b|\bsex breakdown\b|\b(?:split|broken down) by (?:sex|gender)\b", text):
        plan.group = "sex"
    elif re.search(r"\bby age\b|\bage (?:group|band|breakdown)s?\b", text):
        plan.group = "age"
    elif re.search(r"\b(?:each|every|per|by) (?:condition|disease|diagnosis)\b|\bchronic disease (?:counts|breakdown)\b", text):
        plan.group = "condition"
    elif re.search(r"\b(?:each|every|per|by|for each) (?:doctor|provider|physician|md|family doctor)s?\b|"
                   r"\b(?:doctor|provider)[- ]by[- ](?:doctor|provider)\b", text):
        plan.group = "provider"

    if plan.empty() and not re.search(r"\bhow many (?:active )?patients\b|\bpatient count\b|\bnumber of (?:active )?patients\b"
                                      r"|\bpatients? (?:do|does) (?:we|i) have\b", text):
        return None
    # A drug that is really a lab or prevention word: drop it
    if plan.drug and (_has(plan.drug, [w for m in MEASURES for w in m.words]) or
                      _has(plan.drug, [w for p in PREVENTIONS for w in p[1]])):
        plan.drug = ""
    return plan


# ------------------------------------------------------------------ building the query
def _in(values: Iterable[str]) -> str:
    return ", ".join("'" + v.replace("'", "''") + "'" for v in values)


def _types_for(measure: Measure, known: Optional[Sequence[str]]) -> List[str]:
    """The measurement type names to use: those this OSCAR has (when learned) among the usual ones."""
    if known:
        lower = {k.lower(): k for k in known}
        found = [lower[t.lower()] for t in measure.types if t.lower() in lower]
        if found:
            return found
    return list(measure.types)


def _preventions_for(names: Sequence[str], known: Optional[Sequence[str]]) -> List[str]:
    if known:
        lower = {k.lower(): k for k in known}
        found = [lower[n.lower()] for n in names if n.lower() in lower]
        if found:
            return found
    return list(names)


def _condition_ids(icd9: Sequence[str], icd10: Sequence[str], ohip: Sequence[str], billing: bool,
                   billing_columns: Sequence[str], years: int = 3) -> str:
    """Patient ids with the condition: active in the disease registry, or billed with it in the last ``years``."""
    codes = " OR ".join([f"x.dxresearch_code LIKE '{c}%'" for c in icd9] +
                        [f"x.dxresearch_code LIKE '{c}%'" for c in icd10])
    parts = [f"SELECT x.demographic_no AS id FROM dxresearch x WHERE x.status = 'A' AND ({codes})"]
    if billing and ohip:
        dx = sorted(set(ohip))
        cols = " OR ".join(f"b.{col} IN ({_in(dx)})" for col in billing_columns)
        parts.append("SELECT h.demographic_no AS id FROM billing_on_cheader1 h JOIN billing_on_item b ON b.ch1_id = h.id "
                     f"WHERE h.billing_date >= DATE_SUB(CURDATE(), INTERVAL {years} YEAR) AND h.status <> 'D' "
                     f"AND b.status <> 'D' AND ({cols})")
    return " UNION ".join(parts)


def _value(measure: Measure) -> str:
    """The number in a measurement's text value ('8.1', '8.1%', '130/80')."""
    raw = "m.dataField"
    if measure.part == "systolic":
        raw = "SUBSTRING_INDEX(m.dataField, '/', 1)"
    elif measure.part == "diastolic":
        raw = "SUBSTRING_INDEX(m.dataField, '/', -1)"
    return f"TRIM(REPLACE(REPLACE({raw}, '%', ''), ',', '.'))"


def _numeric(measure: Measure) -> Tuple[str, str]:
    """(SQL that is true for a numeric value, the value as a number); A1C as a fraction (0.072) becomes 7.2."""
    text = _value(measure)
    number = f"CAST({text} AS DECIMAL(10,3))"
    if measure.name == "A1C":
        number = f"(CASE WHEN {number} < 1 THEN {number} * 100 ELSE {number} END)"
    return f"{text} REGEXP '^[0-9]+([.][0-9]+)?$'", number


@dataclass
class Recipe:
    sql: str
    explain: str
    plan: Plan
    counts: bool = True        # only totals (a list of patients when False)
    problem: str = ""          # why the question can't be answered this way (sql is then "")


def _plain(name: str) -> str:
    """A name for comparing: lower case, no accents, letters only."""
    import unicodedata
    name = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", name.lower())


def known_providers(profile: Optional[dict]) -> Optional[List[dict]]:
    """The providers learned from this OSCAR: [{"number", "last", "first", "type", "active", "billing"}], or None."""
    results = (profile or {}).get("results", profile or {})
    learned = results.get("providers") or {}
    if learned.get("error") or not learned.get("rows"):
        return None
    billing = {}
    for row in (results.get("provider_billing") or {}).get("rows") or []:
        if len(row) >= 2 and str(row[1]).strip():
            billing[str(row[0])] = re.sub(r"\D", "", str(row[1]))
    providers = []
    for row in learned["rows"]:
        row = [str(c) for c in row] + [""] * 5
        providers.append({"number": row[0], "last": row[1].strip(), "first": row[2].strip(), "type": row[3].strip(),
                          "active": row[4].strip() in ("1", ""), "billing": billing.get(row[0], "")})
    return providers


def _who(p: dict) -> str:
    name = f"{p['first']} {p['last']}".strip() or "(no name)"
    role = "Dr. " if p.get("type", "").lower() in ("doctor", "") else ""
    return f"{role}{name} (provider {p['number']})"


def match_providers(providers: List[dict], last: str = "", first: str = "") -> List[dict]:
    """Providers whose name fits: exact last name (and first name, if given), else a first name alone,
    else a close spelling. Active providers come first; inactive ones only when no active one fits."""
    import difflib
    last, first = _plain(last), _plain(first)

    def pick(found):
        active = [p for p in found if p["active"]]
        return active or found
    if last:
        exact = [p for p in providers if _plain(p["last"]) == last and (not first or _plain(p["first"]).startswith(first))]
        if exact:
            return pick(exact)
        if not first:                                            # "Dr Reza": a first name
            by_first = [p for p in providers if _plain(p["first"]) == last]
            if by_first:
                return pick(by_first)
        close = [p for p in providers
                 if difflib.SequenceMatcher(None, _plain(p["last"]), last).ratio() >= 0.85
                 and (not first or _plain(p["first"]).startswith(first[:1]))]
        if close:
            return pick(close)
        if first:                                                # "Reza Montazeri" given the other way round
            swapped = [p for p in providers if _plain(p["last"]) == first and _plain(p["first"]).startswith(last)]
            if swapped:
                return pick(swapped)
    return []


def _provider_filter(provider: dict, clinician: Optional[dict],
                     providers: Optional[List[dict]] = None) -> Tuple[str, str, str]:
    """(SQL list of provider numbers, how it is described, a problem) for the question's provider. With the
    learned provider list, a name becomes provider numbers here and the description says which ones."""
    def quote(v):
        return "'" + str(v).replace("'", "''") + "'"

    def numbers(found):
        return ", ".join(quote(p["number"]) for p in found)

    def some(found, limit=12):
        names = "; ".join(_who(p) for p in found[:limit])
        return names + (f"; and {len(found) - limit} more" if len(found) > limit else "")

    if provider.get("number"):
        number = provider["number"]
        if providers is not None:
            found = [p for p in providers if p["number"] == number]
            if not found:
                return "", "", f"There is no provider number {number} in this OSCAR."
            return quote(number), _who(found[0]), ""
        return quote(number), f"provider number {number}", ""

    if provider.get("mine"):
        clinician = clinician or {}
        licence = re.sub(r"\D", "", str(clinician.get("licence") or ""))
        words = [w for w in re.findall(r"[A-Za-z][A-Za-z'\-]+", str(clinician.get("name") or ""))
                 if w.lower().rstrip(".") not in {"dr", "md", "do", "np", "rn", "ccfp", "frcpc", "phd", "mph"}]
        if providers is not None:
            found = [p for p in providers if licence and p["billing"] == licence]
            if not found and words:
                found = match_providers(providers, words[-1], words[0] if len(words) > 1 else "")
            if len(found) == 1:
                return quote(found[0]["number"]), f"you, {_who(found[0])}", ""
            if len(found) > 1:
                return "", "", ("Your name in FreeScribe fits more than one provider in OSCAR: " + some(found) +
                                ". Ask with the provider number, for example “provider " + found[0]["number"] +
                                " patients with diabetes”.")
            if words or licence:
                return "", "", ("FreeScribe couldn't find you among this OSCAR's providers (by your name or billing "
                                "number in Your details). Ask with your name or provider number instead.")
        else:
            if licence:
                return (f"SELECT pr.provider_no FROM provider pr WHERE pr.ohip_no = {quote(licence)} "
                        f"OR pr.practitionerNo = {quote(licence)}", f"you (billing/licence number {licence})", "")
            if words:
                return (f"SELECT pr.provider_no FROM provider pr WHERE pr.last_name = {quote(words[-1])}",
                        f"you (a provider named {words[-1]})", "")
        return "", "", ("To ask about “my patients”, add your name in FreeScribe (Fill PDF form > Your details), or "
                        "name the doctor, for example “Dr. Smith's patients”.")

    last, first = provider.get("last", ""), provider.get("first", "")
    asked = (f"{first.title()} " if first else "") + last.title()
    if providers is not None:
        found = match_providers(providers, last, first)
        if not found:
            active = [p for p in providers if p["active"] and p["type"].lower() in ("doctor", "nurse practitioner", "np")]
            return "", "", (f"There is no provider named {asked} in this OSCAR. Providers: " +
                            some(active or [p for p in providers if p["active"]]) + ".")
        if len(found) > 1 and len({(p["last"].lower(), p["first"].lower()) for p in found}) > 1:
            return "", "", (f"More than one provider is called {asked}: " + some(found) +
                            ". Add the first name, or ask with the provider number.")
        return numbers(found), " / ".join(_who(p) for p in found), ""
    where = f"pr.last_name = {quote(last)}" + (f" AND pr.first_name LIKE {quote(first + '%')}" if first else "")
    return (f"SELECT pr.provider_no FROM provider pr WHERE {where}",
            f"Dr. {asked} (a provider with that name; press Learn this OSCAR to match names to provider numbers)", "")


def _profile_lists(profile: Optional[dict]) -> Tuple[Optional[List[str]], Optional[List[str]], bool, List[str]]:
    """From learned results: measurement type names, prevention names, whether billing is there, its dx columns."""
    if not profile:
        return None, None, True, ["dx", "dx1", "dx2"]       # OSCAR's billing_on_item has all three
    results = profile.get("results", profile)

    def first_column(key):
        r = results.get(key) or {}
        if r.get("error") or not r.get("rows"):
            return None
        return [str(row[0]) for row in r["rows"] if row and str(row[0]).strip()]

    columns = results.get("columns") or {}
    billing_cols = ["dx", "dx1", "dx2"]
    has_billing = True
    if not columns.get("error") and columns.get("rows"):
        item = [str(r[1]) for r in columns["rows"] if len(r) > 1 and str(r[0]).lower() == "billing_on_item"]
        has_billing = bool(item)
        billing_cols = [c for c in ("dx", "dx1", "dx2") if c in item] or ["dx"]
    billing = results.get("billing_dx") or {}
    if billing.get("error") and "doesn't exist" in billing.get("error", ""):
        has_billing = False
    return first_column("measurement_types"), first_column("prevention_types"), has_billing, billing_cols


def build(plan: Plan, profile: Optional[dict] = None, counts_only: bool = True,
          clinician: Optional[dict] = None) -> Recipe:
    """One query for ``plan`` (totals, or a list of patients when asked and allowed), with a plain description."""
    types_known, preventions_known, has_billing, billing_cols = _profile_lists(profile)
    joins, where, said = [], [], []
    provider_sql, provider_text = "", ""
    if plan.provider:
        provider_sql, provider_text, problem = _provider_filter(plan.provider, clinician, known_providers(profile))
        if problem:
            return Recipe("", "", plan, problem=problem)
        if not (plan.seen and plan.seen_by):
            where.append(f"d.provider_no IN ({provider_sql})")

    # Who: status, roster, sex, age
    if plan.status == "active":
        where.append("d.patient_status = 'AC'")
        who = "active patients"
    elif plan.status == "inactive":
        where.append("d.patient_status = 'IN'")
        who = "inactive patients"
    else:
        who = "patients (any status)"
    if plan.rostered:
        where.append("d.roster_status = 'RO'")
        who = "rostered " + who
    if plan.sex:
        where.append(f"d.sex = '{plan.sex}'")
        who = ("female " if plan.sex == "F" else "male ") + who
    age = ("TIMESTAMPDIFF(YEAR, STR_TO_DATE(CONCAT(d.year_of_birth, '-', d.month_of_birth, '-', d.date_of_birth), "
           "'%Y-%m-%d'), CURDATE())")
    low, high = plan.age
    if low is not None:
        where.append(f"{age} >= {low}")
    if high is not None:
        where.append(f"{age} <= {high}")
    if low is not None and high is not None:
        who += f" aged {low} to {high}"
    elif low is not None:
        who += f" aged {low} or older"
    elif high is not None:
        who += f" aged {high} or younger"
    if plan.provider and not (plan.seen and plan.seen_by):
        who += f" whose family doctor (most responsible provider in OSCAR) is {provider_text},"

    # What: conditions
    for n, (name, icd9, icd10, ohip) in enumerate(plan.conditions):
        alias = f"c{n}"
        joins.append(f"JOIN ({_condition_ids(icd9, icd10, ohip, has_billing, billing_cols)}) {alias} "
                     f"ON {alias}.id = d.demographic_no")
        billed = has_billing and ohip
        said.append(f"with {name}: active in the disease registry (codes {', '.join(list(icd9) + list(icd10))})"
                    + (f" or billed with diagnostic code {', '.join(ohip)} in the last 3 years" if billed else ""))

    # Labs and measurements
    if plan.measure:
        measure = plan.measure
        types = _types_for(measure, types_known)
        period = plan.period or Period("{col} >= DATE_SUB(CURDATE(), INTERVAL 1 YEAR)", "in the last 12 months")
        when = period.sql.format(col="m.dateObserved")
        type_text = "/".join(types)
        if plan.compare:
            ok, number = _numeric(measure)
            op, value = plan.compare
            value_sql = f"{value:g}"
            # The latest reading in the period decides
            latest = (f"SELECT m.demographicNo AS id, MAX(m.dateObserved) AS seen FROM measurements m WHERE m.type IN ({_in(types)}) "
                      f"AND {when} AND {ok} GROUP BY m.demographicNo")
            joins.append(f"JOIN ({latest}) lt ON lt.id = d.demographic_no")
            joins.append(f"JOIN measurements m ON m.demographicNo = lt.id AND m.dateObserved = lt.seen AND m.type IN ({_in(types)})")
            words = {">": "above", ">=": "at or above", "<": "below", "<=": "at or below"}[op]
            if plan.diastolic is not None and measure.part == "systolic":
                dia = Measure("diastolic", (), measure.types, part="diastolic")
                dia_ok, dia_number = _numeric(dia)
                join = " OR " if op in (">", ">=") else " AND "
                where.append(f"{ok} AND {dia_ok} AND ({number} {op} {value_sql}{join}{dia_number} {op} {plan.diastolic:g})")
                said.append(f"whose latest blood pressure {period.text} ({type_text}) is {words} "
                            f"{value_sql}/{plan.diastolic:g} ({'either number' if join == ' OR ' else 'both numbers'})")
            else:
                where.append(f"{ok} AND {number} {op} {value_sql}")
                said.append(f"whose latest {measure.name} {period.text} ({type_text}) is {words} {value_sql}"
                            f"{(' ' + measure.unit) if measure.unit and measure.unit != '%' else measure.unit}")
        else:
            done = f"SELECT DISTINCT m.demographicNo AS id FROM measurements m WHERE m.type IN ({_in(types)}) AND {when}"
            if plan.measure_absent:
                joins.append(f"LEFT JOIN ({done}) mx ON mx.id = d.demographic_no")
                where.append("mx.id IS NULL")
                said.append(f"with no {measure.name} recorded {period.text} ({type_text})")
            else:
                joins.append(f"JOIN ({done}) mx ON mx.id = d.demographic_no")
                said.append(f"with a {measure.name} recorded {period.text} ({type_text})")

    # Medications
    if plan.drug:
        drug = plan.drug.rstrip("s") if plan.drug.endswith("s") and plan.drug not in ("opioids",) else plan.drug
        classes = {"statin": ("ATORVASTATIN", "ROSUVASTATIN", "SIMVASTATIN", "PRAVASTATIN", "LOVASTATIN", "FLUVASTATIN"),
                   "opioid": ("MORPHINE", "HYDROMORPHONE", "OXYCODONE", "CODEINE", "FENTANYL", "TRAMADOL", "TAPENTADOL",
                              "METHADONE", "BUPRENORPHINE"),
                   "opioids": ("MORPHINE", "HYDROMORPHONE", "OXYCODONE", "CODEINE", "FENTANYL", "TRAMADOL", "TAPENTADOL",
                               "METHADONE", "BUPRENORPHINE"),
                   "benzodiazepine": ("LORAZEPAM", "CLONAZEPAM", "DIAZEPAM", "ALPRAZOLAM", "OXAZEPAM", "TEMAZEPAM",
                                      "BROMAZEPAM", "CHLORDIAZEPOXIDE"),
                   "ozempic": ("SEMAGLUTIDE",), "jardiance": ("EMPAGLIFLOZIN",), "suboxone": ("BUPRENORPHINE",)}
        names = classes.get(drug, (drug.upper(),))
        match = " OR ".join(f"r.GN LIKE '%{n}%' OR r.BN LIKE '%{n}%' OR r.customName LIKE '%{n}%'" for n in names)
        current = ("SELECT DISTINCT r.demographic_no AS id FROM drugs r WHERE r.archived = 0 AND "
                   f"(r.long_term = 1 OR r.end_date >= CURDATE()) AND ({match})")
        joins.append(f"JOIN ({current}) rx ON rx.id = d.demographic_no")
        said.append(f"currently prescribed {drug if drug not in classes else drug + ' (' + ', '.join(n.lower() for n in names) + ')'}"
                    " (long-term or not yet ended, not removed)")

    # Preventions
    if plan.prevention:
        label, _words, usual = plan.prevention
        names = _preventions_for(usual, preventions_known)
        default_years = {"mammogram": 2, "Pap test": 3, "colorectal screening (FIT/FOBT or colonoscopy)": 2,
                         "bone density scan": 5}.get(label, 1)
        period = plan.period or Period(f"{{col}} >= DATE_SUB(CURDATE(), INTERVAL {default_years} YEAR)",
                                       f"in the last {default_years} year{'s' if default_years != 1 else ''}")
        when = period.sql.format(col="p.prevention_date")
        given = (f"SELECT DISTINCT p.demographic_no AS id FROM preventions p WHERE p.prevention_type IN ({_in(names)}) "
                 f"AND p.deleted = '0' AND p.refused = '0' AND {when}")
        if plan.prevention_absent:
            joins.append(f"LEFT JOIN ({given}) pv ON pv.id = d.demographic_no")
            where.append("pv.id IS NULL")
            said.append(f"with no {label} recorded {period.text} ({'/'.join(names)})")
        else:
            joins.append(f"JOIN ({given}) pv ON pv.id = d.demographic_no")
            said.append(f"with a {label} recorded {period.text} ({'/'.join(names)})")

    # Seen (a kept appointment) in the period, or not
    if plan.seen:
        period = plan.period or Period("{col} >= DATE_SUB(CURDATE(), INTERVAL 1 YEAR)", "in the last 12 months")
        when = period.sql.format(col="a.appointment_date")
        visits = (f"SELECT DISTINCT a.demographic_no AS id FROM appointment a WHERE {when} AND a.appointment_date <= CURDATE() "
                  "AND a.status NOT LIKE 'C%' AND a.status NOT LIKE 'N%'")
        by_whom = ""
        if plan.provider and plan.seen_by:
            visits += f" AND a.provider_no IN ({provider_sql})"
            by_whom = f" by {provider_text}"
        if plan.seen == "absent":
            joins.append(f"LEFT JOIN ({visits}) ap ON ap.id = d.demographic_no")
            where.append("ap.id IS NULL")
            said.append(f"not seen{by_whom} (no kept appointment) {period.text}")
        else:
            joins.append(f"JOIN ({visits}) ap ON ap.id = d.demographic_no")
            said.append(f"seen{by_whom} (a kept appointment) {period.text}")

    # A list of the patients (when asked for and Counts only is off)
    if plan.wants_list and not counts_only and plan.group != "condition":
        columns = [f"d.demographic_no", "d.last_name", "d.first_name", "d.sex", f"{age} AS age"]
        if plan.measure and plan.compare:
            columns += [f"m.dataField AS latest_{re.sub(r'[^a-z0-9]+', '_', plan.measure.name.lower()).strip('_')}",
                        "DATE(m.dateObserved) AS measured_on"]
        sql = f"SELECT DISTINCT {', '.join(columns)} FROM demographic d " + " ".join(joins)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY d.last_name, d.first_name"
        explain = (f"Lists {who}" + ((" " + "; ".join(said)) if said else "") + ", by last name.").replace(",,", ",")
        return Recipe(sql, explain.replace(" with with ", " with "), plan, counts=False)

    # The count, or counts by group
    if plan.group == "provider":
        select = "d.provider_no AS provider_no, COUNT(DISTINCT d.demographic_no) AS patients"
        tail, by = " GROUP BY d.provider_no ORDER BY patients DESC", ", for each family doctor (provider number)"
    elif plan.group == "sex":
        select, tail = "d.sex AS sex, COUNT(DISTINCT d.demographic_no) AS patients", " GROUP BY d.sex ORDER BY d.sex"
        by = ", by sex"
    elif plan.group == "age":
        band = (f"CASE WHEN {age} < 18 THEN '0-17' WHEN {age} < 40 THEN '18-39' WHEN {age} < 65 THEN '40-64' "
                f"WHEN {age} < 80 THEN '65-79' ELSE '80+' END")
        select, tail = f"{band} AS age_group, COUNT(DISTINCT d.demographic_no) AS patients", " GROUP BY age_group ORDER BY age_group"
        by = ", by age group"
    else:
        select, tail, by = "COUNT(DISTINCT d.demographic_no) AS patients", "", ""

    if plan.group == "condition" and not plan.conditions:
        # One count per common chronic condition (each its own query part)
        parts = []
        for name, _w, icd9, icd10, ohip in CONDITIONS[:14]:
            parts.append(f"SELECT '{name}' AS condition_name, COUNT(DISTINCT d.demographic_no) AS patients FROM demographic d "
                         f"JOIN ({_condition_ids(icd9, icd10, ohip, has_billing, billing_cols)}) c ON c.id = d.demographic_no "
                         + ("WHERE " + " AND ".join(where) if where else ""))
        sql = " UNION ALL ".join(parts) + " ORDER BY patients DESC"
        return Recipe(sql, f"Counts {who} with each of 14 common chronic conditions, from the disease registry"
                           + (" or billing in the last 3 years" if has_billing else "") + ".", plan)

    sql = f"SELECT {select} FROM demographic d " + " ".join(joins)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += tail
    explain = (f"Counts {who}" + ((" " + "; ".join(said)) if said else "") + by + ".").replace(",.", ".")
    explain = explain.replace(" with with ", " with ")
    if plan.wants_list and counts_only:
        explain += " (Counts only is on, so this is the number: switch it off for the list of patients.)"
    return Recipe(sql, explain, plan)


def build_appointments(plan: Plan, clinician: Optional[dict] = None, profile: Optional[dict] = None) -> Recipe:
    period = plan.period
    by_whom, provider_where = "", ""
    if plan.provider:
        provider_sql, provider_text, problem = _provider_filter(plan.provider, clinician, known_providers(profile))
        if problem:
            return Recipe("", "", plan, problem=problem)
        provider_where = f" AND a.provider_no IN ({provider_sql})"
        by_whom = f" with {provider_text}"
    when = period.sql.format(col="a.appointment_date")
    if plan.appointments == "no-show":
        status, what = "a.status LIKE 'N%'", "no-show appointments"
    elif plan.appointments == "cancelled":
        status, what = "a.status LIKE 'C%'", "cancelled appointments"
    else:
        status, what = "a.status NOT LIKE 'C%' AND a.status NOT LIKE 'N%'", "appointments (not cancelled or no-shows)"
    sql = (f"SELECT COUNT(*) AS appointments, COUNT(DISTINCT a.demographic_no) AS patients FROM appointment a "
           f"WHERE {when} AND {status} AND a.demographic_no > 0{provider_where}")
    return Recipe(sql, f"Counts {what}{by_whom} {period.text}, and the patients they were for.", plan)


def recipe_for(question: str, profile: Optional[dict] = None, counts_only: bool = True,
               clinician: Optional[dict] = None) -> Optional[Recipe]:
    """A ready-made query for ``question``, or None when the AI should write one. ``clinician`` (the
    clinician's own details: name, licence) answers "my patients"."""
    plan = recognize(question)
    if plan is None:
        return None
    providers = known_providers(profile)
    if plan.provider is None and providers:
        plan.provider = _named_provider(question, providers)
    # A doctor or provider is mentioned but not understood: ask, never answer for the whole clinic
    if plan.provider is None and re.search(r"\b(?:dr|doctor|provider|physician|np|nurse practitioner|resident)\b\.?",
                                           (question or "").lower()) and not re.search(
            r"\b(?:any|every|each|all|which|per|by|for each) (?:dr|doctor|provider|physician|md|family doctor)s?\b|\bfamily doctor\b|"
            r"\bwithout a (?:family )?doctor\b", (question or "").lower()):
        listed = ""
        if providers:
            active = [p for p in providers if p["active"] and p["type"].lower() in ("doctor", "nurse practitioner", "np")]
            listed = " Providers: " + "; ".join(_who(p) for p in active[:12]) + ("; …" if len(active) > 12 else "") + "."
        return Recipe("", "", plan, problem="Which provider do you mean? Write the name as in OSCAR, for example "
                                            "“Dr. Smith's patients with diabetes”, or the provider number." + listed)
    if plan.appointments:
        return build_appointments(plan, clinician, profile)
    return build(plan, profile, counts_only, clinician)


def _named_provider(question: str, providers: List[dict]) -> Optional[dict]:
    """A provider's surname written in the question (capitalized, as names are), without "Dr.": "Montazeri
    patients with diabetes", "patients of Reza Montazeri"."""
    known = {_plain(p["last"]): p for p in providers if len(_plain(p["last"])) >= 3}
    words = re.findall(r"\b[A-Z][A-Za-z'\-]+", question or "")
    for n, word in enumerate(words):
        name = _plain(re.sub(r"'s$", "", word))
        if name in known and n + 1 >= 1:
            first = _plain(words[n - 1]) if n > 0 else ""
            if first and _plain(known[name]["first"]).startswith(first):
                return {"last": name, "first": first}
            return {"last": name}
    return None


def answer(columns: Sequence[str], rows: Sequence[Sequence], explain: str) -> str:
    """The answer to a ready-made query, written without the AI."""
    def number(v):
        try:
            return f"{int(float(v)):,}"
        except (TypeError, ValueError):
            return str(v)
    if not rows:
        return "No results. " + explain
    if len(rows) == 1 and len(rows[0]) == 1:
        return f"{number(rows[0][0])} patients. {explain}"
    if "last_name" in list(columns):
        return f"{len(rows):,} patient{'s' if len(rows) != 1 else ''} listed: see the table (View all rows to sort, copy or save). {explain}"
    if len(rows) == 1 and list(columns)[:2] == ["appointments", "patients"]:
        return f"{number(rows[0][0])} appointments, for {number(rows[0][1])} patients. {explain}"
    parts = [f"{' '.join(str(c) for c in row[:-1]) or '(blank)'}: {number(row[-1])}" for row in rows[:20]]
    return "; ".join(parts) + (f"; and {len(rows) - 20} more" if len(rows) > 20 else "") + f". {explain}"


def name_providers(columns: Sequence[str], rows: Sequence[Sequence], providers: Optional[List[dict]]):
    """Results counted per provider number get the provider's name beside the number (from the learned list)."""
    columns = list(columns)
    if not columns or columns[0] != "provider_no":
        return columns, [list(r) for r in rows]
    names = {p["number"]: _who(p).rsplit(" (provider", 1)[0] for p in (providers or [])}
    out = []
    for row in rows:
        row = list(row)
        number = str(row[0]).strip()
        out.append([number, names.get(number, "(no family doctor)" if number in ("", "0", "None") else "(unknown)")] + row[1:])
    return [columns[0], "provider"] + columns[1:], out
