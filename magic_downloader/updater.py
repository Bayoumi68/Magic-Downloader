"""Update check + in-app download of the latest installer (GitHub Releases).

The app publishes every release with version-less asset names, so the "latest"
download URLs never change. We ask the GitHub API for the newest tag, compare it
to the running version, and can fetch + verify the installer before handing it
to Windows to run.

The installer is distributed **zipped** (``MagicDownloader-Setup.zip``) — a bare
``.exe`` is often blocked or renamed by browsers/antivirus, and it's the only
download the project ships. So we download the zip, verify it against the
published SHA-256, extract the installer ``.exe`` from it, and run that.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
import zipfile
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
INSTALLER_ZIP = "MagicDownloader-Setup.zip"   # what we download (browser/AV-safe)
INSTALLER_EXE = "MagicDownloader-Setup.exe"   # the installer inside the zip, run after extract
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
        url=f"{DL_BASE}/{INSTALLER_ZIP}",
    )


def _published_sha256(name: str = INSTALLER_ZIP, timeout: int = TIMEOUT) -> str | None:
    """The hash of *name* from the release's SHA256SUMS.txt (None if absent)."""
    try:
        r = requests.get(f"{DL_BASE}/{SUMS_NAME}", timeout=timeout)
        r.raise_for_status()
        for line in r.text.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[-1] == name:
                return parts[0].strip().lower()
    except Exception:  # noqa: BLE001 — checksum is best-effort
        return None
    return None


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().lower()


def _extract_installer(zip_path: Path, dest_dir: Path) -> Path:
    """Pull the installer .exe out of the downloaded setup zip.

    Returns the path to the extracted ``MagicDownloader-Setup.exe``. Raises
    ValueError if the zip holds no .exe.
    """
    with zipfile.ZipFile(zip_path) as zf:
        members = [n for n in zf.namelist() if n.lower().endswith(".exe")]
        if not members:
            raise ValueError("The setup zip didn't contain an installer .exe.")
        member = members[0]
        zf.extract(member, dest_dir)
    extracted = dest_dir / member
    dest = dest_dir / INSTALLER_EXE
    if extracted.resolve() != dest.resolve():
        extracted.replace(dest)
    return dest


def cached_installer(timeout: int = TIMEOUT) -> Path | None:
    """An already-downloaded installer for the *current* latest release.

    Returned only when the cached setup zip's SHA-256 still matches the published
    one, which makes this both an identity check (it's the release we're about to
    install) and an integrity check. Lets a decline survive a restart without
    re-fetching. Returns None if there's nothing usable.
    """
    zip_dest = DATA_ROOT / "updates" / INSTALLER_ZIP
    if not zip_dest.exists():
        return None
    try:
        expected = _published_sha256(timeout=timeout)
        if not expected or _file_sha256(zip_dest) != expected:
            return None                       # can't prove what it is
        return _extract_installer(zip_dest, zip_dest.parent)
    except (OSError, ValueError, zipfile.BadZipFile):
        return None


class DownloadCancelled(Exception):
    """The caller asked to stop the update download."""


def download_installer(
    progress: Callable[[int, int], None] | None = None,
    timeout: int = TIMEOUT,
    cancel_check: Callable[[], bool] | None = None,
) -> Path:
    """Download the latest setup zip, verify it against the published SHA-256,
    extract the installer .exe, and return its path. Raises on network/checksum
    failure, or DownloadCancelled if *cancel_check* starts returning True."""
    dest_dir = DATA_ROOT / "updates"
    dest_dir.mkdir(parents=True, exist_ok=True)
    zip_dest = dest_dir / INSTALLER_ZIP
    tmp = dest_dir / (INSTALLER_ZIP + ".part")

    digest = hashlib.sha256()
    done = 0
    with requests.get(f"{DL_BASE}/{INSTALLER_ZIP}", stream=True, timeout=timeout) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length") or 0)
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(256 * 1024):
                if cancel_check and cancel_check():
                    f.close()
                    tmp.unlink(missing_ok=True)   # don't leave a part-file behind
                    raise DownloadCancelled()
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
    tmp.replace(zip_dest)
    return _extract_installer(zip_dest, dest_dir)


def run_installer(path: Path) -> None:
    """Hand the installer to Windows. It closes this app itself before copying."""
    if sys.platform == "win32":
        import os

        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        subprocess.Popen([str(path)])
