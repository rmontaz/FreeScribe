"""
scan_form.py

Reading forms that are only pictures: scanned or faxed PDFs, and photos or
images of a page (PNG, JPEG).

A picture has no text and no drawn lines to read, so for each page:

1. the page is straightened (a scan is rarely exactly level),
2. lines are found from the dark pixels; a line must be darker than the paper
   just either side of it, so grey shading and fax speckle are not lines,
3. the long lines are cleared, speckle and shading are cleaned away, and the
   words are read with OCR (Tesseract, built into PyMuPDF; the English data file
   is in assets/ocr); label words misread by a letter or two are corrected to
   common form words ("Patent name" -> "Patient name"),
4. boxes, tick boxes and table cells are the places closed by lines on all four
   sides; strokes inside words are letters, not boxes ("D" looks like a box),
   and solid squares the size of a tick box count as tick boxes,
5. the words and lines go through the same field finder as computer-made PDFs
   (:mod:`services.flat_form`), so every field gets a label, a name and a tag,
6. the fields are turned back to match the page as scanned.

Everything runs on this computer; nothing is sent anywhere.

This software is released under the AGPL-3.0 license.
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from typing import List, Sequence, Tuple

import numpy as np
import pymupdf

from services import flat_form

DPI = 300                     # pixels per inch the page is read at
PT = DPI / 72.0               # pixels per point
MAX_TILT = 3.0                # degrees; more than this is left as it is
MIN_SEGMENT = 19              # pixels; shortest stroke kept as a line piece (a 5 pt tick box side is 21)
MAX_THICKNESS = 9             # pixels; thicker strokes are filled areas, not lines
LONG_HLINE, LONG_VLINE = 30, 20   # points; lines this long are cleared before OCR (letters are shorter)
INK = 0.62                    # darker than this share of the local paper brightness counts as ink
LINE_INK, LINE_SMOOTH = 0.82, 7   # the same for lines, after averaging 7 pixels along the line
GAP = 2                       # pixels; breaks this short in a line are bridged
LINE_REACH = 7                # pixels either side of a line that must be lighter than it
LINE_CONTRAST = 0.85          # a line pixel is this much darker than both sides of it
IMAGE_PAGE_WIDTH = 612.0      # points; an image is read as a Letter-width page


def tessdata_dir() -> str:
    """The folder with Tesseract's English data (assets/ocr)."""
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "ocr")


def available() -> bool:
    """True when the OCR language data is installed."""
    return os.path.isfile(os.path.join(tessdata_dir(), "eng.traineddata"))


@dataclass
class ScannedPage:
    """What was read from one page, in page points of the straightened page."""

    words: List[flat_form._Word]
    hlines: List[Tuple[float, float, float]]
    vlines: List[Tuple[float, float, float]]
    rects: List[Tuple[float, float, float, float]]
    tilt: float                      # degrees the page was turned to straighten it
    size: Tuple[float, float]        # page width and height in points


# ================================================================ pages from images
def image_document(images: Sequence[bytes]):
    """A PDF with one page per image, each page as wide as a Letter page."""
    doc = pymupdf.open()
    for data in images:
        pix = pymupdf.Pixmap(data)
        height = IMAGE_PAGE_WIDTH * pix.height / max(1, pix.width)
        page = doc.new_page(width=IMAGE_PAGE_WIDTH, height=height)
        page.insert_image(page.rect, stream=data)
    return doc


# ================================================================== reading a page
def read_page(page, language: str = "eng") -> ScannedPage:
    gray = _render(page)
    tilt = _tilt(_ink(gray))
    if tilt:
        gray = _rotate(gray, tilt)
    paper = _paper(gray)
    h_ink, v_ink = _line_ink(gray, paper, axis=1), _line_ink(gray, paper, axis=0)
    h_segments = _segments(h_ink, MAX_THICKNESS)
    v_segments = [(x, y0, y1) for y0, x, y1 in _segments(v_ink.T, MAX_THICKNESS)]
    hlines = [(x0 / PT, y / PT, x1 / PT) for x0, y, x1 in h_segments]
    vlines = [(x / PT, y0 / PT, y1 / PT) for x, y0, y1 in v_segments]
    hlines = _join_pieces(hlines, 2.0, reach=1.5)

    # Clear the long lines so OCR does not read them as letters (letter strokes are shorter;
    # clearing those would cut letters out of the labels)
    cleared = gray.copy()
    for x0, y, x1 in h_segments:
        if (x1 - x0) / PT >= LONG_HLINE:
            cleared[max(0, int(y) - 3):int(y) + 4, max(0, int(x0) - 1):int(x1) + 2] = 255
    for x, y0, y1 in v_segments:
        if (y1 - y0) / PT >= LONG_VLINE:
            cleared[max(0, int(y0) - 1):int(y1) + 2, max(0, int(x) - 3):int(x) + 4] = 255
    words = [_respell(w) for w in _ocr(_for_ocr(cleared), language)]

    # Strokes inside words are letters, not lines ("D", or "C" beside the stem of an "l",
    # can look like a small box)
    letters = [(w.x0, w.y0 - 1, w.x1, w.y1 + 1) for w in words if _is_text(w.text)]
    hlines = [l for l in hlines if not any(_inside((l[0], l[1], l[2], l[1]), b) for b in letters)]
    vlines = [l for l in vlines if not any(_inside((l[0], l[1], l[0], l[2]), b) for b in letters)]
    # A real box has all four sides drawn the whole way
    rects = grid_cells(hlines, vlines, accept=lambda r: _closed(r, h_ink, v_ink))
    rects = [r for r in rects if not any(_inside(r, b) for b in letters)]

    # Filled-in squares are tick boxes too (already ticked, or printed solid)
    rects += [r for r in _solid_squares(gray, letters) if not any(_inside(r, o, 2.0) or _inside(o, r, 2.0) for o in rects)]

    # A tick box read as a letter ("O", "L]") is not a word
    small = [r for r in rects if r[2] - r[0] <= flat_form.SQUARE_MAX + 2 and r[3] - r[1] <= flat_form.SQUARE_MAX + 2]
    words = [w for w in words if not ((len(w.text) <= 2 or not _is_text(w.text)) and
                                      any(flat_form._rects_overlap((w.x0, w.y0, w.x1, w.y1), r) for r in small))]
    # The top and bottom of a row of boxes are drawn as one long line; it is not a writing line
    hlines = [l for l in hlines if _edge_share(l, rects) < 0.8]
    height, width = gray.shape
    return ScannedPage(words, hlines, vlines, rects, tilt, (width / PT, height / PT))


def _render(page) -> np.ndarray:
    pix = page.get_pixmap(dpi=DPI, colorspace=pymupdf.csGRAY, alpha=False)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width).copy()


def _box_mean(image: np.ndarray, size: int) -> np.ndarray:
    """The mean of a ``size`` x ``size`` square around every pixel."""
    pad = size // 2
    padded = np.pad(image.astype(np.float32), pad, mode="edge")
    total = padded.cumsum(0).cumsum(1)
    total = np.pad(total, ((1, 0), (1, 0)))
    h, w = image.shape
    return (total[size:size + h, size:size + w] - total[:h, size:size + w]
            - total[size:size + h, :w] + total[:h, :w]) / (size * size)


def _paper(gray: np.ndarray) -> np.ndarray:
    """The brightness of the paper around every pixel (copes with grey or uneven paper)."""
    paper = np.maximum(_box_mean(gray, 61), 1.0)
    return np.maximum(paper, np.percentile(gray, 90) * 0.8)     # inside large dark areas


def _ink(gray: np.ndarray) -> np.ndarray:
    """Dark pixels."""
    return _box_mean(gray, 3) < _paper(gray) * INK


def _line_ink(gray: np.ndarray, paper: np.ndarray, axis: int) -> np.ndarray:
    """
    Dark pixels for finding lines along ``axis`` (1: horizontal): the picture is
    averaged along the line's direction first, which keeps faint or broken lines
    (a fax) and drops speckle. Letters smear into thick bands that are not lines.
    """
    size = LINE_SMOOTH
    image = gray.astype(np.float32)
    total = np.cumsum(np.pad(image, [(0, 0), (size // 2 + 1, size // 2)] if axis == 1
                             else [(size // 2 + 1, size // 2), (0, 0)], mode="edge"), axis=axis)
    if axis == 1:
        mean = (total[:, size:] - total[:, :-size]) / size
    else:
        mean = (total[size:, :] - total[:-size, :]) / size
    # A line is darker than what lies just either side of it. Shading (a grey answer
    # box) and its speckle are about as dark as their surroundings, so they are not lines.
    reach = LINE_REACH
    across = 0 if axis == 1 else 1
    padded = np.pad(mean, [(reach, reach), (0, 0)] if across == 0 else [(0, 0), (reach, reach)], mode="edge")
    if across == 0:
        before, after = padded[:-2 * reach], padded[2 * reach:]
    else:
        before, after = padded[:, :-2 * reach], padded[:, 2 * reach:]
    contrast = mean < np.minimum(before, after) * LINE_CONTRAST
    # Inside a thick stroke the pixels either side are dark too; keep those that touch an edge
    contrast |= (mean < np.maximum(before, after) * LINE_CONTRAST) & (mean < paper * 0.55)
    return (mean < paper * LINE_INK) & contrast


def _tilt(ink: np.ndarray) -> float:
    """The angle (degrees) that makes the page's text rows and lines level."""
    ys, xs = np.nonzero(ink[::2, ::2])
    if len(xs) < 500:
        return 0.0
    if len(xs) > 300_000:
        keep = np.random.default_rng(0).choice(len(xs), 300_000, replace=False)
        xs, ys = xs[keep], ys[keep]
    xs = xs.astype(np.float32)
    ys = ys.astype(np.float32)

    def sharpness(angle):
        rows = np.round(ys - xs * math.tan(math.radians(angle))).astype(np.int64)
        rows -= rows.min()
        counts = np.bincount(rows)
        return float((counts.astype(np.float64) ** 2).sum())

    best = max(np.arange(-MAX_TILT, MAX_TILT + 0.001, 0.25), key=sharpness)
    best = max(np.arange(best - 0.25, best + 0.251, 0.025), key=sharpness)
    return 0.0 if abs(best) < 0.05 else round(float(best), 3)


def _rotate(gray: np.ndarray, angle: float) -> np.ndarray:
    from PIL import Image
    image = Image.fromarray(gray)
    return np.asarray(image.rotate(angle, resample=Image.BICUBIC, fillcolor=255))


def _segments(ink: np.ndarray, max_thickness: int):
    """Horizontal strokes: (x0, y, x1) in pixels, at least MIN_SEGMENT long and at most ``max_thickness`` thick."""
    # Bridge one- and two-pixel breaks (fax dropouts, a faint line)
    closed = ink.copy()
    for gap in range(1, GAP + 1):
        closed[:, gap:-gap] |= ink[:, :-2 * gap] & ink[:, 2 * gap:]
    padded = np.zeros((closed.shape[0], closed.shape[1] + 2), dtype=np.int8)
    padded[:, 1:-1] = closed
    change = np.diff(padded, axis=1)
    start_rows, starts = np.nonzero(change == 1)
    _end_rows, ends = np.nonzero(change == -1)
    long = (ends - starts) >= MIN_SEGMENT
    start_rows, starts, ends = start_rows[long], starts[long], ends[long]

    segments = []          # [first row, last row, x0, x1]
    active = []
    current_row = -1
    for row, x0, x1 in zip(start_rows.tolist(), starts.tolist(), ends.tolist()):
        if row != current_row:
            active = [s for s in active if s[1] >= row - 2]
            current_row = row
        for segment in active:
            overlap = min(x1, segment[3]) - max(x0, segment[2])
            if segment[1] < row and overlap >= 0.7 * min(x1 - x0, segment[3] - segment[2]):
                segment[1] = row
                segment[2], segment[3] = min(segment[2], x0), max(segment[3], x1)
                break
        else:
            segment = [row, row, x0, x1]
            segments.append(segment)
            active.append(segment)
    return [(x0, (first + last) / 2.0, x1) for first, last, x0, x1 in segments
            if last - first + 1 <= max_thickness]


def grid_cells(hlines, vlines, tol: float = 3.0, accept=None):
    """
    Boxes closed by lines on all four sides: single boxes, tick boxes and the
    cells of ruled tables (points). ``accept`` checks a candidate box against the
    picture; under each top edge the nearest bottom edge that makes accepted boxes wins
    (a speck inside a box must not stand in for its bottom edge).
    """
    cells = []
    # A faint or broken edge comes in pieces; join pieces of one line (the caller checks
    # that a box is inked the whole way round)
    hlines = sorted(_join_pieces(hlines, tol), key=lambda l: (l[1], l[0]))
    vlines = [(x, y0, y1) for y0, x, y1 in _join_pieces([(y0, x, y1) for x, y0, y1 in vlines], tol)]
    for i, (ax0, ay, ax1) in enumerate(hlines):
        for bx0, by, bx1 in hlines[i + 1:]:
            if by - ay < flat_form.SQUARE_MIN - 1:
                continue
            left, right = max(ax0, bx0), min(ax1, bx1)
            if right - left < flat_form.SQUARE_MIN - 1:
                continue
            sides = sorted({round(x, 1) for x, y0, y1 in vlines
                            if y0 <= ay + tol and y1 >= by - tol and left - tol <= x <= right + tol})
            columns = []
            for x in sides:
                if not columns or x - columns[-1] > tol:
                    columns.append(x)
            if len(columns) < 2:
                continue
            found = [(x0, ay, x1, by) for x0, x1 in zip(columns, columns[1:]) if x1 - x0 >= flat_form.SQUARE_MIN - 1]
            if accept is not None:
                found = [cell for cell in found if accept(cell)]
            if found:
                cells += found
                break
    return cells


def _solid_squares(gray: np.ndarray, letters) -> List[Tuple[float, float, float, float]]:
    """Solid dark squares the size of a tick box (points), away from words."""
    from scipy import ndimage
    solid = ndimage.median_filter(gray, size=3) < np.percentile(gray, 90) * 0.5
    labels, _count = ndimage.label(solid)
    squares = []
    low, high = (flat_form.SQUARE_MIN + 1) * PT, flat_form.SQUARE_MAX * PT
    for index, region in enumerate(ndimage.find_objects(labels), start=1):
        if region is None:
            continue
        h, w = region[0].stop - region[0].start, region[1].stop - region[1].start
        if not (low <= w <= high and low <= h <= high and abs(w - h) <= 0.2 * max(w, h)):
            continue
        if (labels[region] == index).mean() < 0.85:
            continue
        rect = (region[1].start / PT, region[0].start / PT, region[1].stop / PT, region[0].stop / PT)
        if any(_inside(rect, b, 2.0) for b in letters):
            continue
        squares.append(rect)
    return squares


def _join_pieces(lines, tol: float, reach: float = 40.0):
    """(a0, b, a1) pieces at the same height joined across gaps of up to ``reach`` points."""
    joined = []
    for a0, b, a1 in sorted(lines, key=lambda l: (round(l[1] / tol), l[0])):
        for i, (c0, d, c1) in enumerate(joined):
            if abs(d - b) <= tol / 2 and a0 - c1 <= reach and c0 - a1 <= reach:
                joined[i] = (min(a0, c0), (d + b) / 2, max(a1, c1))
                break
        else:
            joined.append((a0, b, a1))
    return joined


def _closed(rect, h_ink, v_ink, coverage: float = 0.85) -> bool:
    """True when the four sides of ``rect`` (points) are inked along nearly their whole length."""
    x0, y0, x1, y1 = (int(round(v * PT)) for v in rect)
    height, width = h_ink.shape
    if x1 - x0 < 6 or y1 - y0 < 6 or x0 < 0 or y0 < 0 or x1 >= width or y1 >= height:
        return False
    for y in (y0, y1):
        if h_ink[max(0, y - 3):y + 4, x0 + 3:x1 - 2].any(axis=0).mean() < coverage:
            return False
    for x in (x0, x1):
        if v_ink[y0 + 3:y1 - 2, max(0, x - 3):x + 4].any(axis=1).mean() < coverage:
            return False
    return True


def _edge_share(line, rects, tol: float = 2.5) -> float:
    """How much of a horizontal line runs along the top or bottom of boxes (0-1)."""
    x0, y, x1 = line
    spans = sorted((max(x0, r[0]), min(x1, r[2])) for r in rects
                   if (abs(y - r[1]) <= tol or abs(y - r[3]) <= tol) and min(x1, r[2]) > max(x0, r[0]))
    covered, end = 0.0, x0
    for a, b in spans:
        a = max(a, end)
        if b > a:
            covered += b - a
            end = b
    return covered / max(1e-6, x1 - x0)


def _on_rect_edge(line, rects, horizontal: bool, tol: float = 2.0) -> bool:
    if horizontal:
        x0, y, x1 = line
        return any((abs(y - r[1]) <= tol or abs(y - r[3]) <= tol) and x0 >= r[0] - tol - 3 and x1 <= r[2] + tol + 3
                   for r in rects)
    x, y0, y1 = line
    return any((abs(x - r[0]) <= tol or abs(x - r[2]) <= tol) and y0 >= r[1] - tol - 3 and y1 <= r[3] + tol + 3
               for r in rects)


_BOX_READING = re.compile(r"[LJCOoDUIlT0\[\]|()©®@{}]{1,3}")


_FORM_WORDS = None


def form_words() -> set:
    """Words common in form labels (assets/ocr/form_words.txt)."""
    global _FORM_WORDS
    if _FORM_WORDS is None:
        try:
            with open(os.path.join(tessdata_dir(), "form_words.txt"), encoding="utf-8") as handle:
                _FORM_WORDS = {line.strip().lower() for line in handle if line.strip() and not line.startswith("#")}
        except OSError:
            _FORM_WORDS = set()
    return _FORM_WORDS


def respell(word: str) -> str:
    """
    ``word`` corrected to a common form word when OCR misread a letter or two
    ("Patent" -> "Patient", "Chnician" -> "Clinician"); otherwise unchanged.
    Short words and words that may be names are left alone.
    """
    import difflib
    match = re.fullmatch(r"([^A-Za-z]*)([A-Za-z]{4,})([^A-Za-z]*)", word)
    vocabulary = form_words()
    if not match or not vocabulary:
        return word
    lead, core, tail = match.groups()
    lower = core.lower()
    if lower in vocabulary or (lower.endswith("s") and lower[:-1] in vocabulary):
        return word
    cutoff = 0.8 if len(lower) <= 6 else 0.75
    options = difflib.get_close_matches(lower, vocabulary, n=2, cutoff=cutoff)
    if not options:
        return word
    if options[0] in (lower + "s", lower + "es") or lower in (options[0] + "s", options[0] + "es"):
        return word                 # only singular and plural differ ("week", "weeks")
    ratios = [difflib.SequenceMatcher(None, lower, option).ratio() for option in options]
    if len(options) > 1 and ratios[1] >= ratios[0] - 0.02:
        return word                 # two words fit about as well; do not guess
    fixed = options[0]
    if core.isupper():
        fixed = fixed.upper()
    elif core[0].isupper():
        fixed = fixed.capitalize()
    return lead + fixed + tail


def _respell(word: flat_form._Word) -> flat_form._Word:
    text = " ".join(respell(part) for part in word.text.split(" "))
    return word if text == word.text else flat_form._Word(word.x0, word.y0, word.x1, word.y1, text)


def _is_text(text: str) -> bool:
    """
    A word of real text: two or more letters. OCR reads an empty tick box as a
    short run of box-like letters ("CJ", "LJ", "CL]", "O"); those are not text.
    """
    text = text.strip()
    if _BOX_READING.fullmatch(text):
        return False
    return sum(ch.isalpha() for ch in text) >= 2


def _inside(inner, outer, margin: float = 1.0) -> bool:
    return flat_form._inside(inner, outer, margin)


_UNDERSCORE_RUN = re.compile(r"[_—–\-]{2,}")


OCR_INK = 0.6                 # darker than this share of the paper is ink, for OCR


def _for_ocr(gray: np.ndarray) -> np.ndarray:
    """
    A clean black-and-white copy of the page for OCR: speckle (scanner and fax noise)
    is smoothed away and shaded areas (grey answer boxes) turn white, so OCR does not
    read the texture as words.
    """
    from scipy import ndimage
    smooth = ndimage.median_filter(gray, size=3)
    paper = float(np.percentile(smooth, 90))
    return np.where(smooth < paper * OCR_INK, 0, 255).astype(np.uint8)


def _ocr(gray: np.ndarray, language: str) -> List[flat_form._Word]:
    """The words on the page (page points), cleaned of OCR noise."""
    height, width = gray.shape
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.Pixmap(pymupdf.csGRAY, width, height, gray.tobytes(), False))
    pix.set_dpi(DPI, DPI)          # (OCR reads nothing from a grey pixmap)
    pdf = pymupdf.open("pdf", pix.pdfocr_tobytes(language=language, tessdata=tessdata_dir()))
    page = pdf[0]
    sx, sy = (width / PT) / page.rect.width, (height / PT) / page.rect.height
    words = []
    for x0, y0, x1, y1, text, *_rest in page.get_text("words"):
        # Writing lines read as dashes or underscores are found from the pixels instead
        cleaned = _UNDERSCORE_RUN.sub(" ", text).strip()
        if not re.search(r"[A-Za-z0-9]", cleaned):
            continue
        if cleaned != text:
            share = len(text.split(cleaned[0], 1)[0]) / max(1, len(text)) if cleaned else 0
            span = (x1 - x0) * len(cleaned) / max(1, len(text))
            x0 = x0 + (x1 - x0) * share
            x1 = x0 + span
        words.append(flat_form._Word(x0 * sx, y0 * sy, x1 * sx, y1 * sy, cleaned))
    return words


# ============================================================ back to the scan
def unrotate(rect, tilt: float, size) -> Tuple[float, float, float, float]:
    """A rectangle of the straightened page placed back on the page as scanned (same size, moved centre)."""
    if not tilt:
        return rect
    cx, cy = size[0] / 2, size[1] / 2
    mx, my = (rect[0] + rect[2]) / 2 - cx, (rect[1] + rect[3]) / 2 - cy
    # The page was turned by ``tilt`` degrees (anticlockwise as seen); turn the centre back
    a = math.radians(-tilt)
    nx = cx + mx * math.cos(a) + my * math.sin(a)
    ny = cy - mx * math.sin(a) + my * math.cos(a)
    hw, hh = (rect[2] - rect[0]) / 2, (rect[3] - rect[1]) / 2
    return (nx - hw, ny - hh, nx + hw, ny + hh)


def page_blanks(page, page_index: int, keep_unlabelled: bool = True) -> List[flat_form.Blank]:
    """The fields of a page that is only a picture, placed on the page as scanned."""
    scanned = read_page(page)
    blanks = flat_form._page_blanks(page, page_index, keep_unlabelled,
                                    parts=(scanned.words, scanned.hlines, scanned.vlines, scanned.rects))
    sx, sy = page.rect.width / scanned.size[0], page.rect.height / scanned.size[1]
    for blank in blanks:
        x0, y0, x1, y1 = unrotate(blank.rect, scanned.tilt, scanned.size)
        blank.rect = (x0 * sx, y0 * sy, x1 * sx, y1 * sy)
    return blanks
