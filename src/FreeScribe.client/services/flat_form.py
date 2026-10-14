"""
flat_form.py

Turns a "flat" PDF form (one that was printed to PDF, with no fillable fields)
into a fillable one, so the normal form-filling steps can be used on it.

The page is searched for the places a person would write on:

* runs of underscores in the text: ``Patient name: ________``
* drawn horizontal lines: a label followed by a line, or a signature line with
  its label underneath
* empty drawn boxes (a label above the box, or a label inside its top-left
  corner), which become multi-line text fields
* small squares and box characters (☐), which become checkboxes

Each blank becomes a real form field on a copy of the PDF, named after its label
(stored as the field's tooltip). Nothing is written into the original file.

Scanned forms (pages that are only pictures) have no text to read and are not
handled here; :func:`is_scanned` lets the caller explain that.

This software is released under the AGPL-3.0 license.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

import pymupdf

from services.form_filler import is_connective

TEXT = "text"
CHECKBOX = "checkbox"

_UNDERSCORES = re.compile(r"_{3,}")
_BOX_CHARACTERS = {"☐", "☒", "☑", "□", "■", "❏", "❐", "❑", "❒", "▢", "◻", "◽", "⬜", "○", "◯", "❍", "◦",
                   "[ ]", "[]", "( )", "()"}
# Symbol fonts whose letters are drawn as boxes, ticks and bullets (their text reads as junk letters)
_SYMBOL_FONT = re.compile(r"dingbat|wingding|webding|marlett|symbol", re.IGNORECASE)

MIN_LINE_LENGTH = 40      # points; shorter strokes are decoration
LINE_FIELD_HEIGHT = 15    # height of a field placed on a line
SQUARE_MIN, SQUARE_MAX = 5, 20


@dataclass
class Blank:
    """A place on the page to write on."""

    page: int
    rect: Tuple[float, float, float, float]   # x0, y0, x1, y1 in PyMuPDF page coordinates (top-left origin)
    kind: str = TEXT
    label: str = ""
    multiline: bool = False
    comb: int = 0      # number of character boxes when the blank is a row of them


@dataclass
class _Word:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str

    @property
    def mid(self):
        return (self.y0 + self.y1) / 2


# ================================================================== detection
def is_scanned(path: str) -> bool:
    """True when no page has text (a scanned or photographed form)."""
    with pymupdf.open(path) as doc:
        return all(not page.get_text("text").strip() for page in doc)


def find_blanks(path: str, keep_unlabelled: bool = False) -> List[Blank]:
    """
    Every blank found on every page, in reading order.

    Text blanks without any nearby words are left out unless ``keep_unlabelled``
    (form filling needs a label to know what to write; mapping a form does not).
    """
    blanks: List[Blank] = []
    with pymupdf.open(path) as doc:
        for index, page in enumerate(doc):
            blanks.extend(_page_blanks(page, index, keep_unlabelled))
    _make_labels_unique(blanks)
    return blanks


def page_words(page) -> List["_Word"]:
    """The words on ``page`` with their positions (PyMuPDF page coordinates)."""
    return [_Word(*w[:5]) for w in page.get_text("words")]


def label_blanks(blanks: List[Blank], words: List["_Word"]):
    """Fill in the label of every unlabelled blank from the words around it (blanks of one page)."""
    blanks.sort(key=lambda b: (round(b.rect[1] / 4), b.rect[0]))
    _label_by_line(blanks, words)


def _page_blanks(page, page_index, keep_unlabelled: bool = False, parts=None) -> List[Blank]:
    """
    The blanks of one page. ``parts`` is (words, hlines, vlines, boxes) already read
    from a page that is only a picture (:mod:`services.scan_form`); otherwise they
    are read from the PDF.
    """
    page_rect = page.rect
    found: List[Blank] = []
    if parts is not None:
        words, hlines, _vlines, rects = parts
        hlines, rects, glyph_boxes, table_areas = list(hlines), list(rects), [], []
    else:
        words = page_words(page)
        glyph_boxes = _glyph_checkboxes(page, page_index)
        # The box characters themselves are not label text
        words = [w for w in words if not any(_rects_overlap((w.x0, w.y0, w.x1, w.y1), b.rect) and
                                             len(w.text.strip()) <= 3 for b in glyph_boxes)]
        hlines, vlines, rects = _drawings(page)

        table_blanks, table_areas = _table_blanks(page, words, page_index)
        found += table_blanks

        # Table borders are drawn as lines; they are not blanks themselves
        hlines = [l for l in hlines if not any(_inside((l[0], l[1], l[2], l[1]), area) for area in table_areas)]
        rects += _boxes_from_lines(hlines, vlines)
    rects = [r for r in rects if not any(_inside(r, area) for area in table_areas)]
    box_edges = [r for r in rects]
    hlines = [l for l in hlines if not any(_on_box_edge(l, r) for r in box_edges)]

    found += _underscore_blanks(words, page_index)
    found += glyph_boxes
    found += _line_blanks(hlines, words, page_index, found)
    found += _rect_blanks(rects, words, page_index, page_rect)

    # Several blanks on one line read left to right; give each the text before it
    found.sort(key=lambda b: (round(b.rect[1] / 4), b.rect[0]))
    _label_by_line(found, words)
    return [b for b in found if b.label or b.kind == CHECKBOX or keep_unlabelled]


def _drawings(page):
    """Horizontal lines, vertical lines and rectangles drawn on the page."""
    hlines, vlines, rects = [], [], []
    try:
        drawings = page.get_drawings()
    except Exception:
        return hlines, vlines, rects
    for drawing in drawings:
        items = drawing.get("items", [])
        bounds = drawing.get("rect")
        # A small closed curve: a radio button circle
        if items and bounds is not None and all(item[0] == "c" for item in items) and \
                SQUARE_MIN <= bounds.width <= SQUARE_MAX and abs(bounds.width - bounds.height) <= 2:
            rects.append((bounds.x0, bounds.y0, bounds.x1, bounds.y1))
            continue
        for item in items:
            kind = item[0]
            if kind == "l":
                start, end = item[1], item[2]
                if abs(start.y - end.y) < 1 and abs(end.x - start.x) >= 2:
                    hlines.append((min(start.x, end.x), (start.y + end.y) / 2, max(start.x, end.x)))
                elif abs(start.x - end.x) < 1 and abs(end.y - start.y) >= 2:
                    vlines.append(((start.x + end.x) / 2, min(start.y, end.y), max(start.y, end.y)))
            elif kind == "re":
                rect = item[1]
                if rect.height < 2 and rect.width >= 2:
                    hlines.append((rect.x0, (rect.y0 + rect.y1) / 2, rect.x1))   # a thin rectangle drawn as a line
                elif rect.width < 2 and rect.height >= 2:
                    vlines.append(((rect.x0 + rect.x1) / 2, rect.y0, rect.y1))
                elif rect.width >= 2 and rect.height >= 2:
                    rects.append((rect.x0, rect.y0, rect.x1, rect.y1))
    hlines = _merge_segments(hlines)
    return hlines, vlines, rects


def _merge_segments(hlines):
    """Join horizontal pieces that continue each other (word processors draw borders per cell)."""
    merged = []
    for x0, y, x1 in sorted(hlines, key=lambda l: (round(l[1]), l[0])):
        if merged and abs(merged[-1][1] - y) < 1 and x0 <= merged[-1][2] + 1.5:
            last = merged[-1]
            merged[-1] = (min(last[0], x0), last[1], max(last[2], x1))
        else:
            merged.append((x0, y, x1))
    return merged


def _boxes_from_lines(hlines, vlines):
    """Rectangles drawn as four separate lines (a one-cell table, a framed box)."""
    boxes = []
    for i, (tx0, ty, tx1) in enumerate(hlines):
        for bx0, by, bx1 in hlines[i + 1:]:
            if not (SQUARE_MIN <= by - ty <= 500 and abs(bx0 - tx0) <= 2 and abs(bx1 - tx1) <= 2):
                continue
            left = any(abs(x - tx0) <= 2 and y0 <= ty + 2 and y1 >= by - 2 for x, y0, y1 in vlines)
            right = any(abs(x - tx1) <= 2 and y0 <= ty + 2 and y1 >= by - 2 for x, y0, y1 in vlines)
            if left and right:
                boxes.append((tx0, ty, tx1, by))
                break
    return boxes


def _on_box_edge(line, rect):
    x0, y, x1 = line
    return (abs(y - rect[1]) <= 1.5 or abs(y - rect[3]) <= 1.5) and x0 >= rect[0] - 2 and x1 <= rect[2] + 2


def _inside(inner, outer, margin=2.0):
    return (inner[0] >= outer[0] - margin and inner[1] >= outer[1] - margin and
            inner[2] <= outer[2] + margin and inner[3] <= outer[3] + margin)


# ---------------------------------------------------------------------- tables
def _table_blanks(page, words, page_index):
    """Empty cells of ruled tables, labelled by the cell to their left or the column heading."""
    import contextlib
    import io
    blanks, areas = [], []
    try:
        with contextlib.redirect_stdout(io.StringIO()):   # PyMuPDF prints an advert for an add-on
            tables = page.find_tables().tables
    except Exception:
        return blanks, areas

    for table in tables:
        areas.append(tuple(table.bbox))
        grid = [[tuple(cell) if cell else None for cell in row.cells] for row in table.rows]
        texts = [[_join(_words_in(words, cell)) if cell else "" for cell in row] for row in grid]
        for r, row in enumerate(grid):
            for c, cell in enumerate(row):
                if cell is None or texts[r][c]:
                    continue
                label = next((texts[r][k] for k in range(c - 1, -1, -1) if texts[r][k]), "")
                if not label and r > 0 and texts[0][c]:
                    label = texts[0][c]                              # column heading
                if not label:
                    label = _text_above(words, cell)
                x0, y0, x1, y1 = cell
                if x1 - x0 < 8 or y1 - y0 < 8:
                    continue
                blanks.append(Blank(page_index, (x0 + 2, y0 + 1, x1 - 2, y1 - 1), TEXT, label=label,
                                    multiline=(y1 - y0) >= 30))
    return blanks, areas


def _words_in(words, rect):
    """Words whose centre lies in ``rect`` (a word's outline can poke out of a tight cell)."""
    x0, y0, x1, y1 = rect
    return [w for w in words if x0 <= (w.x0 + w.x1) / 2 <= x1 and y0 <= w.mid <= y1]


def _text_above(words, rect, reach=30):
    """The nearest line of text just above ``rect`` that overlaps it horizontally."""
    x0, y0, x1, _y1 = rect
    above = [w for w in words if 0 <= y0 - w.y1 <= reach and _overlap(w.x0, w.x1, x0, x1) > 0]
    if not above:
        return ""
    nearest = max(w.y1 for w in above)
    return _join([w for w in above if abs(w.y1 - nearest) <= 3])


# --------------------------------------------------------------- underscores
def _underscore_blanks(words, page_index) -> List[Blank]:
    blanks = []
    for word in words:
        for match in _UNDERSCORES.finditer(word.text):
            # Words such as "name:_____" carry their label; place the blank on the underscores only
            per_char = (word.x1 - word.x0) / max(1, len(word.text))
            x0 = word.x0 + per_char * match.start()
            x1 = word.x0 + per_char * match.end()
            blanks.append(Blank(page_index, (x0, word.y0 - 1, x1, word.y1 - 1), TEXT))
    return blanks


def _glyph_checkboxes(page, page_index) -> List[Blank]:
    """Tick boxes typed as characters: ☐ and friends, or any letter of a symbol font such as ZapfDingbats."""
    blanks = []
    try:
        blocks = page.get_text("rawdict")["blocks"]
    except Exception:
        return blanks
    for block in blocks:
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                symbol_font = bool(_SYMBOL_FONT.search(span.get("font", "")))
                chars = span.get("chars", [])
                text = "".join(ch.get("c", "") for ch in chars)
                if text.strip() in _BOX_CHARACTERS and len(text.strip()) > 1:
                    blanks.append(_glyph_blank(chars[0]["bbox"][0], chars[-1]["bbox"][2], chars[0], span, page_index))
                    continue
                for ch in chars:
                    c = ch.get("c", "")
                    if not c.strip():
                        continue
                    if c in _BOX_CHARACTERS or symbol_font:
                        blanks.append(_glyph_blank(ch["bbox"][0], ch["bbox"][2], ch, span, page_index))
    return blanks


def _glyph_blank(x0, x1, char, span, page_index) -> Blank:
    """A square tick box over a box character, sitting on the text baseline."""
    size = float(span.get("size") or 10)
    side = max(4.0, min(x1 - x0, size * 0.85))
    baseline = char.get("origin", (x0, char["bbox"][3]))[1]
    centre = (x0 + x1) / 2
    return Blank(page_index, (centre - side / 2, baseline - side, centre + side / 2, baseline), CHECKBOX)


# ---------------------------------------------------------------------- lines
def _line_blanks(lines, words, page_index, already) -> List[Blank]:
    blanks = []
    for x0, y, x1 in lines:
        if x1 - x0 < MIN_LINE_LENGTH:
            continue
        # Text sitting on the line means it underlines that text (a heading, a link)
        on_line = [w for w in words if 0 <= y - w.y1 <= 4 and _overlap(w.x0, w.x1, x0, x1) > 0.3 * (x1 - x0)]
        if on_line:
            continue
        rect = (x0, y - LINE_FIELD_HEIGHT, x1, y - 0.5)
        if any(_rects_overlap(rect, b.rect) for b in already):
            continue
        blank = Blank(page_index, rect, TEXT)
        # A signature line usually has its label underneath it
        below = [w for w in words if 0 <= w.y0 - y <= 14 and _overlap(w.x0, w.x1, x0, x1) > 0]
        left_on_line = [w for w in words if abs(w.y1 - y) <= 6 and w.x1 <= x0 + 2]
        if below and not left_on_line:
            blank.label = _join(below)
        blanks.append(blank)
    return blanks


# ---------------------------------------------------------------------- boxes
def _merge_character_boxes(rects):
    """
    Rows of touching boxes of the same size (a health card number written one
    character per box) become one rectangle; returns (merged rows, other rectangles).
    """
    small = sorted((r for r in rects if 6 <= r[3] - r[1] <= 30 and r[2] - r[0] <= 30),
                   key=lambda r: (round(r[1]), r[0]))
    rows, used = [], set()
    for index, rect in enumerate(small):
        if index in used:
            continue
        row = [rect]
        for other_index in range(index + 1, len(small)):
            other = small[other_index]
            last = row[-1]
            if other_index in used or abs(other[1] - last[1]) > 1.5 or abs(other[3] - last[3]) > 1.5:
                continue
            if 0 <= other[0] - last[2] <= 1.5 or abs(other[0] - last[2]) <= 1.5:
                row.append(other)
                used.add(other_index)
        if len(row) >= 2:
            used.add(index)
            rows.append((row[0][0], min(r[1] for r in row), row[-1][2], max(r[3] for r in row), len(row)))
    others = [r for i, r in enumerate(small) if i not in used] + \
             [r for r in rects if not (6 <= r[3] - r[1] <= 30 and r[2] - r[0] <= 30)]
    return rows, others


def _rect_blanks(rects, words, page_index, page_rect) -> List[Blank]:
    blanks = []
    rows, rects = _merge_character_boxes(rects)
    for x0, y0, x1, y1, count in rows:
        if not _words_in(words, (x0, y0, x1, y1)):
            blanks.append(Blank(page_index, (x0, y0, x1, y1), TEXT, comb=count))
    for x0, y0, x1, y1 in rects:
        width, height = x1 - x0, y1 - y0
        inside = _words_in(words, (x0, y0, x1, y1))

        if SQUARE_MIN <= width <= SQUARE_MAX and SQUARE_MIN <= height <= SQUARE_MAX and abs(width - height) <= 3:
            if not inside:
                blanks.append(Blank(page_index, (x0, y0, x1, y1), CHECKBOX))
            continue

        if width < MIN_LINE_LENGTH or height < 12:
            continue
        if width > page_rect.width * 0.9 and height > page_rect.height * 0.5:
            continue  # a page frame

        if not inside:
            blanks.append(Blank(page_index, (x0 + 3, y0 + 2, x1 - 3, y1 - 2), TEXT, multiline=height >= 30))
            continue

        # A label in the box's top-left corner, with room to write underneath
        top = min(w.y0 for w in inside)
        label_words = [w for w in inside if w.y0 <= top + 3]
        if len(label_words) == len(inside) and top - y0 <= 8 and label_words[0].x0 - x0 <= 12:
            label_bottom = max(w.y1 for w in label_words)
            if y1 - label_bottom >= 12:
                blanks.append(Blank(page_index, (x0 + 3, label_bottom + 1, x1 - 3, y1 - 2), TEXT,
                                    label=_join(label_words), multiline=(y1 - label_bottom) >= 30))
    return blanks


# --------------------------------------------------------------------- labels
def _label_by_line(blanks: List[Blank], words: List[_Word]):
    """Give each unlabelled blank the words before it on its line, or the words above it."""
    for blank in blanks:
        if blank.label:
            continue
        x0, y0, x1, y1 = blank.rect
        mid = (y0 + y1) / 2
        same_line = [w for w in words if abs(w.mid - mid) <= max(5, (y1 - y0) / 2) or
                     (blank.kind == TEXT and abs(w.y1 - y1) <= 4)]
        same_line = [w for w in same_line if not _UNDERSCORES.fullmatch(w.text)]
        others = [b for b in blanks if b is not blank and abs((b.rect[1] + b.rect[3]) / 2 - mid) <= 6]

        if blank.kind == CHECKBOX:
            blank.label = _checkbox_label(blank, same_line, others)
            continue

        # Words between the previous blank on this line and this one
        boundary = max([b.rect[2] for b in others if b.rect[2] <= x0 + 1] or [-1e9])
        before = [w for w in same_line if w.x1 <= x0 + 3 and w.x0 >= boundary - 1]
        label = _join([_strip_underscores(w) for w in before])
        previous = next((b for b in reversed(others) if b.rect[2] <= x0 + 1), None)
        if not label and previous is not None and previous.label and x0 - previous.rect[2] <= 40:
            blank.label = previous.label        # "Date of birth": YYYY, then MM, then DD boxes
            continue
        if label and is_connective(label):
            lead = [w for w in same_line if w.x1 <= boundary + 3]
            previous = next((b for b in reversed(others) if b.rect[2] <= x0 + 1), None)
            lead_text = previous.label if previous and previous.label else _join(lead)
            if lead_text:
                label = f"{lead_text} … {label}"
        if not label:
            above = [w for w in words if 0 <= y0 - w.y1 <= 16 and _overlap(w.x0, w.x1, x0, x1) > 0]
            label = _join(above)
        blank.label = label


def _checkbox_label(blank, same_line, others) -> str:
    x0, _y0, x1, _y1 = blank.rect
    # The option text runs from the box to the next box on the line
    next_box = min([b.rect[0] for b in others if b.rect[0] >= x1 - 1] or [1e9])
    after = [w for w in same_line if w.x0 >= x1 - 1 and w.x1 <= next_box + 1 and w.x0 - x1 < 120]
    option = _join(after)
    # The question is the text before the first box on the line ("Allergies:")
    first_box = min([b.rect[0] for b in others + [blank]])
    question = _join([w for w in same_line if w.x1 <= first_box + 1 and first_box - w.x1 < 200])
    if option and question:
        return f"{question}: {option}"
    return option or question


def _strip_underscores(word: _Word) -> _Word:
    return _Word(word.x0, word.y0, word.x1, word.y1, _UNDERSCORES.sub(" ", word.text))


def _join(words) -> str:
    text = " ".join(w.text for w in sorted(words, key=lambda w: (round(w.y0 / 4), w.x0)))
    text = re.sub(r"\s+", " ", _UNDERSCORES.sub(" ", text)).strip(" :.\t")
    return text[:100]


def _overlap(a0, a1, b0, b1) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def _rects_overlap(a, b) -> bool:
    return _overlap(a[0], a[2], b[0], b[2]) > 0 and _overlap(a[1], a[3], b[1], b[3]) > 0


def _make_labels_unique(blanks: List[Blank]):
    seen = {}
    for blank in blanks:
        key = blank.label.lower()
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            blank.label = f"{blank.label} ({seen[key]})"


# =================================================================== creating
def make_fillable(source_path: str, target_path: str, blanks: Optional[List[Blank]] = None) -> int:
    """
    Save a copy of ``source_path`` with a form field on every blank.

    Returns the number of fields added.
    """
    blanks = find_blanks(source_path) if blanks is None else blanks
    added = 0
    with pymupdf.open(source_path) as doc:
        for number, blank in enumerate(blanks, 1):
            page = doc[blank.page]
            widget = pymupdf.Widget()
            slug = re.sub(r"[^A-Za-z0-9]+", "_", blank.label).strip("_")[:40] or "field"
            widget.field_name = f"p{blank.page + 1}_{number:03d}_{slug}"
            widget.field_label = blank.label      # stored as the tooltip, which becomes the label
            widget.rect = pymupdf.Rect(*blank.rect)
            widget.border_width = 0
            widget.border_color = None
            widget.fill_color = None
            if blank.kind == CHECKBOX:
                widget.field_type = pymupdf.PDF_WIDGET_TYPE_CHECKBOX
                widget.field_value = False
            else:
                widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
                height = blank.rect[3] - blank.rect[1]
                widget.text_font = "Helv"
                widget.text_fontsize = 10 if blank.multiline else max(6, min(10, height * 0.7))
                widget.text_color = (0.05, 0.05, 0.2)
                if blank.multiline:
                    widget.field_flags |= pymupdf.PDF_TX_FIELD_IS_MULTILINE
            page.add_widget(widget)
            added += 1
        doc.save(target_path, garbage=1, deflate=True)
    _register_font(target_path)
    return added


def _register_font(path: str):
    """
    Declare the Helvetica font the new fields use in the form's resources, so every
    viewer (and the step that later writes the values) draws them the same way.
    """
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, TextStringObject

    writer = PdfWriter(clone_from=path)
    acroform = writer._root_object.get("/AcroForm")
    if acroform is None:
        return
    acroform = acroform.get_object()
    resources = acroform.setdefault(NameObject("/DR"), DictionaryObject()).get_object()
    fonts = resources.setdefault(NameObject("/Font"), DictionaryObject()).get_object()
    for key, base_font, encoding in (("/Helv", "/Helvetica", "/WinAnsiEncoding"),
                                     ("/ZaDb", "/ZapfDingbats", None)):   # ZapfDingbats draws tick marks
        if key in fonts:
            continue
        font = DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject(base_font),
        })
        if encoding:
            font[NameObject("/Encoding")] = NameObject(encoding)
        fonts[NameObject(key)] = writer._add_object(font)
    acroform.setdefault(NameObject("/DA"), TextStringObject("/Helv 0 Tf 0 g"))
    with open(path, "wb") as handle:
        writer.write(handle)
