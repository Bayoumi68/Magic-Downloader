"""Application paths and default settings."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from magic_downloader.paths import DATA_DIR, DOWNLOADS_DIR, RESOURCE_ROOT

# Read-only resource root (project folder when run from source; bundle when frozen)
ROOT = RESOURCE_ROOT
SETTINGS_PATH = DATA_DIR / "settings.json"
JOBS_PATH = DATA_DIR / "jobs.json"

DEFAULT_SETTINGS: dict[str, Any] = {
    "default_save_path": str(DOWNLOADS_DIR),
    "connections": 8,
    "max_simultaneous": 3,
    "user_agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36 MagicDownloader/0.2"
    ),
    "chunk_size": 256 * 1024,
    # Parallel workers used to fetch HLS/DASH segments (falls back to connections)
    "media_workers": 8,
    # Network behaviour
    "timeout": 60,             # per-request seconds
    "retries": 3,              # segment/stream retry attempts
    "max_speed_kbps": 0,       # global download cap in KB/s (0 = unlimited)
    # Video / ffmpeg
    "ffmpeg_path": "",         # explicit ffmpeg path; empty = auto-detect
    "default_video_quality": "best",  # best | ask | 2160 | 1440 | 1080 | 720 | 480 | 360 | audio
    # UX
    "confirm_delete": True,
    # Download-list columns to show (right-click the list header to choose).
    # Empty means every column, so ones added by a later version show up for
    # anyone who hasn't picked their own set.
    "visible_columns": [],
    # Left-to-right order of the columns (drag a heading to move one), and their
    # pixel widths. Empty = the built-in layout.
    "column_order": [],
    "column_widths": {},
    # Look for a newer release at startup and every 60 minutes (a toast, never
    # a popup). Turn off in Options → General.
    "check_updates": True,
    # Download a newer release in the background, then ask before installing.
    # Only honoured when check_updates is on, and it always waits until no
    # download is running so the installer (which closes the app) can't
    # interrupt a transfer. The install itself is never automatic.
    "auto_install_updates": False,
    # A version the user chose to skip; never prompt for it again (Help → About
    # still installs it on demand).
    "skipped_update": "",
    # The version currently installed and when it was first launched, so the app
    # can show "updated <date>". Set the first time a new version runs.
    "installed_version": "",
    "updated_at": 0.0,
    # Keep running in the system tray when the window is closed;
    # only "Exit" actually quits.
    "close_to_tray": True,
    "minimize_to_tray": False,  # also hide to tray on the minimize button
    "last_save_dir": "",        # remember the folder the user last downloaded to
    # Pop a separate progress window for each download
    "show_progress_dialog": True,
    "progress_close_on_complete": False,
    "category_paths": {
        "General": str(DOWNLOADS_DIR / "General"),
        "Compressed": str(DOWNLOADS_DIR / "Compressed"),
        "Documents": str(DOWNLOADS_DIR / "Documents"),
        "Music": str(DOWNLOADS_DIR / "Music"),
        "Video": str(DOWNLOADS_DIR / "Video"),
    },
    # Extensions that map a downloaded file to a category ("File Types").
    "category_extensions": {
        "Compressed": [".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".iso"],
        "Documents": [".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".rtf", ".odt"],
        "Music": [".mp3", ".flac", ".wav", ".aac", ".ogg", ".m4a", ".wma", ".opus"],
        "Video": [".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm", ".m4v", ".flv", ".ts"],
    },
    # Browser integration (extension talks to this localhost API)
    "browser_integration": True,
    "browser_port": 7373,
    "browser_token": "",  # optional shared secret; leave empty for local-only trust
    "browser_auto_start": True,  # start download immediately when captured from browser
    # Show the "Download File Info" dialog (name/category/folder) for
    # downloads captured from the browser, instead of starting them silently.
    "confirm_browser_captures": True,
}


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    for path in DEFAULT_SETTINGS["category_paths"].values():
        Path(path).mkdir(parents=True, exist_ok=True)


def load_settings() -> dict[str, Any]:
    ensure_dirs()
    if not SETTINGS_PATH.exists():
        save_settings(DEFAULT_SETTINGS.copy())
        return DEFAULT_SETTINGS.copy()
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        data = {}
    merged = DEFAULT_SETTINGS.copy()
    merged.update(data)
    # Deep-merge nested dicts so new default categories survive upgrades.
    cats = DEFAULT_SETTINGS["category_paths"].copy()
    cats.update(data.get("category_paths") or {})
    merged["category_paths"] = cats
    exts = {k: list(v) for k, v in DEFAULT_SETTINGS["category_extensions"].items()}
    exts.update(data.get("category_extensions") or {})
    merged["category_extensions"] = exts
    return merged


def save_settings(settings: dict[str, Any]) -> None:
    ensure_dirs()
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)


def category_for_filename(filename: str, settings: dict[str, Any] | None = None) -> str:
    ext = Path(filename).suffix.lower()
    # Prefer the user's editable File Types mapping when available.
    if settings and isinstance(settings.get("category_extensions"), dict):
        for cat, exts in settings["category_extensions"].items():
            if ext in {str(e).lower() for e in exts}:
                return cat
        return "General"
    if ext in {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".iso"}:
        return "Compressed"
    if ext in {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".rtf", ".odt"}:
        return "Documents"
    if ext in {".mp3", ".flac", ".wav", ".aac", ".ogg", ".m4a", ".wma", ".opus"}:
        return "Music"
    if ext in {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm", ".m4v", ".flv", ".ts"}:
        return "Video"
    return "General"


def resolve_save_path(settings: dict[str, Any], filename: str, category: str | None = None) -> Path:
    cat = category or category_for_filename(filename)
    base = settings.get("category_paths", {}).get(cat) or settings.get("default_save_path")
    if not base:
        base = str(DOWNLOADS_DIR)
    path = Path(base)
    path.mkdir(parents=True, exist_ok=True)
    return path / filename
