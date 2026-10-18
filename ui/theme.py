"""DownLink light palette, shared by every screen. Ink colour matches the logo (near-black on white)."""

BG = "#f3f5f9"
PANEL = "#ffffff"
PANEL2 = "#e9edf4"
TEXT = "#0b0d17"
DIM = "#5d6878"
GREEN = "#1f9d55"
GREEN_D = "#177a43"
RED = "#d64545"
RED_D = "#a8272f"
AMBER = "#c98a00"
BLUE = "#2f7de1"
HOT = "#e8672a"

# drawing details
TRACK = "#dbe2ed"          # empty bar tracks, ring tracks
GRID_A = "#e6ebf3"         # room grid, alternating line shades
GRID_B = "#d3dae6"
ROOM_FILL = "#ffffff"
MARKER = "#0b0d17"         # person / sensor dots (drawn on a light background)
MUTED_DOT = "#7b8798"
HOT_OLD = "#f1c3ab"        # fading trails: newest = HOT / BLUE_NEW, oldest = *_OLD
BLUE_NEW = "#5b9bea"
BLUE_OLD = "#c4d7f0"
ON_RED_1 = "#ffffff"       # text on a red alert background
ON_RED_2 = "#ffe9e9"
BG_RGB = (0xf3, 0xf5, 0xf9)  # waterfall pixel buffer background (same as BG)

FONT = "Encode Sans Expanded"

import fonts
fonts.register()          # before any widget asks for FONT
