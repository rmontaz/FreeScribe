"""
export.py

Save a note or document as plain text, Word (.docx) or PDF.

The writers are deliberately small and dependency-free (standard library only),
so exporting works in every packaged build without adding libraries to the
installer. They understand the light structure that generated notes usually
have: section headings (``Subjective:``, ``## Plan``, ``**Assessment**``),
bullet lines and ``**bold**`` spans.

This software is released under the AGPL-3.0 license.
"""

import datetime as _dt
import os
import re
import zipfile
from typing import List, Tuple
from xml.sax.saxutils import escape as _xml_escape

FORMATS = {
    ".txt": "Plain text",
    ".docx": "Word document",
    ".pdf": "PDF document",
}

_HEADING_HASH = re.compile(r"^\s*#{1,6}\s+(.*\S)\s*$")
_HEADING_BOLD = re.compile(r"^\s*\*\*([^*]{1,80})\*\*:?\s*$")
_HEADING_COLON = re.compile(r"^\s*([A-Z][A-Za-z0-9 /&,()'-]{1,48}):\s*$")
_BULLET = re.compile(r"^\s*([-*\u2022\u2013]|\d{1,2}[.)])\s+(.*)$")
_BOLD_SPAN = re.compile(r"\*\*(.+?)\*\*")
BULLET = "\u2022"
NBSP = "\u00a0"
_INVALID_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def parse_blocks(text: str) -> List[Tuple[str, str, str]]:
    """Split note text into ``(kind, text, marker)`` blocks.

    ``kind`` is ``heading``, ``bullet``, ``blank`` or ``text``. ``marker`` is the
    list marker to print for a ``bullet`` (a bullet character, or the original
    number such as "2.") and empty otherwise. Markdown heading markers are
    removed; inline ``**bold**`` markers are kept for the writers.
    """
    blocks = []
    for raw in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.rstrip()
        if not line.strip():
            if blocks and blocks[-1][0] != "blank":
                blocks.append(("blank", "", ""))
            continue
        match = _HEADING_HASH.match(line)
        if match:
            blocks.append(("heading", match.group(1).strip().strip("*").strip(), ""))
            continue
        match = _HEADING_BOLD.match(line)
        if match:
            heading = match.group(1).strip()
            if line.endswith(":") and not heading.endswith(":"):
                heading += ":"
            blocks.append(("heading", heading, ""))
            continue
        match = _HEADING_COLON.match(line)
        if match:
            blocks.append(("heading", match.group(1).strip() + ":", ""))
            continue
        match = _BULLET.match(line)
        if match:
            marker = match.group(1)
            blocks.append(("bullet", match.group(2).strip(), marker if marker[0].isdigit() else BULLET))
            continue
        blocks.append(("text", line.strip(), ""))
    while blocks and blocks[-1][0] == "blank":
        blocks.pop()
    return blocks


def _spans(text: str) -> List[Tuple[str, bool]]:
    """Split ``text`` into ``(fragment, is_bold)`` runs on ``**bold**`` markers."""
    spans, position = [], 0
    for match in _BOLD_SPAN.finditer(text):
        if match.start() > position:
            spans.append((text[position:match.start()], False))
        spans.append((match.group(1), True))
        position = match.end()
    if position < len(text):
        spans.append((text[position:], False))
    return [(fragment, bold) for fragment, bold in spans if fragment]


def default_filename(title: str, kind: str = "Note", when: _dt.datetime = None) -> str:
    """A safe default file name such as ``Jane Doe - Note - 2025-03-04``."""
    when = when or _dt.datetime.now()
    base = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", title or "").strip() or "Session"
    kind = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", kind or "Note").strip() or "Note"
    return f"{base[:60]} - {kind[:40]} - {when.strftime('%Y-%m-%d')}"


def export(path: str, body: str, title: str = "", subtitle: str = "") -> str:
    """Write ``body`` to ``path``; the format is chosen from the file extension."""
    extension = os.path.splitext(path)[1].lower()
    if extension == ".docx":
        write_docx(path, body, title, subtitle)
    elif extension == ".pdf":
        write_pdf(path, body, title, subtitle)
    else:
        write_text(path, body, title, subtitle)
    return path


# ----------------------------------------------------------------- plain text
def write_text(path: str, body: str, title: str = "", subtitle: str = "") -> None:
    lines = []
    if title:
        lines.append(title)
    if subtitle:
        lines.append(subtitle)
    if lines:
        lines.append("")
    lines.append((body or "").replace("\r\n", "\n").rstrip())
    with open(path, "w", encoding="utf-8", newline="\n") as file:
        file.write("\n".join(lines) + "\n")


# ----------------------------------------------------------------------- docx
_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
    '</Types>'
)
_ROOT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
    '</Relationships>'
)
_DOCUMENT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    '</Relationships>'
)
_W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
_STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<w:styles {_W}>'
    '<w:docDefaults>'
    '<w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:cs="Calibri"/>'
    '<w:sz w:val="22"/><w:szCs w:val="22"/><w:lang w:val="en-US"/></w:rPr></w:rPrDefault>'
    '<w:pPrDefault><w:pPr><w:spacing w:after="80" w:line="264" w:lineRule="auto"/></w:pPr></w:pPrDefault>'
    '</w:docDefaults>'
    '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>'
    '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/>'
    '<w:pPr><w:spacing w:after="40"/></w:pPr><w:rPr><w:b/><w:sz w:val="34"/><w:szCs w:val="34"/></w:rPr></w:style>'
    '<w:style w:type="paragraph" w:styleId="Subtitle"><w:name w:val="Subtitle"/><w:basedOn w:val="Normal"/>'
    '<w:pPr><w:spacing w:after="240"/></w:pPr><w:rPr><w:color w:val="666666"/><w:sz w:val="18"/><w:szCs w:val="18"/></w:rPr></w:style>'
    '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/>'
    '<w:pPr><w:keepNext/><w:spacing w:before="200" w:after="60"/><w:outlineLvl w:val="1"/></w:pPr>'
    '<w:rPr><w:b/><w:sz w:val="24"/><w:szCs w:val="24"/></w:rPr></w:style>'
    '<w:style w:type="paragraph" w:styleId="ListItem"><w:name w:val="List Item"/><w:basedOn w:val="Normal"/>'
    '<w:pPr><w:ind w:left="360" w:hanging="240"/><w:spacing w:after="40"/></w:pPr></w:style>'
    '</w:styles>'
)


def _clean_xml(text: str) -> str:
    return _xml_escape(_INVALID_XML.sub("", text))


def _docx_runs(text: str, bold_all: bool = False) -> str:
    runs = []
    for fragment, bold in (_spans(text) or [("", False)]):
        properties = "<w:rPr><w:b/></w:rPr>" if (bold or bold_all) else ""
        runs.append(f'<w:r>{properties}<w:t xml:space="preserve">{_clean_xml(fragment)}</w:t></w:r>')
    return "".join(runs)


def _docx_paragraph(text: str, style: str = "") -> str:
    properties = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f"<w:p>{properties}{_docx_runs(text)}</w:p>"


def write_docx(path: str, body: str, title: str = "", subtitle: str = "") -> None:
    paragraphs = []
    if title:
        paragraphs.append(_docx_paragraph(title, "Title"))
    if subtitle:
        paragraphs.append(_docx_paragraph(subtitle, "Subtitle"))
    for kind, text, marker in parse_blocks(body):
        if kind == "heading":
            paragraphs.append(_docx_paragraph(text.replace("**", ""), "Heading2"))
        elif kind == "bullet":
            paragraphs.append(_docx_paragraph(f"{marker}{NBSP}{NBSP}{text}", "ListItem"))
        elif kind == "blank":
            paragraphs.append("<w:p/>")
        else:
            paragraphs.append(_docx_paragraph(text))
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document {_W}><w:body>'
        + "".join(paragraphs)
        + '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/>'
          '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="720" w:footer="720" w:gutter="0"/>'
          '</w:sectPr></w:body></w:document>'
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
        archive.writestr("_rels/.rels", _ROOT_RELS)
        archive.writestr("word/_rels/document.xml.rels", _DOCUMENT_RELS)
        archive.writestr("word/styles.xml", _STYLES)
        archive.writestr("word/document.xml", document)


# ------------------------------------------------------------------------ pdf
# Advance widths (1/1000 em) of Helvetica for the printable ASCII range 32..126.
_HELVETICA_WIDTHS = [
    278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278,
    556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556,
    1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778,
    667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556,
    333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556,
    556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584,
]
_PDF_REPLACEMENTS = {
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',      # curly quotes
    "\u2026": "...", "\u00a0": " ", "\u2192": "->", "\u2264": "<=", "\u2265": ">=",
    "\u2212": "-", "\u2011": "-", "\t": "    ",
}
_PAGE_W, _PAGE_H = 612.0, 792.0          # US Letter, points
_MARGIN_X, _MARGIN_TOP, _MARGIN_BOTTOM = 72.0, 72.0, 72.0


def _pdf_normalise(text: str) -> str:
    for source, target in _PDF_REPLACEMENTS.items():
        text = text.replace(source, target)
    return _INVALID_XML.sub("", text)


def _pdf_width(text: str, size: float, bold: bool = False) -> float:
    total = 0
    for char in text:
        code = ord(char)
        total += _HELVETICA_WIDTHS[code - 32] if 32 <= code <= 126 else 556
    return total * size / 1000.0 * (1.06 if bold else 1.0)


def _pdf_literal(text: str) -> bytes:
    """Encode text as a PDF string literal in WinAnsi (cp1252)."""
    encoded = text.encode("cp1252", errors="replace")
    out = bytearray(b"(")
    for byte in encoded:
        if byte in (0x28, 0x29, 0x5C):
            out += b"\\" + bytes([byte])
        elif byte < 32 or byte > 126:
            out += b"\\%03o" % byte
        else:
            out.append(byte)
    out += b")"
    return bytes(out)


def _wrap_spans(spans: List[Tuple[str, bool]], size: float, max_width: float) -> List[List[Tuple[str, bool]]]:
    """Word-wrap formatted spans into lines no wider than ``max_width`` points."""
    words = []                                 # (word, bold, leading_space)
    for fragment, bold in spans:
        pieces = re.split(r"(\s+)", fragment)
        pending_space = False
        for piece in pieces:
            if not piece:
                continue
            if piece.isspace():
                pending_space = True
            else:
                words.append((piece, bold, pending_space or False))
                pending_space = False
        if pending_space and words:
            words.append(("", bold, True))
    lines, current, width = [], [], 0.0
    space_width = _pdf_width(" ", size)
    for word, bold, leading_space in words:
        if not word:
            if current:
                current.append((" ", bold))
                width += space_width
            continue
        word_width = _pdf_width(word, size, bold)
        gap = space_width if (current and leading_space) else 0.0
        if current and width + gap + word_width > max_width:
            lines.append(current)
            current, width, gap = [], 0.0, 0.0
        # Break words that are longer than a whole line (long URLs, etc.).
        while word_width > max_width and len(word) > 1:
            cut = len(word)
            while cut > 1 and _pdf_width(word[:cut], size, bold) > max_width:
                cut -= 1
            lines.append([(word[:cut], bold)])
            word = word[cut:]
            word_width = _pdf_width(word, size, bold)
        if gap:
            current.append((" ", bold))
            width += gap
        current.append((word, bold))
        width += word_width
    if current:
        lines.append(current)
    cleaned = []
    for line in lines or [[]]:
        while line and line[-1][0] == " ":
            line = line[:-1]
        cleaned.append(line)
    return cleaned


class _PdfLayout:
    """Flows lines onto pages and records the drawing operators for each page."""

    def __init__(self):
        self.pages = [[]]
        self.y = _PAGE_H - _MARGIN_TOP

    def ensure(self, height: float):
        if self.y - height < _MARGIN_BOTTOM:
            self.pages.append([])
            self.y = _PAGE_H - _MARGIN_TOP

    def space(self, amount: float):
        if self.y < _PAGE_H - _MARGIN_TOP:      # never start a page with a gap
            self.y -= amount

    def line(self, spans, size, leading, x=_MARGIN_X, gray=0.0, bold_all=False):
        self.ensure(leading)
        self.y -= leading
        ops = [b"BT", b"%.2f g" % gray, b"1 0 0 1 %.2f %.2f Tm" % (x, self.y)]
        # Join neighbouring fragments that share a font so each run is written once.
        runs = []
        for fragment, bold in spans:
            if not fragment:
                continue
            bold = bool(bold or bold_all)
            if runs and runs[-1][1] == bold:
                runs[-1][0] += fragment
            else:
                runs.append([fragment, bold])
        for fragment, bold in runs:
            ops.append((b"/F2" if bold else b"/F1") + b" %.1f Tf" % size)
            ops.append(_pdf_literal(fragment) + b" Tj")
        ops.append(b"ET")
        self.pages[-1].append(b"\n".join(ops))

    def paragraph(self, text, size=11.0, leading=15.0, indent=0.0, marker="", gray=0.0, bold_all=False):
        spans = _spans(_pdf_normalise(text)) or [("", False)]
        if bold_all:
            spans = [(fragment, True) for fragment, _ in spans]
        marker_width = _pdf_width(marker, size) + 6.0 if marker else 0.0
        indent = max(indent, marker_width) if marker else indent
        width = _PAGE_W - 2 * _MARGIN_X - indent
        lines = _wrap_spans(spans, size, width)
        for index, line in enumerate(lines):
            if marker and index == 0:
                self.ensure(leading)
                saved = self.y
                self.line([(marker, False)], size, leading, x=_MARGIN_X + indent - marker_width, gray=gray)
                self.y = saved
            self.line(line, size, leading, x=_MARGIN_X + indent, gray=gray)


def write_pdf(path: str, body: str, title: str = "", subtitle: str = "") -> None:
    layout = _PdfLayout()
    if title:
        layout.paragraph(title, size=17.0, leading=22.0, bold_all=True)
    if subtitle:
        layout.paragraph(subtitle, size=9.0, leading=14.0, gray=0.42)
    if title or subtitle:
        layout.space(12.0)
    for kind, text, marker in parse_blocks(body):
        if kind == "heading":
            layout.space(7.0)
            layout.ensure(15.0 * 2)             # keep a heading with its first line
            layout.paragraph(text.replace("**", ""), size=11.5, leading=16.0, bold_all=True)
        elif kind == "bullet":
            layout.paragraph(text, indent=16.0, marker=marker)
        elif kind == "blank":
            layout.space(7.0)
        else:
            layout.paragraph(text)

    total = len(layout.pages)
    objects = []                                 # list of bytes, object n is objects[n-1]
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(b"")                          # pages tree, filled in below
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
    kids = []
    for number, operators in enumerate(layout.pages, start=1):
        footer = f"Page {number} of {total}"
        footer_x = (_PAGE_W - _pdf_width(footer, 8.0)) / 2
        stream = b"\n".join(operators + [
            b"BT", b"0.55 g", b"/F1 8.0 Tf", b"1 0 0 1 %.2f %.2f Tm" % (footer_x, 40.0),
            _pdf_literal(footer) + b" Tj", b"ET",
        ])
        content_id = len(objects) + 2
        page_id = len(objects) + 1
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] /Contents %d 0 R "
            b"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> >>" % (int(_PAGE_W), int(_PAGE_H), content_id))
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        kids.append(b"%d 0 R" % page_id)
    objects[1] = b"<< /Type /Pages /Kids [" + b" ".join(kids) + b"] /Count %d >>" % total

    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for index, data in enumerate(objects, start=1):
        offsets.append(len(output))
        output += b"%d 0 obj\n" % index + data + b"\nendobj\n"
    xref_position = len(output)
    output += b"xref\n0 %d\n" % (len(objects) + 1)
    output += b"0000000000 65535 f \n"
    for offset in offsets:
        output += b"%010d 00000 n \n" % offset
    output += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref_position)
    with open(path, "wb") as file:
        file.write(bytes(output))
