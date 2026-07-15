"""Colours and fonts.

The palette is taken from logo.png rather than invented: its dominant inks are
azure #0078d8 and navy #001860. The previous scheme was Office-2003 blue
(#2b579a chrome on #e8eef5) which fought the logo sitting on top of it.

Every name below is used across the GUI, so restyling the app is a matter of
changing values here — not of touching widgets.
"""

# Surfaces
BG = "#f4f6f9"           # the page behind everything
BG_DARK = "#0a1738"      # deepest navy
BG_TOOLBAR = "#0d1f4d"   # navy chrome, from the logo's #001860
BG_SIDEBAR = "#f9fafc"
BG_LIST = "#ffffff"
BG_DETAIL = "#f9fafc"
BG_STATUS = "#f4f6f9"
STRIPE = "#fafbfd"       # alternating list rows — barely there, on purpose

# Text
FG = "#111827"
FG_MUTED = "#6b7280"
FG_ON_DARK = "#ffffff"
FG_ON_DARK_DISABLED = "#5c6f96"  # dimmed toolbar text for inactive buttons
FG_ON_DARK_MUTED = "#8fa6c9"     # secondary text on the navy bar ("TOTAL SPEED")
TOOLBAR_SEP = "#1b3269"          # divider on the navy bar

# Badges / strips on the dark bar
SPEED_BADGE = "#4ade80"          # the big live speed figure
AMBER = "#fbbf24"                # "Browser: off"
TOAST_BG = "#0f9d58"             # capture / update notice strip

# Accents
ACCENT = "#0078d8"       # the logo's azure
# Toolbar-button hover. This sits ON the navy bar, so it's a lighter navy:
# using the azure here would flash an unrelated colour under the cursor.
ACCENT_HOVER = "#1b3269"

GREEN = "#0f9d58"        # actively downloading
GREEN_DONE = "#15803d"   # finished — deeper, so it reads apart from "Downloading"
GREEN_SEG = "#22c55e"
GREEN_SEG_DONE = "#0f9d58"
ORANGE = "#e08600"
RED = "#d93025"
BLUE = "#0078d8"
GRAY = "#9ca3af"
BORDER = "#e5e7eb"       # a hairline, not a 2003 bevel
SELECT = "#e8f2fd"
SELECT_FG = "#0b4f9e"

FONT_UI = ("Segoe UI", 10)
FONT_UI_BOLD = ("Segoe UI", 10, "bold")
FONT_TITLE = ("Segoe UI", 11, "bold")
FONT_SMALL = ("Segoe UI", 9)
FONT_MONO = ("Consolas", 9)
FONT_TOOLBAR = ("Segoe UI", 9)

# The single source of truth for row colours — app.py builds its Treeview tags
# from this. It used to hardcode Complete as #1a7a32, which quietly survived a
# retheme and left one status stuck in the old palette.
STATUS_COLORS = {
    "Queued": GRAY,
    "Connecting": BLUE,
    "Downloading": GREEN,
    "Processing": BLUE,
    "Paused": ORANGE,
    "Complete": GREEN_DONE,
    "Failed": RED,
    "Cancelled": GRAY,
}
