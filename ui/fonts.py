"""Register the bundled Encode Sans Expanded so Tk can use it by name.

Tk cannot load a font file itself; the font has to be known to the OS font
system first. Per process and non-persistent: CoreText on macOS, fontconfig
(what Tk's Xft uses) on Linux. Call before the first Tk widget is created.
Falls back silently to Tk's default sans if registration fails.
"""

import ctypes
import ctypes.util
import sys
from pathlib import Path

FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"


def register() -> None:
    files = sorted(FONT_DIR.glob("*.ttf"))
    try:
        if sys.platform == "darwin":
            cf = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation"))
            ct = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreText"))
            cf.CFURLCreateFromFileSystemRepresentation.restype = ctypes.c_void_p
            cf.CFURLCreateFromFileSystemRepresentation.argtypes = [
                ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_bool]
            ct.CTFontManagerRegisterFontsForURL.argtypes = [
                ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
            for f in files:
                raw = str(f).encode()
                url = cf.CFURLCreateFromFileSystemRepresentation(None, raw, len(raw), False)
                ct.CTFontManagerRegisterFontsForURL(url, 1, None)   # 1 = process scope
        else:
            fc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("fontconfig"))
            fc.FcConfigAppFontAddFile.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
            for f in files:
                fc.FcConfigAppFontAddFile(None, str(f).encode())
    except Exception:
        pass
