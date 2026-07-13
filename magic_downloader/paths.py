"""Filesystem locations that work both from source and as a frozen exe.

When running from source, everything lives under the project folder (unchanged
behaviour). When packaged with PyInstaller (``sys.frozen``):
  * read-only bundled files (the browser extension) come from the bundle;
  * writable data (settings, jobs, a downloaded ffmpeg) goes to
    ``%LOCALAPPDATA%\\MagicDownloader`` so it works even if the app is installed
    in a read-only location.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    """Folder containing read-only bundled resources."""
    if is_frozen():
        base = getattr(sys, "_MEIPASS", None)
        return Path(base) if base else Path(sys.executable).resolve().parent
    # magic_downloader/paths.py -> project root
    return Path(__file__).resolve().parent.parent


def data_root() -> Path:
    """Writable per-user app folder."""
    if is_frozen():
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
        root = Path(base) / "MagicDownloader"
    else:
        root = Path(__file__).resolve().parent.parent
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return root


RESOURCE_ROOT = resource_root()
DATA_ROOT = data_root()

DATA_DIR = DATA_ROOT / "data"          # settings.json, jobs.json
BIN_DIR = DATA_ROOT / "bin"            # auto-installed ffmpeg lives here
DOWNLOADS_DIR = Path.home() / "Downloads" / "MagicDownloader"


def extension_dir() -> Path:
    """Where the loadable, unpacked extension lives (a stable, real folder)."""
    if is_frozen():
        return DATA_ROOT / "browser_extension"
    return RESOURCE_ROOT / "browser_extension"


def install_txt_path() -> Path:
    if is_frozen():
        return DATA_ROOT / "INSTALL_BROWSER.txt"
    return RESOURCE_ROOT / "INSTALL_BROWSER.txt"


def sync_bundled_resources() -> None:
    """Frozen only: copy bundled read-only files to a stable writable folder so
    the "Install browser extension" feature points at a real, persistent path
    (the PyInstaller bundle dir is internal/temporary)."""
    if not is_frozen():
        return
    import shutil

    try:
        src = RESOURCE_ROOT / "browser_extension"
        dst = DATA_ROOT / "browser_extension"
        if src.exists():
            dst.mkdir(parents=True, exist_ok=True)
            for item in src.rglob("*"):
                target = dst / item.relative_to(src)
                if item.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(item, target)
        txt = RESOURCE_ROOT / "INSTALL_BROWSER.txt"
        if txt.exists():
            shutil.copy2(txt, DATA_ROOT / "INSTALL_BROWSER.txt")
    except OSError:
        pass
