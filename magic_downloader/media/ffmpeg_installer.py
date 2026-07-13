"""Download a static ffmpeg build into the app's ``bin/`` folder.

Matches the user's choice of a one-click ffmpeg helper. ffmpeg is what merges
streamed / YouTube video+audio into a single clean MP4; without it the app can
only save raw ``.ts`` or separate files. This grabs a self-contained build so
the user doesn't have to touch PATH.
"""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path
from typing import Callable

import requests

from magic_downloader.media import ffmpeg as ffmpeg_mod

ProgressCB = Callable[[int, int, str], None]  # (downloaded, total, phase)

# Windows static builds (self-contained, include ffmpeg.exe + ffprobe.exe).
_WIN64_URLS = [
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
    "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
]


def bin_dir() -> Path:
    from magic_downloader.paths import BIN_DIR

    return BIN_DIR


def _emit(cb: ProgressCB | None, done: int, total: int, phase: str) -> None:
    if cb:
        try:
            cb(done, total, phase)
        except Exception:
            pass


def install_ffmpeg(progress: ProgressCB | None = None) -> str:
    """Download + extract ffmpeg into ``bin/``. Returns the ffmpeg path.

    Raises RuntimeError on unsupported OS or if every source fails.
    """
    if sys.platform != "win32":
        raise RuntimeError(
            "Auto-install is Windows-only. On macOS: 'brew install ffmpeg'. "
            "On Linux: use your package manager (e.g. apt install ffmpeg)."
        )

    dest = bin_dir()
    dest.mkdir(parents=True, exist_ok=True)

    last_error = ""
    for url in _WIN64_URLS:
        try:
            _emit(progress, 0, 0, f"Connecting to {_host(url)}…")
            data = _download(url, progress)
            _emit(progress, len(data), len(data), "Extracting…")
            path = _extract_ffmpeg(data, dest)
            if path:
                ffmpeg_mod.reset_cache()
                found = ffmpeg_mod.find_ffmpeg(extra_hint=str(path))
                _emit(progress, len(data), len(data), "Done")
                return found or str(path)
            last_error = "ffmpeg.exe not found inside the downloaded archive"
        except Exception as exc:  # noqa: BLE001 — try the next mirror
            last_error = f"{_host(url)}: {exc}"
            continue
    raise RuntimeError(f"Could not install ffmpeg. Last error: {last_error}")


def _host(url: str) -> str:
    try:
        return url.split("/")[2]
    except IndexError:
        return url


def _download(url: str, progress: ProgressCB | None) -> bytes:
    with requests.get(url, stream=True, timeout=60, allow_redirects=True) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length") or 0)
        buf = io.BytesIO()
        done = 0
        for chunk in r.iter_content(chunk_size=256 * 1024):
            if not chunk:
                continue
            buf.write(chunk)
            done += len(chunk)
            _emit(progress, done, total, "Downloading ffmpeg…")
        return buf.getvalue()


def _extract_ffmpeg(zip_bytes: bytes, dest: Path) -> Path | None:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        wanted = {"ffmpeg.exe": None, "ffprobe.exe": None}
        for name in names:
            base = name.replace("\\", "/").split("/")[-1].lower()
            if base in wanted and wanted[base] is None:
                wanted[base] = name
        if not wanted["ffmpeg.exe"]:
            return None
        out_ffmpeg = dest / "ffmpeg.exe"
        with zf.open(wanted["ffmpeg.exe"]) as src, open(out_ffmpeg, "wb") as dst:
            dst.write(src.read())
        if wanted["ffprobe.exe"]:
            with zf.open(wanted["ffprobe.exe"]) as src, open(dest / "ffprobe.exe", "wb") as dst:
                dst.write(src.read())
        return out_ffmpeg
