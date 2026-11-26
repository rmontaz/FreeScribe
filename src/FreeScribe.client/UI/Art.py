"""Local cosmic artwork; no network calls, no artwork behind active editors."""
from functools import lru_cache
from pathlib import Path
import tkinter as tk
from PIL import Image, ImageChops, ImageDraw, ImageOps, ImageTk
from UI.Theme import Theme, Palette
from UI.Icons import icon, logo


@lru_cache(maxsize=24)
def source(name):
    """Resolve alongside UI source (or frozen bundle); corrupt/missing art is optional."""
    from utils.file_utils import get_file_path
    path = Path(__file__).resolve().parent.parent / 'assets' / 'cosmic' / (name + '.png')
    if not path.is_file():
        path = Path(get_file_path('assets', 'cosmic', name + '.png'))
    try:
        with Image.open(path) as image:
            return image.convert('RGBA')
    except (OSError, ValueError):
        return None


def illustration(name, size, disabled=False):
    """Render a production illustration at its actual UI size; cached per size and per Tk root."""
    return _illustration(name, size, disabled, id(tk._default_root))


@lru_cache(maxsize=64)
def _illustration(name, size, disabled, _root):
    image = source(name)
    if image is None:
        return None
    bounds = image.getchannel('A').point(lambda a: 255 if a > 32 else 0).getbbox()
    rendered = image.crop(bounds) if bounds else image.copy()
    rendered.thumbnail((Theme.px(size), Theme.px(size)), Image.Resampling.LANCZOS)
    if disabled:
        rendered.putalpha(rendered.getchannel('A').point(lambda a: int(a * .4)))
    return ImageTk.PhotoImage(rendered)


@lru_cache(maxsize=12)
def edge_fade(width, height):
    """Feather only the upper/lower atmosphere; keep the side artwork intact."""
    from math import sin, pi
    # Broad, gently irregular vertical feathering avoids a rectangular vignette.
    # Work at a small mask resolution, then interpolate for smooth display edges.
    mw, mh = min(width, 384), min(height, 256)
    values = []
    for y in range(mh):
        v = y / max(1, mh - 1)
        for x in range(mw):
            u = x / max(1, mw - 1)
            top = .18 + .035 * sin(2 * pi * u + .4) + .015 * sin(5 * pi * u)
            bottom = .20 + .04 * sin(2 * pi * u + 1.7) + .015 * sin(4 * pi * u)
            t = max(0.0, min(1.0, v / top, (1 - v) / bottom))
            values.append(round(255 * t * t * (3 - 2 * t)))
    mask = Image.new('L', (mw, mh))
    mask.putdata(values)
    return mask.resize((width, height), Image.Resampling.BICUBIC)


class Artwork(tk.Canvas):
    """Responsive decorative artwork with one retained PhotoImage per widget."""
    def __init__(self, parent, name, height=150, themed=False, cover=False, fade=False, **kwargs):
        self.asset_name, self.themed, self.cover = name, themed, cover
        self.fade = fade
        self._pending = None
        super().__init__(parent, bg=parent.cget('bg'), height=Theme.px(height),
                         highlightthickness=0, bd=0, takefocus=0, **kwargs)
        self.bind('<Configure>', self._schedule)
        self.bind('<Destroy>', self._cancel, add='+')

    def _cancel(self, event=None):
        if self._pending is not None:
            self.after_cancel(self._pending)
            self._pending = None

    def _schedule(self, event=None):
        self._cancel()
        self._pending = self.after(30, self.draw)

    def apply_theme(self):
        self.configure(bg=self.master.cget('bg'))
        self.draw()

    def draw(self):
        self._cancel()
        self.delete('art')
        w, h = self.winfo_width(), self.winfo_height()
        if w < 2 or h < 2:
            return
        name = self.asset_name + ('-' + Theme.mode if self.themed else '')
        image = source(name)
        key = (name, w, h, self.cover, self.fade)
        if image is not None and getattr(self, '_render_key', None) == key:
            self.create_image(w/2, h/2, image=self._photo, tags='art')
        elif image is not None:
            if self.cover:
                rendered = ImageOps.fit(image, (w, h), method=Image.Resampling.LANCZOS, centering=(.5, 1.0 if name.startswith("splash-bg") else .5))
                if self.fade:
                    rendered.putalpha(ImageChops.multiply(rendered.getchannel('A'), edge_fade(w, h)))
                else:
                    mask = Image.new('L', (w, h))
                    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w-1, h-1), radius=Theme.px(16), fill=255)
                    rendered.putalpha(ImageChops.multiply(rendered.getchannel('A'), mask))
            else:
                # Tight visible bounds prevent generated transparent margins making art tiny.
                bounds = image.getchannel('A').point(lambda a: 255 if a > 32 else 0).getbbox()
                rendered = image.crop(bounds) if bounds else image.copy()
                rendered.thumbnail((max(1,w-Theme.px(20)), max(1,h-Theme.px(12))), Image.Resampling.LANCZOS)
            self._render_key = key
            self._photo = ImageTk.PhotoImage(rendered, master=self)
            self.create_image(w/2, h/2, image=self._photo, tags='art')
        self.tag_lower('art')


class CosmicHero(Artwork):
    """Keyboard-operable record action drawn over a theme-specific quiet hero."""
    def __init__(self, parent, command, subtitle):
        self._command, self.subtitle = command, subtitle
        self.enabled, self._hover = True, False
        super().__init__(parent, 'hero-start', height=330, themed=True, cover=True, fade=True)
        self.configure(takefocus=1, cursor='hand2')
        self.bind('<Button-1>', lambda e: self.invoke() if self._hit(e.x,e.y) else None)
        self.bind('<Motion>', self._motion)
        self.bind('<Leave>', lambda e: self._set_hover(False))
        self.bind('<Key-Return>', lambda e: self.invoke())
        self.bind('<Key-space>', lambda e: self.invoke())
        self.bind('<FocusIn>', lambda e: self.draw())
        self.bind('<FocusOut>', lambda e: self.draw())

    def _hit(self, x, y):
        return abs(x-self.winfo_width()/2) < Theme.px(145) and Theme.px(8) < y < self.winfo_height()-Theme.px(8)

    def _motion(self, event):
        active = self._hit(event.x,event.y)
        self.configure(cursor='hand2' if active and self.enabled else '')
        self._set_hover(active)

    def _set_hover(self, hover):
        if hover != self._hover:
            self._hover = hover
            self.draw()

    def invoke(self):
        if self.enabled and callable(self._command):
            self._command()

    def set_enabled(self, enabled):
        self.enabled = bool(enabled)
        self.draw()

    def draw(self):
        super().draw()
        self.delete('control')
        w,h=self.winfo_width(),self.winfo_height()
        if w<2 or h<2: return
        compact = h < Theme.px(220)
        cx,cy=w/2,h*(.32 if compact else .37)
        size = min(176, max(64, int(h / Theme.scale * .53)))
        self._mic = illustration('illustrated-mic', size + (4 if self._hover and self.enabled else 0), not self.enabled)
        if self._mic:
            self.create_image(cx,cy,image=self._mic,tags='control')
        fg=Palette.TEXT if Theme.mode=='light' else '#F4F5EC'
        title_y = h * (.71 if compact else .73)
        self.create_text(cx,title_y,text='Start transcribing',fill=fg,font=Theme.font('title','semibold'),tags='control')
        self.create_text(cx,h-Theme.px(15 if compact else 25),text=self.subtitle(),fill=fg,font=Theme.font('small'),width=max(1,w-Theme.px(80)),tags='control')
        if self.focus_get() is self:
            self.create_rectangle(4,4,w-5,h-5,outline=Palette.PRIMARY if Theme.mode=='light' else Palette.MINT,width=2,dash=(4,3),tags='control')


class StartupSplash(Artwork):
    def __init__(self, root):
        self.status_title = 'Starting your workspace…'
        self.status_text = 'Preparing FreeScribe'
        self.status_note = ''
        self.cancellable = False
        self.on_cancel = None
        self._phase = 0
        self._animation = None
        super().__init__(root, 'splash-universe', height=540, themed=False, cover=True)
        self.configure(width=800)
        self.pack(fill='both', expand=True)
        self.bind('<Destroy>', self._stop_animation, add='+')
        root.update_idletasks()
        self._cancel()
        self.draw()
        self._animate()
        root.update_idletasks()

    def set_status(self, title, text, note='', cancellable=False):
        self.cancellable = cancellable
        self.status_title, self.status_text, self.status_note = title, text, note
        self.draw()

    def _stop_animation(self, event=None):
        if self._animation is not None:
            self.after_cancel(self._animation)
            self._animation = None

    def _animate(self):
        self.delete('progress')
        w, h = self.winfo_width(), self.winfo_height()
        left, right, y = w*.23, w*.77, h*.69
        self.create_rectangle(left,y,right,y+4,fill='#365661',outline='',tags='progress')
        span = (right-left)*.22
        x = left + (right-left-span)*(1-abs(self._phase/30-1))
        self.create_rectangle(x,y,x+span,y+4,fill='#71d8c1',outline='',tags='progress')
        self._phase = (self._phase+1)%60
        self._animation = self.after(40,self._animate)

    def draw(self):
        super().draw()
        self.delete('brand')
        w,h=self.winfo_width(),self.winfo_height()
        self._logo=logo(64)
        if self._logo:
            self.create_image(w/2,h*.19,image=self._logo,tags='brand')
        self.create_text(w/2,h*.31,text='FreeScribe',font=Theme.font('display','semibold'),fill='#fff4df',tags='brand')
        self.create_text(w/2,h*.42,text=self.status_title,width=w*.7,font=Theme.font('medium','semibold'),fill='#fff4df',tags='brand')
        self.create_text(w/2,h*.51,text=self.status_text,width=w*.68,justify='center',font=Theme.font('base'),fill='#d0e5e3',tags='brand')
        self.create_text(w/2,h*.61,text=self.status_note,width=w*.68,justify='center',font=Theme.font('small'),fill='#bdd0d3',tags='brand')
        if self.cancellable:
            self.create_text(w/2,h*.74,text='Cancel loading',font=Theme.font('small'),fill='#fff4df',tags=('brand','cancel'))
            self.tag_bind('cancel','<Button-1>',lambda event: self.on_cancel() if self.on_cancel else None)
        self.tag_raise('progress')
