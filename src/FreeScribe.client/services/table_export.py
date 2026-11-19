"""
table_export.py

Rows and columns (for example a query of OSCAR's records) saved as a CSV file or an Excel
workbook. The workbook is written directly (an .xlsx file is a zip of a few XML files), so
nothing extra has to be installed; Excel, Numbers, LibreOffice and Google Sheets open it.

This software is released under the AGPL-3.0 license.
"""

from __future__ import annotations

import csv
import os
import re
import tempfile
import zipfile
from typing import List, Sequence
from xml.sax.saxutils import escape

# A cell is written as a number only when it plainly is one: no leading zeros (health card and
# chart numbers keep them), and short enough that Excel keeps every digit.
_NUMBER = re.compile(r"-?(0|[1-9]\d{0,14})(\.\d+)?")
# Characters XML does not allow (control characters other than tab and line breaks)
_BAD_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
# Text a spreadsheet would treat as a formula when the file is opened
_FORMULA = ("=", "+", "-", "@", "\t", "\r")


def _rows(columns: Sequence[str], rows: Sequence[Sequence]) -> List[List[str]]:
    width = max([len(columns)] + [len(r) for r in rows]) if (columns or rows) else 0
    table = [list(map(str, columns)) + [""] * (width - len(columns))]
    for row in rows:
        cells = ["" if c is None else str(c) for c in row]
        table.append(cells + [""] * (width - len(cells)))
    return table


def _safe_text(cell: str) -> str:
    """Text that a spreadsheet will show as it is, never run as a formula."""
    if cell.startswith(_FORMULA) and not _NUMBER.fullmatch(cell):
        return "'" + cell
    return cell


def _atomic_write(path: str, write):
    directory = os.path.dirname(os.path.abspath(path)) or "."
    handle, temp = tempfile.mkstemp(prefix=".export_", dir=directory)
    os.close(handle)
    try:
        write(temp)
        os.replace(temp, path)
    except BaseException:
        if os.path.exists(temp):
            os.remove(temp)
        raise


def to_csv(path: str, columns: Sequence[str], rows: Sequence[Sequence]) -> None:
    """A CSV file (UTF-8 with the mark Excel looks for, so accents show correctly)."""
    table = _rows(columns, rows)

    def write(temp):
        with open(temp, "w", newline="", encoding="utf-8-sig") as file:
            out = csv.writer(file)
            out.writerow(table[0])
            for row in table[1:]:
                out.writerow([_safe_text(c) for c in row])
    _atomic_write(path, write)


def _column_name(index: int) -> str:
    name = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        name = chr(65 + rest) + name
    return name


def _cell(ref: str, value: str, header: bool) -> str:
    style = ' s="1"' if header else ""
    if not header and _NUMBER.fullmatch(value):
        return f'<c r="{ref}"{style}><v>{value}</v></c>'
    text = escape(_BAD_XML.sub("", value))
    space = ' xml:space="preserve"' if text != text.strip() else ""
    return f'<c r="{ref}" t="inlineStr"{style}><is><t{space}>{text}</t></is></c>'


def to_xlsx(path: str, columns: Sequence[str], rows: Sequence[Sequence], sheet: str = "Results") -> None:
    """An Excel workbook: one sheet, a bold header row that stays in view, sensible column widths."""
    table = _rows(columns, rows)
    width = len(table[0])
    widths = []
    for c in range(width):
        longest = max((len(r[c]) for r in table[:500]), default=0)
        widths.append(min(60, max(8, longest + 2)))
    cols = "".join(f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>' for i, w in enumerate(widths))
    body = []
    for r, row in enumerate(table, start=1):
        cells = "".join(_cell(f"{_column_name(c)}{r}", v, r == 1) for c, v in enumerate(row) if v != "")
        body.append(f'<row r="{r}">{cells}</row>')
    last = f"{_column_name(max(width, 1) - 1)}{len(table)}"
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<dimension ref="A1:{last}"/>'
        '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" '
        'state="frozen"/></sheetView></sheetViews>'
        + (f"<cols>{cols}</cols>" if cols else "")
        + f'<sheetData>{"".join(body)}</sheetData>'
        + (f'<autoFilter ref="A1:{last}"/>' if width else "")
        + '</worksheet>')
    name = escape(re.sub(r"[\[\]:*?/\\]", " ", sheet)[:31] or "Results")
    files = {
        "[Content_Types].xml":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/styles.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            '</Types>',
        "_rels/.rels":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f'<sheets><sheet name="{name}" sheetId="1" r:id="rId1"/></sheets>'
            + (f'<definedNames><definedName name="_xlnm._FilterDatabase" localSheetId="0" hidden="1">'
               f"'{name}'!$A$1:${_column_name(max(width, 1) - 1)}${len(table)}</definedName></definedNames>"
               if width else "")
            + '</workbook>',
        "xl/_rels/workbook.xml.rels":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            'Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
            'Target="styles.xml"/></Relationships>',
        "xl/styles.xml":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
            '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
            '<fills count="2"><fill><patternFill patternType="none"/></fill>'
            '<fill><patternFill patternType="gray125"/></fill></fills>'
            '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
            '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>'
            '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
            '</styleSheet>',
        "xl/worksheets/sheet1.xml": sheet_xml,
    }

    def write(temp):
        with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED) as z:
            for name_, data in files.items():
                z.writestr(name_, data)
    _atomic_write(path, write)


def as_tsv(columns: Sequence[str], rows: Sequence[Sequence]) -> str:
    """Tab-separated text (with the header) that pastes into a spreadsheet as columns."""
    def clean(c):
        return str(c).replace("\t", " ").replace("\r", " ").replace("\n", " ")
    lines = ["\t".join(clean(c) for c in columns)] if columns else []
    lines += ["\t".join(clean(c) for c in row) for row in rows]
    return "\n".join(lines)
