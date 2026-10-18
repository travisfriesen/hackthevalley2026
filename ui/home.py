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
    from PIL import Image, ImageDraw, ImageFilter
except ImportError:                      # slideshow is optional
    Image = None

ASSETS = Path(__file__).resolve().parent / "assets"
PHOTO_EXT = {".jpg", ".jpeg", ".png"}
W, H = 800, 480
NO_PHOTO_BG = "#1b2433"
# button boxes (x0, y0, x1, y1): drawn translucent black into the photo
HELP_BOX = (W - 320, H - 170, W - 20, H - 20)
DETAILS_BOX = (W - 196, 16, W - 16, 96)
BUTTON_ALPHA = 150


def _scrim() -> "Image.Image":
    """Dark top and bottom fade so white text reads on any photo."""
    scrim = Image.new("L", (1, H), 0)
    for y in range(H):
        top = max(0.0, 1 - y / 150)
        bottom = max(0.0, (y - (H - 230)) / 230)
        scrim.putpixel((0, y), int(255 * 0.62 * max(top, bottom ** 0.8)))
    return scrim.resize((W, H))


def _white_logo(height: int) -> "Image.Image":
    logo = Image.open(ASSETS / "logo_full.png").convert("RGBA")
    logo = logo.resize((round(logo.width * height / logo.height), height),
                       Image.LANCZOS)
    white = Image.new("RGBA", logo.size, (255, 255, 255, 255))
    white.putalpha(logo.getchannel("A"))
    return white


def render_photo(path: Path, scrim, logo) -> bytes:
    """Cover-crop `path` to the screen, darken the edges, stamp the logo; PPM bytes."""
    img = Image.open(path)
    img.draft("RGB", (W * 2, H * 2))     # fast JPEG downscale on decode
    img = img.convert("RGB")
    scale = max(W / img.width, H / img.height)
    img = img.resize((round(img.width * scale), round(img.height * scale)),
                     Image.LANCZOS)
    left, top = (img.width - W) // 2, (img.height - H) // 2
    img = img.crop((left, top, left + W, top + H))
    img = Image.composite(Image.new("RGB", (W, H), (8, 12, 20)), img, scrim)
    img.paste(logo, (22, 16), logo)
    img = img.convert("RGBA")
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.rounded_rectangle(HELP_BOX, 22, fill=(0, 0, 0, BUTTON_ALPHA),
                           outline=(255, 70, 70, 255), width=5)
    draw.rounded_rectangle(DETAILS_BOX, 18, fill=(0, 0, 0, BUTTON_ALPHA))
    img = Image.alpha_composite(img, layer).convert("RGB")
    return b"P6 %d %d 255\n" % (W, H) + img.tobytes()


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
        self._ready: bytes | None = None   # next frame, rendered off-thread
        self._loading = False
        self._next_at = 0.0
        self._image = None
        self._scrim = _scrim() if Image else None
        self._logo = _white_logo(64) if Image else None

        self.canvas = tk.Canvas(self, width=W, height=H, bg=NO_PHOTO_BG,
                                highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.bg_item = self.canvas.create_image(0, 0, anchor="nw")
        if Image is None:
            self._plain_logo = tk.PhotoImage(file=str(ASSETS / "logo_icon.png"))
            self.canvas.create_image(24, 20, anchor="nw", image=self._plain_logo)

        # time + date, bottom left, with a drop shadow
        self.time_items = []
        for dx, dy, fill in ((2, 2, self.CLOCK_SHADOW), (0, 0, "white")):
            self.time_items.append(self.canvas.create_text(
                30 + dx, 395 + dy, anchor="sw", fill=fill,
                font=(FONT, 84, "bold")))
        self.date_items = []
        for dx, dy, fill in ((1, 1, self.CLOCK_SHADOW), (0, 0, "white")):
            self.date_items.append(self.canvas.create_text(
                34 + dx, 455 + dy, anchor="sw", fill=fill,
                font=(FONT, 24)))

        # big touch targets, drawn on the canvas so the photo shows through
        # (the translucent black is baked into each photo by render_photo)
        self.buttons = [(HELP_BOX, app.manual_help, "I NEED HELP", 26),
                        (DETAILS_BOX, app.show_dashboard, "\u2630  DETAILS", 16)]
        for (x0, y0, x1, y1), _cmd, label, size in self.buttons:
            if Image is None:
                self.canvas.create_rectangle(x0, y0, x1, y1, fill="#10141c",
                                             outline="")
            self.canvas.create_text((x0 + x1) // 2, (y0 + y1) // 2, text=label,
                                    fill="white", font=(FONT, size, "bold"))
        self.canvas.bind("<ButtonRelease-1>", self._tap)

    def _tap(self, event) -> None:
        for (x0, y0, x1, y1), command, _label, _size in self.buttons:
            if x0 <= event.x <= x1 and y0 <= event.y <= y1:
                command()
                return

    # -- slideshow -------------------------------------------------------------

    def _prepare_next(self) -> None:
        path = self.photos[self._i % len(self.photos)]
        self._i += 1
        try:
            self._ready = render_photo(path, self._scrim, self._logo)
        except Exception:                # a bad file must not stop the show
            self._ready = None
        self._loading = False

    def _advance(self, now: float) -> None:
        if not self.photos or Image is None:
            return
        if self._ready is None and not self._loading:
            self._loading = True
            threading.Thread(target=self._prepare_next, daemon=True).start()
        if self._ready is not None and now >= self._next_at:
            self._image = tk.PhotoImage(data=self._ready, format="PPM")
            self.canvas.itemconfig(self.bg_item, image=self._image)
            self._ready = None
            self._next_at = now + self.interval

    def refresh(self, app) -> None:
        self._advance(app.now)
        t = time.localtime()
        text = time.strftime("%I:%M", t).lstrip("0") + time.strftime(" %p", t)
        date = time.strftime("%A, %B ", t) + str(t.tm_mday)
        for item in self.time_items:
            self.canvas.itemconfig(item, text=text)
        for item in self.date_items:
            self.canvas.itemconfig(item, text=date)
