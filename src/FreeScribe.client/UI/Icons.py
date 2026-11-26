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
from pathlib import Path

from PIL import Image, ImageDraw, ImageTk

from UI.Theme import Theme, Palette

_GRID = 24
_SUPERSAMPLE = 8
_STROKE = 1.55

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


# One complete, shared icon collection; no fallback to the legacy shapes.
from UI.IconDesign import ICONS as _ICONS


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


def icon(name, size=16, color=None) -> ImageTk.PhotoImage:
    """Return a cached Tk image for ``name`` at ``size`` design pixels."""
    color = color or Palette.TEXT
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
            path = Path(__file__).resolve().parent.parent / "assets" / "logo.png"
            image = Image.open(path if path.exists() else get_file_path("assets", "logo.png")).convert("RGBA")
            image = image.resize((size_px, size_px), Image.LANCZOS)
            photo = ImageTk.PhotoImage(image)
        except Exception:
            return None
        _photo_cache[key] = photo
    return photo
