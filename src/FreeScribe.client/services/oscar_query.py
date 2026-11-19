"""
oscar_query.py

Asking OSCAR's database from the chat. The AI writes one MySQL SELECT for the
clinician's question; the clinician sees it and presses Run; the browser extension
runs it with the clinician's own OSCAR login through OSCAR's built-in
"Query by Example" page (Admin > Reports), and the rows come back to the chat,
where the AI answers the question from them.

FreeScribe never connects to the database itself. A query is refused unless it
is a single read-only SELECT (:func:`check_sql`), and a row limit is always
added. OSCAR's page checks again on its side.

This software is released under the AGPL-3.0 license.
"""

from __future__ import annotations

import re
import threading
import uuid
from typing import Dict, List, Optional, Tuple

MAX_ROWS = 1000           # rows a query may return (a LIMIT is added when there is none)
SHOWN_ROWS = 50           # rows shown in the chat and given to the AI for its answer

# Words that change data, read files, or tie up the database. Checked outside quoted text.
_FORBIDDEN = re.compile(
    r"\b(replace(?!\s*\()|insert|update|delete|drop|alter|create|truncate|rename|grant|revoke|set|call|load|"
    r"handler|lock|unlock|outfile|dumpfile|into|sleep|benchmark|get_lock|release_lock|load_file|"
    r"execute|prepare|deallocate|kill|shutdown|flush|reset|purge|install|uninstall|use|do|commit|"
    r"rollback|savepoint|start|begin)\b", re.IGNORECASE)

# A short map of OSCAR's tables for the AI (MySQL; names as OSCAR uses them)
SCHEMA = """\
demographic (patients): demographic_no (patient id), last_name, first_name, sex ('M'/'F'), year_of_birth,
  month_of_birth, date_of_birth (day of the month; all three are text), hin (health card), hc_type (province),
  patient_status ('AC' active, 'IN' inactive, 'DE' deceased, 'MO' moved, 'FI' fired; clinics add their own),
  roster_status ('RO' rostered, ...), provider_no (the family doctor), city, province, postal, date_joined
provider: provider_no, last_name, first_name, provider_type ('doctor', 'nurse', ...), specialty, status ('1' active)
appointment: appointment_no, provider_no, appointment_date, start_time, end_time, demographic_no, reason, type,
  status (text; starts with 't' to do, 'H' here, 'P' picked, 'E' signed, 'N' no show, 'C' cancelled, 'B' billed)
drugs (prescriptions): drugid, demographic_no, provider_no, rx_date, end_date, GN (generic name), BN (brand name),
  customName, ATC, regional_identifier (DIN), dosage, long_term (1 = long-term), archived (1 = removed).
  One drug often has several rows (each renewal): count DISTINCT patients.
allergies: allergyid, demographic_no, DESCRIPTION, reaction, severity_of_reaction, entry_date, archived (1 = removed)
dxresearch (disease registry): demographic_no, dxresearch_code, coding_system ('icd9', 'icd10', 'ichppccode', ...),
  status ('A' active, 'C' completed/resolved, 'D' deleted), start_date, update_date
issue + casemgmt_issue (the problem list / "Ongoing Concerns" in the eChart): issue(issue_id, code, description,
  type ('icd9', 'icd10', 'system')), casemgmt_issue(demographic_no, issue_id, resolved (0 open, 1 resolved), acute)
billing_on_cheader1 + billing_on_item (Ontario OHIP billing): billing_on_cheader1(id, demographic_no, provider_no,
  billing_date, status ('D' deleted)), billing_on_item(ch1_id = billing_on_cheader1.id, service_code, dx (3-digit
  diagnostic code, e.g. '250' diabetes, '401' hypertension), status ('D' deleted))
measurements: id, demographicNo, type (e.g. 'BP', 'WT', 'HT', 'A1C', 'LDL', 'BMI', 'SMK'; the names differ between
  clinics), dataField (the value, TEXT: '7.2', '130/80'), dateObserved, dateEntered, providerNo
measurementType: type, typeDisplayName, typeDescription (what each measurement type name means)
preventions: id, demographic_no, prevention_type (e.g. 'Flu', 'PAP', 'MAM', 'FOBT', 'COVID'), prevention_date,
  deleted ('0' kept, '1' deleted), refused ('0' given, '1' refused, '2' ineligible), next_date
casemgmt_note (encounter notes): note_id, demographic_no, provider_no, note (text), observation_date, uuid
  (several versions per note: the newest per uuid is the current one), archived
tickler (reminders): tickler_no, demographic_no, message, status ('A' active, 'C' completed, 'D' deleted),
  service_date, task_assigned_to
consultationRequests (referrals): requestId, demographicNo, providerNo, referalDate, serviceId, status, urgency
"""

# What makes OSCAR queries go wrong, for the AI
GUIDE = """\
- Count patients with COUNT(DISTINCT ...): joins and repeated records (renewed prescriptions, many
  measurements) otherwise count a patient more than once.
- When the clinic profile lists a table's columns, use only those names (OSCAR versions such as OSCAR 19 and
  OSCAR Pro name some columns differently). If a table in the guide is missing from the profile, don't use it.
- "Patients" means active patients (demographic.patient_status = 'AC') unless the question says otherwise.
- A diagnosis can be recorded in three places: the disease registry (dxresearch), the problem list (issue +
  casemgmt_issue) and billing (billing_on_item.dx). Use every place the clinic profile shows has data, joined
  with OR. Match code families with LIKE and cover both coding systems the profile shows: diabetes is ICD-9
  '250%' (250, 2500, 25000...) or ICD-10 'E10%'..'E14%'; hypertension ICD-9 '401%' or ICD-10 'I10%'.
- Measurement values are text ('8.4', '8.4%', '<5'): drop a % sign and keep only numbers before comparing:
  TRIM(REPLACE(m.dataField,'%','')) REGEXP '^[0-9]+([.][0-9]+)?$' AND CAST(TRIM(REPLACE(m.dataField,'%',''))
  AS DECIMAL(8,2)) > 8.
- A test is often stored under several type names (e.g. 'A1C' and 'HBA1C' from labs): use every name in the
  clinic profile that means that test, with IN (...).
- Age: TIMESTAMPDIFF(YEAR, STR_TO_DATE(CONCAT(d.year_of_birth,'-',d.month_of_birth,'-',d.date_of_birth),
  '%Y-%m-%d'), CURDATE()).
- "This year" is YEAR(date) = YEAR(CURDATE()); "in the last year" is date >= DATE_SUB(CURDATE(), INTERVAL 1 YEAR).
- Leave out removed records (archived = 1, deleted = '1', status 'D') unless asked.
- When the question is unclear (which test, which dates), choose the usual clinical meaning and say so in EXPLAIN.
"""

EXAMPLES = """\
Question: How many active patients have diabetes?
EXPLAIN: Counts active patients with diabetes (ICD-9 250 or ICD-10 E10-E14) active in the disease registry, open on the problem list, or billed in the last 3 years.
SQL: SELECT COUNT(DISTINCT d.demographic_no) AS patients FROM demographic d WHERE d.patient_status = 'AC' AND (d.demographic_no IN (SELECT x.demographic_no FROM dxresearch x WHERE x.status = 'A' AND (x.dxresearch_code LIKE '250%' OR x.dxresearch_code REGEXP '^E1[0-4]')) OR d.demographic_no IN (SELECT ci.demographic_no FROM casemgmt_issue ci JOIN issue i ON i.issue_id = ci.issue_id WHERE ci.resolved = 0 AND (i.code LIKE '250%' OR i.code REGEXP '^E1[0-4]')) OR d.demographic_no IN (SELECT h.demographic_no FROM billing_on_cheader1 h JOIN billing_on_item b ON b.ch1_id = h.id WHERE b.dx = '250' AND h.status <> 'D' AND h.billing_date >= DATE_SUB(CURDATE(), INTERVAL 3 YEAR)))

Question: How many patients had an A1C over 8 in the last year?
EXPLAIN: Counts active patients with at least one A1C reading (types A1C and HBA1C) above 8 dated in the last 12 months (any reading, not only the latest).
SQL: SELECT COUNT(DISTINCT m.demographicNo) AS patients FROM measurements m JOIN demographic d ON d.demographic_no = m.demographicNo WHERE d.patient_status = 'AC' AND m.type IN ('A1C', 'HBA1C') AND TRIM(REPLACE(m.dataField, '%', '')) REGEXP '^[0-9]+([.][0-9]+)?$' AND CAST(TRIM(REPLACE(m.dataField, '%', '')) AS DECIMAL(8,2)) > 8 AND m.dateObserved >= DATE_SUB(CURDATE(), INTERVAL 1 YEAR)

Question: Active patients by sex and age group
EXPLAIN: Counts active patients by sex in age groups (0-17, 18-64, 65 and over), from the date of birth.
SQL: SELECT t.sex, CASE WHEN t.age < 18 THEN '0-17' WHEN t.age < 65 THEN '18-64' ELSE '65+' END AS age_group, COUNT(*) AS patients FROM (SELECT d.sex, TIMESTAMPDIFF(YEAR, STR_TO_DATE(CONCAT(d.year_of_birth,'-',d.month_of_birth,'-',d.date_of_birth), '%Y-%m-%d'), CURDATE()) AS age FROM demographic d WHERE d.patient_status = 'AC') t GROUP BY t.sex, age_group ORDER BY t.sex, age_group
"""


class QueryError(Exception):
    """A query that can't be run, with the reason for the clinician."""


def _outside_strings(sql: str) -> str:
    """``sql`` with the contents of quoted strings blanked out (so words inside quotes are not keywords)."""
    out, quote, i = [], "", 0
    while i < len(sql):
        ch = sql[i]
        if quote:
            if ch == "\\" and i + 1 < len(sql):
                out.append("  ")
                i += 2
                continue
            if ch == quote:
                if i + 1 < len(sql) and sql[i + 1] == quote:     # '' inside a string
                    out.append("  ")
                    i += 2
                    continue
                quote = ""
                out.append(ch)
            else:
                out.append(" ")
        else:
            if ch in ("'", '"', "`"):
                quote = ch
            out.append(ch)
        i += 1
    if quote:
        raise QueryError("The query has a quote that is never closed.")
    return "".join(out)


# Columns that identify a patient or hold free text (which can name one). With "counts only" a query
# may not return them, only totals.
_IDENTIFYING = re.compile(
    r"\b(last_name|first_name|middle_names|alias|hin|ver|phone|phone2|cell|email|address|postal|chart_no|"
    r"demographic_no|demographicno|demographic_id|date_of_birth|dob|name|note|notes|message|comments|reason|"
    r"customname|sin|myoscar_user_name|pin|family_doctor|newsletter)\b", re.IGNORECASE)
_AGGREGATE = re.compile(r"\b(count|sum|avg)\s*\(", re.IGNORECASE)


def _split_top(text: str, sep: str = ",") -> List[str]:
    """``text`` split at ``sep`` outside brackets."""
    parts, depth, start = [], 0, 0
    for i, ch in enumerate(text):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == sep and depth == 0:
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    return parts


def _select_list(bare: str) -> str:
    """The columns of the outer SELECT (``bare`` has its quoted text blanked out)."""
    depth = 0
    for match in re.finditer(r"\(|\)|\bfrom\b", bare, re.IGNORECASE):
        token = match.group(0)
        if token == "(":
            depth += 1
        elif token == ")":
            depth -= 1
        elif depth == 0:
            return bare[len("select"):match.start()]
    return bare[len("select"):]


def _without_ages(text: str) -> str:
    """``text`` with age-in-years calculations, TIMESTAMPDIFF(YEAR, <birth date>, ...), replaced by 0: an age in
    whole years (used for age groups) doesn't identify anyone, though it is computed from the date of birth."""
    out, i = [], 0
    for match in re.finditer(r"\btimestampdiff\s*\(\s*year\b", text, re.IGNORECASE):
        if match.start() < i:
            continue
        depth, j = 0, match.end()
        k = text.index("(", match.start())
        for j in range(k, len(text)):
            if text[j] == "(":
                depth += 1
            elif text[j] == ")":
                depth -= 1
                if depth == 0:
                    break
        out.append(text[i:match.start()] + "0")
        i = j + 1
    out.append(text[i:])
    return "".join(out)


def check_counts_only(bare: str) -> None:
    """Refuse a query whose results could name or identify a patient (``bare``: quoted text blanked)."""
    columns = re.sub(r"^\s*(distinct|all)\b", "", _without_ages(_select_list(bare)), flags=re.IGNORECASE)
    items = [c.strip() for c in _split_top(columns) if c.strip()]
    if not any(_AGGREGATE.search(c) for c in items):
        raise QueryError("Counts only: the query must return totals (COUNT, SUM or AVG), not a list of records.")
    for item in items:
        if item == "*" or item.endswith(".*"):
            raise QueryError("Counts only: the query may not return whole records (*).")
        if re.search(r"\bselect\b|\bgroup_concat\s*\(|\bjson_", item, re.IGNORECASE):
            raise QueryError("Counts only: the query may not list values inside a column.")
        if _AGGREGATE.match(item) and not re.search(r"\b(min|max)\s*\(", item, re.IGNORECASE):
            continue                       # COUNT(...), SUM(...), AVG(...) and arithmetic on them
        expression = re.sub(r"\s+as\s+\w+\s*$|\s+\w+\s*$(?<=\))", "", item, flags=re.IGNORECASE)
        found = _IDENTIFYING.search(expression)
        if found:
            raise QueryError(f"Counts only: the query returns “{found.group(0)}”, which can identify a patient. "
                             "Ask for totals, or turn off Counts only.")


def check_sql(sql: str, max_rows: int = MAX_ROWS, counts_only: bool = False) -> str:
    """
    The query ready to run, or :class:`QueryError` saying why not. Only one SELECT is allowed:
    nothing that changes data, reads or writes files, or holds the database. A LIMIT is added
    when there is none, and a larger one is lowered to ``max_rows``. With ``counts_only`` the
    query must return totals and nothing that identifies a patient (:func:`check_counts_only`).
    """
    sql = (sql or "").strip()
    sql = re.sub(r"^```(?:sql)?\s*|\s*```$", "", sql, flags=re.IGNORECASE).strip()
    sql = sql.rstrip().rstrip(";").strip()
    if not sql:
        raise QueryError("There is no query.")
    if len(sql) > 16000:
        raise QueryError("The query is too long.")
    bare = _outside_strings(sql)
    if ";" in bare:
        raise QueryError("Only one statement can be run at a time.")
    if re.search(r"--|#|/\*|\*/", bare):
        raise QueryError("Comments are not allowed in the query.")
    if not re.match(r"select\b", bare, re.IGNORECASE):
        raise QueryError("Only SELECT queries (that read, never change, the records) can be run.")
    bad = _FORBIDDEN.search(bare)
    if bad:
        raise QueryError(f"The query uses “{bad.group(0).upper()}”, which is not allowed: queries can only read.")
    if re.search(r"\bfor\s+update\b|\block\s+in\s+share\s+mode\b|@@|@\w", bare, re.IGNORECASE):
        raise QueryError("The query asks for locks or server variables, which is not allowed.")
    if counts_only:
        check_counts_only(bare)
    limit = re.search(r"\blimit\s+(\d+)(?:\s*,\s*(\d+)|\s+offset\s+\d+)?\s*$", bare, re.IGNORECASE)
    if limit:
        count = int(limit.group(2) or limit.group(1))
        if count > max_rows:
            start, end = (limit.span(2) if limit.group(2) else limit.span(1))
            sql = sql[:start] + str(max_rows) + sql[end:]
    else:
        if re.search(r"\blimit\b", bare, re.IGNORECASE):
            raise QueryError("The query's LIMIT must come at the end.")
        sql = f"{sql} LIMIT {max_rows}"
    return sql


def sql_prompt(question: str, schema: str = SCHEMA, profile: str = "", counts_only: bool = True) -> str:
    """
    The AI's instructions for one question. ``profile`` (from :func:`profile_text`) says how this
    clinic's OSCAR records things: which tables hold data and the names it uses.
    """
    privacy = ("COUNTS ONLY: return totals (COUNT(DISTINCT ...), SUM, AVG), optionally grouped by categories "
               "that do not identify anyone (sex, age group, year, status, code, measurement type). Never return "
               "names, patient ids, health card numbers, dates of birth, contact details or free text.\n"
               if counts_only else
               "For a list of patients include demographic_no and the name; prefer counts when they answer the "
               "question.\n")
    clinic = (f"This clinic's OSCAR (counted from its own records; use these names and the places that hold "
              f"data):\n{profile.strip()}\n\n" if profile.strip() else
              "This clinic's OSCAR has not been checked yet: use the usual OSCAR names.\n\n")
    return (
        "You write MySQL queries for OSCAR, a Canadian electronic medical record. Write ONE read-only SELECT "
        "statement that answers the clinician's question.\n\n"
        f"Tables and columns:\n{schema}\n{clinic}"
        f"How to get it right:\n{GUIDE}\n"
        "Rules: SELECT only (no changes, no comments, no semicolons, no variables). Dates are 'YYYY-MM-DD'; "
        f"today is CURDATE().\n{privacy}"
        "If the question cannot be answered from these tables, reply with exactly: CANNOT\n\n"
        "Reply in exactly this form, nothing else:\n"
        "EXPLAIN: <one plain sentence for the clinician: what is counted, from which records, and any choice "
        "you made (codes, dates, active only)>\n"
        "SQL: <the query on one line>\n\n"
        f"Examples:\n{EXAMPLES}\n"
        f"Question: {question.strip()}")


def extract_explanation(reply: str) -> str:
    """The AI's one-sentence description of its query ("" when there is none)."""
    match = re.search(r"^\W*explain(?:ation)?\s*:\s*(.+?)\s*$", reply or "", re.IGNORECASE | re.MULTILINE)
    return match.group(1).strip()[:400] if match else ""


def extract_sql(reply: str) -> str:
    """The SQL in the AI's reply ("" when it said it can't)."""
    reply = (reply or "").strip()
    if re.fullmatch(r"\W*cannot\W*", reply, re.IGNORECASE):
        return ""
    block = re.search(r"```(?:sql)?\s*(.*?)```", reply, re.IGNORECASE | re.DOTALL)
    if block:
        reply = block.group(1)
    else:
        labelled = re.search(r"^\W*sql\s*:\s*(.+)", reply, re.IGNORECASE | re.MULTILINE | re.DOTALL)
        if labelled:
            reply = labelled.group(1)
    reply = re.sub(r"^\W*explain(?:ation)?\s*:.*$", "", reply, flags=re.IGNORECASE | re.MULTILINE).strip()
    start = re.search(r"\bselect\b", reply, re.IGNORECASE)
    if start:
        return reply[start.start():].strip()
    # Some other statement: returned as it is, so it is shown in the chat and refused there
    return reply if _FORBIDDEN.match(reply) else ""


# ------------------------------------------------------------------ learning this clinic's OSCAR
# Count-only queries that show how a clinic's OSCAR records things: which tables hold data and the
# names and codes it uses. Nothing in them identifies a patient; small counts are shown as "<5".
DISCOVERY = [
    ("tables", "Records in each table (approximate)",
     "SELECT table_name, table_rows FROM information_schema.tables WHERE table_schema = DATABASE() AND "
     "table_name IN ('demographic', 'dxresearch', 'casemgmt_issue', 'issue', 'billing_on_item', "
     "'billing_on_cheader1', 'measurements', 'measurementType', 'preventions', 'drugs', 'allergies', "
     "'appointment', 'casemgmt_note', 'tickler', 'consultationRequests')"),
    ("columns", "Columns of the main tables (as this OSCAR names them)",
     "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = DATABASE() AND "
     "table_name IN ('demographic', 'provider', 'appointment', 'drugs', 'allergies', 'dxresearch', 'issue', 'casemgmt_issue', 'billing_on_cheader1', 'billing_on_item', 'measurements', 'measurementType', 'preventions', 'casemgmt_note', 'tickler', 'consultationRequests') ORDER BY table_name, ordinal_position"),
    ("big_tables", "Other tables holding many records (this OSCAR's own additions)",
     "SELECT table_name, table_rows FROM information_schema.tables WHERE table_schema = DATABASE() AND "
     "table_rows > 1000 AND table_name NOT IN ('demographic', 'provider', 'appointment', 'drugs', 'allergies', 'dxresearch', 'issue', 'casemgmt_issue', 'billing_on_cheader1', 'billing_on_item', 'measurements', 'measurementType', 'preventions', 'casemgmt_note', 'tickler', 'consultationRequests') ORDER BY table_rows DESC LIMIT 60"),
    ("patient_status", "Patient status values (demographic.patient_status)",
     "SELECT patient_status, COUNT(*) AS patients FROM demographic GROUP BY patient_status ORDER BY patients DESC"),
    ("roster_status", "Roster status values (demographic.roster_status)",
     "SELECT roster_status, COUNT(*) AS patients FROM demographic WHERE patient_status = 'AC' "
     "GROUP BY roster_status ORDER BY patients DESC"),
    ("measurement_types", "Measurement type names in use (measurements.type)",
     "SELECT m.type, COUNT(DISTINCT m.demographicNo) AS patients FROM measurements m GROUP BY m.type "
     "ORDER BY patients DESC LIMIT 120"),
    ("dx_systems", "Disease registry: coding systems and statuses (dxresearch)",
     "SELECT coding_system, status, COUNT(DISTINCT demographic_no) AS patients FROM dxresearch "
     "GROUP BY coding_system, status ORDER BY patients DESC"),
    ("dx_codes", "Disease registry: most used active codes",
     "SELECT coding_system, dxresearch_code, COUNT(DISTINCT demographic_no) AS patients FROM dxresearch "
     "WHERE status = 'A' GROUP BY coding_system, dxresearch_code ORDER BY patients DESC LIMIT 60"),
    ("issue_codes", "Problem list: most used open issues (issue + casemgmt_issue)",
     "SELECT i.type, i.code, i.description, COUNT(DISTINCT ci.demographic_no) AS patients FROM casemgmt_issue ci "
     "JOIN issue i ON i.issue_id = ci.issue_id WHERE ci.resolved = 0 GROUP BY i.type, i.code, i.description "
     "ORDER BY patients DESC LIMIT 60"),
    ("billing_dx", "Billing: most used diagnostic codes in the last 3 years (billing_on_item.dx)",
     "SELECT b.dx, COUNT(DISTINCT h.demographic_no) AS patients FROM billing_on_item b JOIN billing_on_cheader1 h "
     "ON h.id = b.ch1_id WHERE h.billing_date >= DATE_SUB(CURDATE(), INTERVAL 3 YEAR) AND b.dx <> '' "
     "GROUP BY b.dx ORDER BY patients DESC LIMIT 60"),
    ("prevention_types", "Prevention names in use (preventions.prevention_type)",
     "SELECT prevention_type, COUNT(DISTINCT demographic_no) AS patients FROM preventions WHERE deleted = '0' "
     "GROUP BY prevention_type ORDER BY patients DESC LIMIT 60"),
    ("drugs", "Prescriptions: rows and patients (drugs)",
     "SELECT COUNT(*) AS prescriptions, COUNT(DISTINCT demographic_no) AS patients, SUM(archived = 1) AS removed, "
     "SUM(long_term = 1) AS long_term FROM drugs"),
    # Staff, not patients: each provider's number and name, so a name in a question becomes a provider number
    ("providers", "Providers (number, last name, first name, role, status)",
     "SELECT provider_no, last_name, first_name, provider_type, status FROM provider ORDER BY last_name, first_name"),
    ("provider_billing", "Providers' billing numbers (to find \"my patients\")",
     "SELECT provider_no, ohip_no FROM provider WHERE ohip_no IS NOT NULL AND ohip_no <> ''"),
]


def _small(value: str) -> str:
    """Counts under 5 shown as "<5" (they could point to one person)."""
    try:
        return "<5" if 0 < int(float(value)) < 5 else value
    except (TypeError, ValueError):
        return value


def profile_text(results: Dict[str, dict]) -> str:
    """
    A short description of the clinic's OSCAR from the :data:`DISCOVERY` results
    (``{key: {"columns": [...], "rows": [...], "error": ""}}``), for the AI and for people.
    """
    lines = []
    for key, title, _sql in DISCOVERY:
        result = results.get(key)
        if result is None:
            continue
        if result.get("error"):
            lines.append(f"{title}: not available ({result['error'][:120]})")
            continue
        rows = result.get("rows") or []
        if not rows:
            lines.append(f"{title}: none")
            continue
        if key == "columns":
            tables = {}
            for row in rows:
                if len(row) >= 2:
                    tables.setdefault(str(row[0]), []).append(str(row[1]))
            lines.append(f"{title}:")
            lines += [f"  {t}: {', '.join(c)}" for t, c in tables.items()]
            continue
        if key == "provider_billing":
            continue                                     # used to find "my patients", not needed by the AI
        if key == "providers":
            active = [r for r in rows if len(r) >= 3 and str(r[4] if len(r) > 4 else "1") in ("1", "")]
            listed = [f"{r[0]} {r[1]}, {r[2]}".strip(", ") for r in (active or rows)[:80]]
            lines.append(f"{title}: " + "; ".join(listed))
            continue
        if key == "drugs":
            names = result.get("columns") or []
            lines.append(f"{title}: " + ", ".join(f"{n} {_small(v)}" for n, v in zip(names, rows[0])))
            continue
        parts = []
        for row in rows:
            *labels, count = [str(c) for c in row]
            label = " ".join(l for l in labels if l.strip()) or "(blank)"
            parts.append(f"{label} = {_small(count)}")
        lines.append(f"{title}: " + "; ".join(parts))
    return "\n".join(lines)


def as_table(columns: List[str], rows: List[List[str]], limit: int = SHOWN_ROWS) -> str:
    """The rows as plain text, a header line then one line per row (cells separated by " | ")."""
    lines = [" | ".join(columns)] if columns else []
    for row in rows[:limit]:
        lines.append(" | ".join(str(c) for c in row))
    if len(rows) > limit:
        lines.append(f"... {len(rows) - limit} more rows not shown")
    return "\n".join(lines)


def answer_prompt(question: str, sql: str, columns: List[str], rows: List[List[str]], limit: int = SHOWN_ROWS,
                  explain: str = "") -> str:
    shown = as_table(columns, rows, limit)
    what = f"What the query counts: {explain.strip()}\n\n" if explain.strip() else ""
    return (
        "A clinician asked a question about their OSCAR medical records. This query was run and returned "
        f"{len(rows)} row{'s' if len(rows) != 1 else ''}.\n\nQuestion: {question.strip()}\n\nQuery: {sql}\n\n{what}"
        f"Results:\n{shown or '(no rows)'}\n\n"
        "Answer the question in a few sentences from these results only, and say in a few words what was "
        "counted (for example which codes or dates). If the query may not fully answer the question (for example "
        "it may miss records stored another way), say so briefly. Do not invent anything that is not in the "
        "results.")


class QueryQueue:
    """
    Approved queries waiting for the browser extension (an OSCAR page with the FreeScribe panel),
    and their results. ``on_result(query_id, columns, rows, error)`` is called when one comes back.
    """

    # A page that took a query but never answered (closed tab, browser quit) stops holding the
    # queue after this long, and the query is offered again
    CLAIM_SECONDS = 240

    def __init__(self, on_result, clock=None):
        import time
        self.on_result = on_result
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._waiting: Dict[str, dict] = {}

    def add(self, sql: str, counts_only: bool = False, max_rows: int = MAX_ROWS) -> str:
        sql = check_sql(sql, max_rows=max_rows, counts_only=counts_only)
        query_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._waiting[query_id] = {"id": query_id, "sql": sql, "claimed": False, "claimed_at": 0.0}
        return query_id

    def cancel(self, query_id: str):
        with self._lock:
            self._waiting.pop(query_id, None)

    def _release_stale(self):
        now = self._clock()
        for q in self._waiting.values():
            if q["claimed"] and now - q["claimed_at"] > self.CLAIM_SECONDS:
                q["claimed"] = False

    def next(self) -> Optional[dict]:
        """
        The oldest query no page has taken yet (for the extension's status), but only when none is
        running: one query at a time, whichever OSCAR tab runs it, so OSCAR isn't asked for several
        report pages at once.
        """
        with self._lock:
            self._release_stale()
            if any(q["claimed"] for q in self._waiting.values()):
                return None
            for q in self._waiting.values():
                if not q["claimed"]:
                    return {"id": q["id"], "sql": q["sql"]}
        return None

    def handle(self, payload: dict) -> dict:
        """The extension's requests: ``claim`` a query (only one page runs it), then send its ``result``."""
        action = str(payload.get("action", ""))
        query_id = str(payload.get("id", ""))
        with self._lock:
            q = self._waiting.get(query_id)
            if q is None:
                return {"ok": False, "gone": True}
            if action == "claim":
                self._release_stale()
                if q["claimed"] or any(o["claimed"] for o in self._waiting.values()):
                    return {"ok": False}
                q["claimed"], q["claimed_at"] = True, self._clock()
                return {"ok": True, "sql": q["sql"]}
            if action != "result":
                return {"ok": False}
            self._waiting.pop(query_id, None)
        columns = [str(c)[:200] for c in (payload.get("columns") or [])][:60]
        rows = [[str(c)[:2000] for c in row][:60] for row in (payload.get("rows") or [])[:MAX_ROWS]
                if isinstance(row, list)]
        error = str(payload.get("error") or "")[:500]
        self.on_result(query_id, columns, rows, error)
        return {"ok": True}
