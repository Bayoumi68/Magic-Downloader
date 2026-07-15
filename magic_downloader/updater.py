"""Update check + in-app download of the latest installer (GitHub Releases).

The app publishes every release with version-less asset names, so the "latest"
download URLs never change. We ask the GitHub API for the newest tag, compare it
to the running version, and can fetch + verify the installer against the
published SHA256SUMS.txt before handing it to Windows to run.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import requests

from magic_downloader import __version__
from magic_downloader.paths import DATA_ROOT

REPO = "Bayoumi68/Magic-Downloader"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
DL_BASE = f"https://github.com/{REPO}/releases/latest/download"
INSTALLER_NAME = "MagicDownloader-Setup.exe"
SUMS_NAME = "SHA256SUMS.txt"
TIMEOUT = 20


@dataclass
class Release:
    version: str          # "0.5.13"
    tag: str              # "v0.5.13"
    notes: str
    url: str              # installer download URL


def parse_version(v: str) -> tuple[int, ...]:
    """'v0.5.12' -> (0, 5, 12). Trailing non-numeric parts are ignored."""
    out: list[int] = []
    for part in re.split(r"[.\-+_]", (v or "").strip().lstrip("vV")):
        if part.isdigit():
            out.append(int(part))
        else:
            break
    return tuple(out) or (0,)


def is_newer(latest: str, current: str = __version__) -> bool:
    """True when *latest* is a strictly newer version than *current*."""
    return parse_version(latest) > parse_version(current)


def check_latest(timeout: int = TIMEOUT) -> Release:
    """Ask GitHub for the newest release. Raises on network/HTTP errors."""
    r = requests.get(
        API_LATEST, timeout=timeout,
        headers={"Accept": "application/vnd.github+json",
                 "User-Agent": f"MagicDownloader/{__version__}"},
    )
    r.raise_for_status()
    data = r.json()
    tag = str(data.get("tag_name") or "")
    return Release(
        version=tag.lstrip("vV"),
        tag=tag,
        notes=str(data.get("body") or "").strip(),
        url=f"{DL_BASE}/{INSTALLER_NAME}",
    )


def _published_sha256(timeout: int = TIMEOUT) -> str | None:
    """The installer's hash from the release's SHA256SUMS.txt (None if absent)."""
    try:
        r = requests.get(f"{DL_BASE}/{SUMS_NAME}", timeout=timeout)
        r.raise_for_status()
        for line in r.text.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[-1] == INSTALLER_NAME:
                return parts[0].strip().lower()
    except Exception:  # noqa: BLE001 — checksum is best-effort
        return None
    return None


def download_installer(
    progress: Callable[[int, int], None] | None = None,
    timeout: int = TIMEOUT,
) -> Path:
    """Download the latest installer and verify it against the published
    SHA-256. Returns the file path. Raises on network/checksum failure."""
    dest_dir = DATA_ROOT / "updates"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / INSTALLER_NAME
    tmp = dest_dir / (INSTALLER_NAME + ".part")

    digest = hashlib.sha256()
    done = 0
    with requests.get(f"{DL_BASE}/{INSTALLER_NAME}", stream=True, timeout=timeout) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length") or 0)
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(256 * 1024):
                if not chunk:
                    continue
                f.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)

    expected = _published_sha256(timeout=timeout)
    if expected and digest.hexdigest().lower() != expected:
        tmp.unlink(missing_ok=True)
        raise ValueError(
            "The downloaded installer didn't match its published checksum — "
            "download aborted."
        )
    tmp.replace(dest)
    return dest


def run_installer(path: Path) -> None:
    """Hand the installer to Windows. It closes this app itself before copying."""
    if sys.platform == "win32":
        import os

        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        subprocess.Popen([str(path)])
