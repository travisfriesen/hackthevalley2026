"""Home screen: the at-a-glance view older relatives live with.

A full-screen family photo slideshow with the time, the white DownLink logo
and one very large help button. Diagnostics (room map, CSI, ultrasonic) live
on the Dashboard behind the DETAILS button.

Photos are decoded and cover-cropped to the screen on a background thread
(Pi 4B: a 5 MB JPEG takes a moment), then handed to Tk as raw PPM bytes so
no ImageTk is needed. Without Pillow or without photos the screen falls back
to a plain dark background.
"""

from __future__ import annotations

import random
import threading
import time
import tkinter as tk
from pathlib import Path

from theme import FONT
from screens import Screen

try:
    from PIL import Image, ImageDraw
except ImportError:                      # slideshow is optional
    Image = None

ASSETS = Path(__file__).resolve().parent / "assets"
PHOTO_EXT = {".jpg", ".jpeg", ".png"}
NO_PHOTO_BG = "#1b2433"
BUTTON_ALPHA = 150           # translucent black behind the buttons


class Layout:
    """Everything positioned for a w x h screen; designed at 800x480 and scaled."""

    def __init__(self, w: int, h: int):
        self.w, self.h = w, h
        self.s = s = min(w / 800, h / 480)

        def px(v):
            return round(v * s)
        self.px = px
        self.help_box = (w - px(320), h - px(170), w - px(20), h - px(20))
        self.details_box = (w - px(196), px(16), w - px(16), px(96))
        self.logo_h = px(64)
        self.logo_xy = (px(22), px(16))
        self.time_xy = (px(30), h - px(85))
        self.date_xy = (px(34), h - px(25))
        self.time_pt, self.date_pt = px(84), px(24)
        self.help_pt, self.details_pt = px(26), px(16)


def _scrim(lay: Layout) -> "Image.Image":
    """Dark top and bottom fade so white text reads on any photo."""
    w, h = lay.w, lay.h
    top_len, bottom_len = lay.px(150), lay.px(230)
    scrim = Image.new("L", (1, h), 0)
    for y in range(h):
        top = max(0.0, 1 - y / top_len)
        bottom = max(0.0, (y - (h - bottom_len)) / bottom_len)
        scrim.putpixel((0, y), int(255 * 0.62 * max(top, bottom ** 0.8)))
    return scrim.resize((w, h))


def _white_logo(height: int) -> "Image.Image":
    logo = Image.open(ASSETS / "logo_full.png").convert("RGBA")
    logo = logo.resize((round(logo.width * height / logo.height), height),
                       Image.LANCZOS)
    white = Image.new("RGBA", logo.size, (255, 255, 255, 255))
    white.putalpha(logo.getchannel("A"))
    return white


def render_photo(path: Path, lay: Layout, scrim, logo) -> bytes:
    """Cover-crop `path` to the screen, darken the edges, stamp the logo; PPM bytes."""
    w, h = lay.w, lay.h
    img = Image.open(path)
    img.draft("RGB", (w * 2, h * 2))     # fast JPEG downscale on decode
    img = img.convert("RGB")
    scale = max(w / img.width, h / img.height)
    img = img.resize((round(img.width * scale), round(img.height * scale)),
                     Image.LANCZOS)
    left, top = (img.width - w) // 2, (img.height - h) // 2
    img = img.crop((left, top, left + w, top + h))
    img = Image.composite(Image.new("RGB", (w, h), (8, 12, 20)), img, scrim)
    img.paste(logo, lay.logo_xy, logo)
    img = img.convert("RGBA")
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.rounded_rectangle(lay.help_box, lay.px(22),
                           fill=(0, 0, 0, BUTTON_ALPHA),
                           outline=(255, 70, 70, 255), width=lay.px(5))
    draw.rounded_rectangle(lay.details_box, lay.px(18),
                           fill=(0, 0, 0, BUTTON_ALPHA))
    img = Image.alpha_composite(img, layer).convert("RGB")
    return b"P6 %d %d 255\n" % (w, h) + img.tobytes()


class Home(Screen):
    CLOCK_SHADOW = "#000000"

    def __init__(self, master, app):
        super().__init__(master, bg=NO_PHOTO_BG)
        self.app = app
        repo = Path(__file__).resolve().parent.parent
        album = repo / (app.settings.get("photo_dir") or "my_family_album")
        self.interval = float(app.settings.get("photo_interval_s", 20))
        self.photos = sorted(p for p in album.glob("*")
                             if p.suffix.lower() in PHOTO_EXT) if Image else []
        random.shuffle(self.photos)
        self._i = 0
        self._ready = None                 # (size, ppm bytes), rendered off-thread
        self._loading = False
        self._next_at = 0.0
        self._image = None
        self.lay = None
        self.buttons = []
        self._plain_logo = (None if Image else
                            tk.PhotoImage(file=str(ASSETS / "logo_icon.png")))

        self.canvas = tk.Canvas(self, bg=NO_PHOTO_BG, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self._resized)
        self.canvas.bind("<ButtonRelease-1>", self._tap)

    # -- layout: rebuilt whenever the canvas size changes (fullscreen, rotation) --

    def _resized(self, event) -> None:
        if event.width < 100 or event.height < 100:
            return
        if self.lay and (self.lay.w, self.lay.h) == (event.width, event.height):
            return
        lay = self.lay = Layout(event.width, event.height)
        self._scrim = _scrim(lay) if Image else None
        self._logo = _white_logo(lay.logo_h) if Image else None
        self._ready, self._next_at = None, 0.0     # re-render photos at the new size

        c = self.canvas
        c.delete("all")
        self.bg_item = c.create_image(0, 0, anchor="nw")
        if self._plain_logo:
            c.create_image(*lay.logo_xy, anchor="nw", image=self._plain_logo)
        self.time_items = [
            c.create_text(lay.time_xy[0] + dx, lay.time_xy[1] + dy, anchor="sw",
                          fill=fill, font=(FONT, lay.time_pt, "bold"))
            for dx, dy, fill in ((2, 2, self.CLOCK_SHADOW), (0, 0, "white"))]
        self.date_items = [
            c.create_text(lay.date_xy[0] + dx, lay.date_xy[1] + dy, anchor="sw",
                          fill=fill, font=(FONT, lay.date_pt))
            for dx, dy, fill in ((1, 1, self.CLOCK_SHADOW), (0, 0, "white"))]

        # big touch targets, drawn on the canvas so the photo shows through
        # (the translucent black is baked into each photo by render_photo)
        self.buttons = [
            (lay.help_box, self.app.manual_help, "I NEED HELP", lay.help_pt),
            (lay.details_box, self.app.show_dashboard, "\u2630  DETAILS",
             lay.details_pt)]
        for (x0, y0, x1, y1), _cmd, label, size in self.buttons:
            if Image is None:
                c.create_rectangle(x0, y0, x1, y1, fill="#10141c", outline="")
            c.create_text((x0 + x1) // 2, (y0 + y1) // 2, text=label,
                          fill="white", font=(FONT, size, "bold"))

    def _tap(self, event) -> None:
        for (x0, y0, x1, y1), command, _label, _size in self.buttons:
            if x0 <= event.x <= x1 and y0 <= event.y <= y1:
                command()
                return

    # -- slideshow -------------------------------------------------------------

    def _prepare_next(self, lay, scrim, logo) -> None:
        path = self.photos[self._i % len(self.photos)]
        self._i += 1
        try:
            self._ready = ((lay.w, lay.h), render_photo(path, lay, scrim, logo))
        except Exception:                # a bad file must not stop the show
            self._ready = None
        self._loading = False

    def _advance(self, now: float) -> None:
        if not self.photos or Image is None or self.lay is None:
            return
        lay = self.lay
        if self._ready is None and not self._loading:
            self._loading = True
            threading.Thread(target=self._prepare_next, daemon=True,
                             args=(lay, self._scrim, self._logo)).start()
        if self._ready is not None and now >= self._next_at:
            size, data = self._ready
            self._ready = None
            if size != (lay.w, lay.h):      # screen resized while it rendered
                return
            self._image = tk.PhotoImage(data=data, format="PPM")
            self.canvas.itemconfig(self.bg_item, image=self._image)
            self._next_at = now + self.interval

    def refresh(self, app) -> None:
        if self.lay is None:
            return
        self._advance(app.now)
        t = time.localtime()
        text = time.strftime("%I:%M", t).lstrip("0") + time.strftime(" %p", t)
        date = time.strftime("%A, %B ", t) + str(t.tm_mday)
        for item in self.time_items:
            self.canvas.itemconfig(item, text=text)
        for item in self.date_items:
            self.canvas.itemconfig(item, text=date)
