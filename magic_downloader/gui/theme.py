"""classic colors and fonts."""

# Classic download-manager palette (blue chrome + light list)
BG = "#e8eef5"
BG_DARK = "#1e3a5f"
BG_TOOLBAR = "#2b579a"
BG_SIDEBAR = "#f5f7fa"
BG_LIST = "#ffffff"
BG_DETAIL = "#f0f4f8"
BG_STATUS = "#dce6f0"
FG = "#1a1a1a"
FG_MUTED = "#5a6a7a"
FG_ON_DARK = "#ffffff"
FG_ON_DARK_DISABLED = "#6a83ab"  # dimmed toolbar text for inactive buttons
ACCENT = "#2b579a"
ACCENT_HOVER = "#1e3f73"
GREEN = "#2e9b3a"
GREEN_SEG = "#3cb54a"
GREEN_SEG_DONE = "#1f7a2e"
ORANGE = "#d17a00"
RED = "#c0392b"
BLUE = "#1a6fb5"
GRAY = "#7a8694"
BORDER = "#b8c4d0"
SELECT = "#cfe2f5"
SELECT_FG = "#0d2137"

FONT_UI = ("Segoe UI", 10)
FONT_UI_BOLD = ("Segoe UI", 10, "bold")
FONT_TITLE = ("Segoe UI", 11, "bold")
FONT_SMALL = ("Segoe UI", 9)
FONT_MONO = ("Consolas", 9)
FONT_TOOLBAR = ("Segoe UI", 9)

STATUS_COLORS = {
    "Queued": GRAY,
    "Connecting": BLUE,
    "Downloading": GREEN,
    "Paused": ORANGE,
    "Complete": "#1a7a32",
    "Failed": RED,
    "Cancelled": GRAY,
}
