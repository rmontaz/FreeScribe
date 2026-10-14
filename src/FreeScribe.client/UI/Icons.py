"""
Icons.py

A small set of line icons drawn at runtime with Pillow.

Drawing the icons in code keeps them crisp at any display scale, lets them be
tinted to any theme colour, and avoids shipping (and packaging) image files.
Each icon is described on a 24x24 grid, rendered several times larger and then
reduced, which gives smooth edges without needing a vector renderer.

    from UI.Icons import icon
    label.configure(image=icon("mic", 16, Palette.TEXT))

This software is released under the AGPL-3.0 license.
"""

import math

from PIL import Image, ImageDraw, ImageTk

from UI.Theme import Theme, Palette

_GRID = 24
_SUPERSAMPLE = 8
_STROKE = 1.8

_photo_cache = {}
_mask_cache = {}


class _Pen:
    """Draws strokes and shapes onto a greyscale mask in grid units."""

    def __init__(self, stroke=_STROKE):
        self.k = _SUPERSAMPLE
        size = _GRID * self.k
        self.image = Image.new("L", (size, size), 0)
        self.draw = ImageDraw.Draw(self.image)
        self.stroke = stroke

    def _w(self):
        return max(1, int(round(self.stroke * self.k)))

    def dot(self, x, y, r):
        k = self.k
        self.draw.ellipse([(x - r) * k, (y - r) * k, (x + r) * k, (y + r) * k], fill=255)

    def line(self, *points, closed=False):
        pts = list(points)
        if closed:
            pts.append(pts[0])
        k = self.k
        scaled = [(x * k, y * k) for x, y in pts]
        self.draw.line(scaled, fill=255, width=self._w(), joint="curve")
        radius = self.stroke / 2
        for x, y in pts:
            self.dot(x, y, radius)

    def circle(self, cx, cy, r, fill=False):
        k, half = self.k, self.stroke / 2
        if fill:
            self.dot(cx, cy, r)
        else:
            self.draw.ellipse([(cx - r - half) * k, (cy - r - half) * k, (cx + r + half) * k, (cy + r + half) * k],
                              outline=255, width=self._w())

    def ellipse(self, cx, cy, rx, ry):
        k, half = self.k, self.stroke / 2
        self.draw.ellipse([(cx - rx - half) * k, (cy - ry - half) * k, (cx + rx + half) * k, (cy + ry + half) * k],
                          outline=255, width=self._w())

    def arc(self, cx, cy, r, start, end):
        """Arc in degrees, clockwise from three o'clock (Pillow's convention)."""
        k, half = self.k, self.stroke / 2
        self.draw.arc([(cx - r - half) * k, (cy - r - half) * k, (cx + r + half) * k, (cy + r + half) * k],
                      start=start, end=end, fill=255, width=self._w())
        for angle in (start, end):
            rad = math.radians(angle)
            self.dot(cx + r * math.cos(rad), cy + r * math.sin(rad), half)

    def rrect(self, x0, y0, x1, y1, r, fill=False):
        k, half = self.k, self.stroke / 2
        if fill:
            self.draw.rounded_rectangle([x0 * k, y0 * k, x1 * k, y1 * k], radius=r * k, fill=255)
        else:
            self.draw.rounded_rectangle([(x0 - half) * k, (y0 - half) * k, (x1 + half) * k, (y1 + half) * k],
                                        radius=(r + half) * k, outline=255, width=self._w())

    def polygon(self, *points):
        k = self.k
        self.draw.polygon([(x * k, y * k) for x, y in points], fill=255)

    def erase(self, x0, y0, x1, y1, r=0):
        k = self.k
        self.draw.rounded_rectangle([x0 * k, y0 * k, x1 * k, y1 * k], radius=r * k, fill=0)


# ------------------------------------------------------------------ icon set
def _mic(p):
    p.rrect(9, 3, 15, 13.5, 3)
    p.arc(12, 11, 7, 0, 180)
    p.line((12, 18), (12, 21))
    p.line((8.5, 21), (15.5, 21))


def _stop(p):
    p.rrect(6.5, 6.5, 17.5, 17.5, 2.5, fill=True)


def _pause(p):
    p.rrect(6.5, 5, 10.5, 19, 1.3, fill=True)
    p.rrect(13.5, 5, 17.5, 19, 1.3, fill=True)


def _play(p):
    p.polygon((8, 5.2), (18.5, 12), (8, 18.8))
    p.line((8, 5.2), (18.5, 12), (8, 18.8), closed=True)


def _record(p):
    p.circle(12, 12, 6, fill=True)


def _plus(p):
    p.line((12, 5), (12, 19))
    p.line((5, 12), (19, 12))


def _search(p):
    p.circle(11, 11, 6.2)
    p.line((16, 16), (20, 20))


def _settings(p):
    p.line((4, 7), (12.2, 7)); p.circle(15, 7, 2.2); p.line((17.8, 7), (20, 7))
    p.line((4, 12), (6.2, 12)); p.circle(9, 12, 2.2); p.line((11.8, 12), (20, 12))
    p.line((4, 17), (11.2, 17)); p.circle(14, 17, 2.2); p.line((16.8, 17), (20, 17))


def _help(p):
    p.circle(12, 12, 8.8)
    p.arc(12, 10, 2.7, 185, 50)
    p.line((13.7, 12.1), (12, 13.6))
    p.dot(12, 16.7, 1.1)


def _info(p):
    p.circle(12, 12, 8.8)
    p.line((12, 11.2), (12, 16.4))
    p.dot(12, 7.9, 1.1)


def _copy(p):
    p.rrect(4, 4, 14.5, 14.5, 2.4)
    p.erase(7.4, 7.4, 22, 22, 2.6)
    p.rrect(9.5, 9.5, 20, 20, 2.4)


def _check(p):
    p.line((5, 12.5), (10, 17.5), (19, 7))


def _close(p):
    p.line((6.5, 6.5), (17.5, 17.5))
    p.line((17.5, 6.5), (6.5, 17.5))


def _chevron_down(p):
    p.line((6.5, 9.5), (12, 15), (17.5, 9.5))


def _chevron_up(p):
    p.line((6.5, 14.5), (12, 9), (17.5, 14.5))


def _chevron_right(p):
    p.line((9.5, 6.5), (15, 12), (9.5, 17.5))


def _chevron_left(p):
    p.line((14.5, 6.5), (9, 12), (14.5, 17.5))


def _trash(p):
    p.line((4, 7), (20, 7))
    p.line((9, 7), (9, 4.3), (15, 4.3), (15, 7))
    p.line((6, 7), (7, 20), (17, 20), (18, 7))
    p.line((10, 11), (10, 16))
    p.line((14, 11), (14, 16))


def _tray(p):
    p.line((4, 15), (4, 19.5), (20, 19.5), (20, 15))


def _upload(p):
    _tray(p)
    p.line((12, 15), (12, 4.2))
    p.line((7.5, 8.7), (12, 4.2), (16.5, 8.7))


def _download(p):
    _tray(p)
    p.line((12, 4), (12, 14.8))
    p.line((7.5, 10.3), (12, 14.8), (16.5, 10.3))


def _edit(p):
    p.line((4, 20), (4.6, 16), (15.6, 5), (19, 8.4), (8, 19.4), closed=True)
    p.line((13.4, 7.2), (16.8, 10.6))


def _doc(p):
    p.line((6, 3), (14, 3), (19, 8), (19, 21), (6, 21), closed=True)
    p.line((14, 3), (14, 8), (19, 8))
    p.line((9.3, 13), (15.7, 13))
    p.line((9.3, 17), (15.7, 17))


def _sparkle(p):
    def star(cx, cy, r, waist):
        pts = []
        for i in range(8):
            angle = math.radians(-90 + i * 45)
            radius = r if i % 2 == 0 else waist
            pts.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
        p.polygon(*pts)
    star(10.5, 13, 8.5, 2.4)
    star(18.8, 5.4, 3.6, 1.0)


def _send(p):
    p.line((12, 19), (12, 5.2))
    p.line((6, 11.2), (12, 5.2), (18, 11.2))


def _dots(p):
    for x in (5.5, 12, 18.5):
        p.dot(x, 12, 1.7)


def _panel(p):
    p.rrect(3.5, 4.5, 20.5, 19.5, 3)
    p.line((9.5, 4.5), (9.5, 19.5))


def _compact(p):
    p.rrect(3, 5, 21, 19, 2.6)
    p.rrect(12, 11.5, 18, 16, 1.2, fill=True)


def _expand(p):
    p.line((14, 4), (20, 4), (20, 10))
    p.line((20, 4), (13.5, 10.5))
    p.line((10, 20), (4, 20), (4, 14))
    p.line((4, 20), (10.5, 13.5))


def _clock(p):
    p.circle(12, 12, 8.8)
    p.line((12, 7.2), (12, 12), (15.3, 14))


def _calendar(p):
    p.rrect(4, 5.5, 20, 20, 2.6)
    p.line((4, 10.3), (20, 10.3))
    p.line((8.3, 3.3), (8.3, 7))
    p.line((15.7, 3.3), (15.7, 7))


def _globe(p):
    p.circle(12, 12, 8.8)
    p.line((3.2, 12), (20.8, 12))
    p.ellipse(12, 12, 3.9, 8.8)


def _user(p):
    p.circle(12, 8.2, 3.9)
    p.arc(12, 21, 7.4, 190, 350)


def _refresh(p):
    p.arc(12, 12, 8, 40, 320)
    p.line((18.6, 2.8), (18.6, 7.2), (14.2, 7.2))


def _lines(p):
    p.line((4, 7), (20, 7))
    p.line((4, 12), (20, 12))
    p.line((4, 17), (14, 17))


def _clipboard(p):
    p.rrect(5, 5.5, 19, 21, 2.6)
    p.erase(8.2, 2, 15.8, 8.3, 1.5)
    p.rrect(9.2, 3.3, 14.8, 7, 1.4)
    p.line((9, 12.5), (15, 12.5))
    p.line((9, 16.5), (13, 16.5))


def _lock(p):
    p.rrect(5, 11, 19, 20.5, 2.6)
    p.arc(12, 8, 4, 180, 360)
    p.line((8, 8), (8, 11))
    p.line((16, 8), (16, 11))


def _warning(p):
    p.line((12, 3.8), (21.3, 19.8), (2.7, 19.8), closed=True)
    p.line((12, 10), (12, 14.3))
    p.dot(12, 17.1, 1.1)


def _folder(p):
    p.line((3.5, 6), (9.5, 6), (11.5, 8.5), (20.5, 8.5), (20.5, 19), (3.5, 19), closed=True)


def _undo(p):
    p.line((8.5, 5), (4, 9.5), (8.5, 14))
    p.line((4, 9.5), (14.5, 9.5))
    p.arc(14.5, 14.5, 5, 270, 90)
    p.line((14.5, 19.5), (11, 19.5))


def _chat(p):
    p.line((7.5, 16.5), (6, 20.5), (11.5, 16.5))
    p.rrect(3.5, 4, 20.5, 16.5, 4)


def _form(p):
    p.line((6, 3), (14, 3), (19, 8), (19, 21), (6, 21), closed=True)
    p.line((14, 3), (14, 8), (19, 8))
    p.line((9, 14.5), (11.4, 16.9), (15.8, 12.4))


def _mapper(p):
    p.rrect(4, 4, 20, 20, 2.5)
    p.line((8, 9), (16, 9))
    p.line((8, 13), (13.5, 13))
    p.line((8, 17), (11, 17))


_ICONS = {
    "chat": _chat, "form": _form, "mapper": _mapper,
    "mic": _mic, "stop": _stop, "pause": _pause, "play": _play, "record": _record,
    "plus": _plus, "search": _search, "settings": _settings, "help": _help, "info": _info,
    "copy": _copy, "check": _check, "close": _close,
    "chevron-down": _chevron_down, "chevron-up": _chevron_up,
    "chevron-right": _chevron_right, "chevron-left": _chevron_left,
    "trash": _trash, "upload": _upload, "download": _download, "edit": _edit, "doc": _doc,
    "sparkle": _sparkle, "send": _send, "dots": _dots, "panel": _panel, "compact": _compact,
    "expand": _expand, "clock": _clock, "calendar": _calendar, "globe": _globe, "user": _user,
    "refresh": _refresh, "lines": _lines, "clipboard": _clipboard, "lock": _lock,
    "warning": _warning, "folder": _folder, "undo": _undo,
}


def available():
    """Names of all icons in the set."""
    return sorted(_ICONS)


def _hex_to_rgb(color):
    color = color.lstrip("#")
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))


def icon_image(name, size_px, color=Palette.TEXT) -> Image.Image:
    """Render an icon as an RGBA Pillow image ``size_px`` pixels square."""
    key = (name, size_px)
    mask = _mask_cache.get(key)
    if mask is None:
        painter = _ICONS.get(name)
        if painter is None:
            raise KeyError(f"Unknown icon: {name}")
        # Keep small icons legible: the stroke is thickened slightly below 16 px.
        stroke = _STROKE * (1.12 if size_px < 16 else 1.0)
        pen = _Pen(stroke)
        painter(pen)
        mask = pen.image.resize((size_px, size_px), Image.LANCZOS)
        _mask_cache[key] = mask
    image = Image.new("RGBA", (size_px, size_px), _hex_to_rgb(color) + (0,))
    image.putalpha(mask)
    return image


def icon(name, size=16, color=Palette.TEXT) -> ImageTk.PhotoImage:
    """Return a cached Tk image for ``name`` at ``size`` design pixels."""
    size_px = max(8, Theme.px(size))
    key = (name, size_px, color)
    photo = _photo_cache.get(key)
    if photo is None:
        photo = ImageTk.PhotoImage(icon_image(name, size_px, color))
        _photo_cache[key] = photo
    return photo


def logo(size=24):
    """The application logo scaled to ``size`` design pixels, or ``None``."""
    size_px = Theme.px(size)
    key = ("__logo__", size_px, "")
    photo = _photo_cache.get(key)
    if photo is None:
        try:
            from utils.file_utils import get_file_path
            image = Image.open(get_file_path("assets", "logo.png")).convert("RGBA")
            image = image.resize((size_px, size_px), Image.LANCZOS)
            photo = ImageTk.PhotoImage(image)
        except Exception:
            return None
        _photo_cache[key] = photo
    return photo
