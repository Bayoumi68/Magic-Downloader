"""Locate ffmpeg and mux/concat media segments into a final file.

Design goal (per user choice): auto-detect ffmpeg, degrade gracefully when it
is absent. When ffmpeg is available we produce a clean, seekable MP4. When it
is not, we still deliver the content by raw-concatenating the transport-stream
segments (a playable ``.ts`` for HLS) and tell the caller to install ffmpeg
for a proper MP4.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

# Cache the resolved path so we only probe the filesystem once.
_FFMPEG_CACHE: str | None = None
_PROBED = False

# Common Windows install locations to check beyond PATH.
_WINDOWS_HINTS = [
    r"C:\ffmpeg\bin\ffmpeg.exe",
    r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
    r"C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe",
]

# Subprocess flag to avoid a console window flashing on Windows.
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def _app_bundled_dir() -> Path:
    """Where a user-fetched/bundled ffmpeg lives (app data 'bin' folder)."""
    from magic_downloader.paths import BIN_DIR

    return BIN_DIR


def find_ffmpeg(extra_hint: str | None = None) -> str | None:
    """Return an ffmpeg executable path, or ``None`` if not found.

    Search order: explicit hint → app bin/ folder → PATH → OS-specific hints.
    Result is cached; pass ``extra_hint`` to force a re-check with a new path.
    """
    global _FFMPEG_CACHE, _PROBED
    if extra_hint:
        cand = _valid_exe(extra_hint)
        if cand:
            _FFMPEG_CACHE = cand
            _PROBED = True
            return cand
    if _PROBED:
        return _FFMPEG_CACHE

    _PROBED = True
    exe = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"

    # 1) App-bundled bin/
    bundled = _app_bundled_dir() / exe
    if bundled.exists():
        _FFMPEG_CACHE = str(bundled)
        return _FFMPEG_CACHE

    # 2) PATH
    on_path = shutil.which("ffmpeg")
    if on_path:
        _FFMPEG_CACHE = on_path
        return _FFMPEG_CACHE

    # 3) OS-specific hints
    if sys.platform == "win32":
        for hint in _WINDOWS_HINTS:
            if Path(hint).exists():
                _FFMPEG_CACHE = hint
                return _FFMPEG_CACHE
    else:
        for hint in ("/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/opt/homebrew/bin/ffmpeg"):
            if Path(hint).exists():
                _FFMPEG_CACHE = hint
                return _FFMPEG_CACHE

    _FFMPEG_CACHE = None
    return None


def _valid_exe(path: str) -> str | None:
    p = Path(path)
    if p.is_file():
        return str(p)
    which = shutil.which(path)
    return which


def reset_cache() -> None:
    """Force the next :func:`find_ffmpeg` to re-probe (e.g. after installing)."""
    global _FFMPEG_CACHE, _PROBED
    _FFMPEG_CACHE = None
    _PROBED = False


def has_ffmpeg() -> bool:
    return find_ffmpeg() is not None


def _run(args: list[str], timeout: int | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=_NO_WINDOW,
        timeout=timeout,
        check=False,
    )


def mux_to_mp4(
    inputs: list[str | os.PathLike[str]],
    output: str | os.PathLike[str],
    *,
    copy: bool = True,
) -> None:
    """Combine one or more input files (e.g. video + audio) into ``output``.

    Uses stream-copy by default (fast, no re-encode). Raises RuntimeError on
    failure or if ffmpeg is unavailable.
    """
    ff = find_ffmpeg()
    if not ff:
        raise RuntimeError("ffmpeg not found")
    args: list[str] = [ff, "-y", "-hide_banner", "-loglevel", "error"]
    for inp in inputs:
        args += ["-i", str(inp)]
    if copy:
        args += ["-c", "copy"]
    # +faststart makes the MP4 web-seekable; bsf fixes AAC in TS containers.
    args += ["-movflags", "+faststart", "-bsf:a", "aac_adtstoasc", str(output)]
    proc = _run(args)
    if proc.returncode != 0:
        # Retry without the audio bitstream filter (not always applicable).
        args2 = [ff, "-y", "-hide_banner", "-loglevel", "error"]
        for inp in inputs:
            args2 += ["-i", str(inp)]
        if copy:
            args2 += ["-c", "copy"]
        args2 += ["-movflags", "+faststart", str(output)]
        proc2 = _run(args2)
        if proc2.returncode != 0:
            err = (proc2.stderr or proc.stderr or b"").decode("utf-8", "replace")[-500:]
            raise RuntimeError(f"ffmpeg mux failed: {err}")


def mux_to_ts(
    inputs: list[str | os.PathLike[str]],
    output: str | os.PathLike[str],
    *,
    copy: bool = True,
) -> None:
    """Combine inputs into a single MPEG-TS (``.ts``) file.

    MPEG-TS is the raw, concatenation-friendly stream container classic download
    managers save. Stream-copy by default (no re-encode). Raises RuntimeError on
    failure or if ffmpeg is unavailable.
    """
    ff = find_ffmpeg()
    if not ff:
        raise RuntimeError("ffmpeg not found")
    args: list[str] = [ff, "-y", "-hide_banner", "-loglevel", "error"]
    for inp in inputs:
        args += ["-i", str(inp)]
    if copy:
        args += ["-c", "copy"]
    args += ["-f", "mpegts", str(output)]
    proc = _run(args)
    if proc.returncode != 0:
        err = (proc.stderr or b"").decode("utf-8", "replace")[-500:]
        raise RuntimeError(f"ffmpeg ts mux failed: {err}")


def concat_via_demuxer(
    segment_paths: list[str | os.PathLike[str]],
    output: str | os.PathLike[str],
) -> None:
    """Concatenate many segments into one file using ffmpeg's concat demuxer.

    Preferred for HLS: handles TS→MP4 remux cleanly. Raises on failure.
    """
    ff = find_ffmpeg()
    if not ff:
        raise RuntimeError("ffmpeg not found")
    out = Path(output)
    listfile = out.with_suffix(out.suffix + ".ffconcat.txt")
    try:
        with open(listfile, "w", encoding="utf-8") as f:
            for seg in segment_paths:
                p = str(Path(seg).resolve()).replace("\\", "/").replace("'", "'\\''")
                f.write(f"file '{p}'\n")
        args = [
            ff, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(listfile),
            "-c", "copy", "-movflags", "+faststart",
            "-bsf:a", "aac_adtstoasc", str(output),
        ]
        proc = _run(args)
        if proc.returncode != 0:
            args2 = [
                ff, "-y", "-hide_banner", "-loglevel", "error",
                "-f", "concat", "-safe", "0", "-i", str(listfile),
                "-c", "copy", "-movflags", "+faststart", str(output),
            ]
            proc2 = _run(args2)
            if proc2.returncode != 0:
                err = (proc2.stderr or proc.stderr or b"").decode("utf-8", "replace")[-500:]
                raise RuntimeError(f"ffmpeg concat failed: {err}")
    finally:
        try:
            listfile.unlink(missing_ok=True)
        except OSError:
            pass


def raw_concat(segment_paths: list[str | os.PathLike[str]], output: str | os.PathLike[str]) -> None:
    """Byte-concatenate segments without ffmpeg (fallback).

    For MPEG-TS (.ts) HLS segments this yields a playable .ts file. Not valid
    for fragmented-MP4 (fMP4) segments — those need ffmpeg.
    """
    with open(output, "wb") as out:
        for seg in segment_paths:
            with open(seg, "rb") as f:
                shutil.copyfileobj(f, out, length=1024 * 1024)
