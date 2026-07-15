"""yt-dlp powered downloader for web-page videos (~1800 sites).

Why this exists: many sites don't expose a catchable HLS/DASH manifest —
the media URLs are signature-ciphered and hidden in page JavaScript. yt-dlp is
the standard, maintained extractor that solves this, enumerates *every* quality
and format, and merges the chosen video+audio into one file via ffmpeg.

Two entry points:
  * ``probe_formats(url)`` — list qualities/formats for the quality picker.
  * ``YtdlpEngine``       — download a chosen format, with progress/pause/cancel.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable

from magic_downloader.media import ffmpeg as ffmpeg_mod
from magic_downloader.models import DownloadJob, DownloadStatus

ProgressCallback = Callable[[DownloadJob], None]
CancelCheck = Callable[[], bool]


class YtdlpNotInstalled(RuntimeError):
    pass


class _Cancelled(Exception):
    pass


def _import_ytdlp():
    try:
        import yt_dlp  # noqa: PLC0415
        return yt_dlp
    except ImportError as exc:  # pragma: no cover
        raise YtdlpNotInstalled(
            "yt-dlp is not installed. Run: pip install -U yt-dlp"
        ) from exc


def _base_opts(
    cookie: str = "",
    user_agent: str = "",
    referrer: str = "",
    use_cookies: bool = False,
) -> dict:
    headers: dict[str, str] = {}
    if user_agent:
        headers["User-Agent"] = user_agent
    if referrer:
        headers["Referer"] = referrer
    # NOTE: raw logged-in cookies push yt-dlp toward SABR/DRM/PO-token
    # paths that fail with "Requested format is not available". Off by default;
    # public videos download fine anonymously.
    if cookie and use_cookies:
        headers["Cookie"] = cookie
    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "skip_download": True,
        "ignoreerrors": False,
        "windowsfilenames": True,
    }
    if headers:
        opts["http_headers"] = headers
    return opts


def is_probably_supported(url: str) -> bool:
    """Cheap check: does this look like a normal web page (not a media file)?"""
    low = url.lower()
    if any(low.split("?")[0].endswith(ext) for ext in
           (".mp4", ".webm", ".m4a", ".mp3", ".mkv", ".mov", ".ts", ".m3u8", ".mpd")):
        return False
    return low.startswith("http")


def _fmt_label(f: dict) -> str:
    height = f.get("height")
    ext = f.get("ext") or ""
    if f.get("vcodec") == "none" and f.get("acodec") != "none":
        abr = f.get("abr")
        return f"Audio {ext.upper()}" + (f" · {int(abr)}kbps" if abr else "")
    if height:
        fps = f.get("fps")
        fps_s = f"{int(fps)}fps" if fps and fps >= 50 else ""
        return f"{height}p {ext.upper()}" + (f" {fps_s}" if fps_s else "")
    return f.get("format_note") or ext.upper() or f.get("format_id", "format")


def probe_formats(url: str, cookie: str = "", user_agent: str = "", referrer: str = "") -> dict:
    """Return a curated list of downloadable qualities/formats for ``url``.

    Shape: {kind, title, duration, thumbnail, formats:[{format_id,label,height,
    ext,filesize,vcodec,acodec,fps,audio_only,needs_ffmpeg}]}
    """
    yt_dlp = _import_ytdlp()
    with yt_dlp.YoutubeDL(_base_opts(cookie, user_agent, referrer)) as ydl:
        info = ydl.extract_info(url, download=False)

    # Playlist → use the first playable entry.
    if info.get("_type") == "playlist" or info.get("entries"):
        entries = [e for e in (info.get("entries") or []) if e]
        if not entries:
            raise RuntimeError("No playable video found on this page")
        info = entries[0]

    raw = info.get("formats") or []
    duration = int(info.get("duration") or 0)
    # Best audio-only (for merge size estimates on video-only formats).
    audio_fmts = [
        f for f in raw
        if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none")
        and f.get("protocol") not in ("mhtml",)
    ]
    best_audio = max(audio_fmts, key=lambda f: (f.get("abr") or 0), default=None)
    best_audio_size = _est_size(best_audio, duration) if best_audio else 0

    # Show EVERY real format the site offers , deduped only by
    # (resolution, container, progressive) so the list stays readable while
    # still surfacing mp4 AND webm, all resolutions, and every audio track.
    video_rows: dict[tuple, dict] = {}
    audio_rows: dict[tuple, dict] = {}
    for f in raw:
        proto = f.get("protocol") or ""
        ext = (f.get("ext") or "").lower()
        if proto == "mhtml" or ext in ("mhtml", "") or f.get("format_note") == "storyboard":
            continue
        vcodec = f.get("vcodec")
        acodec = f.get("acodec")
        has_v = vcodec not in (None, "none")
        has_a = acodec not in (None, "none")
        if not has_v and not has_a:
            continue

        if has_v:
            h = f.get("height") or 0
            progressive = has_a
            key = (h, ext, progressive)
            score = f.get("tbr") or f.get("vbr") or 0
            prev = video_rows.get(key)
            if prev is not None and score <= prev["_score"]:
                continue
            size = _est_size(f, duration)
            if not progressive:
                size = (size or 0) + best_audio_size
            fps = f.get("fps") or 0
            qual = f"{h}p" if h else (f.get("format_note") or "video")
            if fps and fps >= 50:
                qual += f"{int(fps)}"
            video_rows[key] = {
                # Video-only formats are merged with best audio (needs ffmpeg).
                "format_id": f["format_id"] if progressive else f"{f['format_id']}+bestaudio",
                "label": qual,
                "height": h,
                "ext": ext,
                "filesize": size or 0,
                "vcodec": (vcodec or "").split(".")[0],
                "acodec": "aac" if not progressive else (acodec or "").split(".")[0],
                "fps": fps,
                "audio_only": False,
                "progressive": progressive,
                "needs_ffmpeg": not progressive,
                "approx": (not progressive) or (not f.get("filesize")),
                "_score": score,
            }
        else:
            key = (ext,)
            score = f.get("abr") or f.get("tbr") or 0
            prev = audio_rows.get(key)
            if prev is not None and score <= prev["_score"]:
                continue
            abr = f.get("abr")
            audio_rows[key] = {
                "format_id": f["format_id"],
                "label": f"Audio {ext.upper()}" + (f" {int(abr)}kbps" if abr else ""),
                "height": 0,
                "ext": ext,
                "filesize": _est_size(f, duration) or 0,
                "vcodec": "none",
                "acodec": (acodec or "").split(".")[0],
                "fps": 0,
                "audio_only": True,
                "progressive": False,
                "needs_ffmpeg": False,
                "approx": not f.get("filesize"),
                "_score": score,
            }

    # Video first (highest res, then progressive before merge, then mp4), audio last.
    videos = sorted(
        video_rows.values(),
        key=lambda x: (x["height"], 1 if x["progressive"] else 0, 1 if x["ext"] == "mp4" else 0, x["_score"]),
        reverse=True,
    )
    audios = sorted(audio_rows.values(), key=lambda x: x["_score"], reverse=True)
    formats = videos + audios
    for f in formats:
        f.pop("_score", None)
        f.pop("progressive", None)

    return {
        "kind": "page",
        "title": info.get("title") or "video",
        "duration": int(info.get("duration") or 0),
        "thumbnail": info.get("thumbnail") or "",
        "extractor": info.get("extractor_key") or info.get("extractor") or "",
        "webpage_url": info.get("webpage_url") or url,
        "formats": formats,
    }


def _est_size(f: dict | None, duration: int = 0) -> int:
    """Real filesize if known, else estimate from bitrate × duration."""
    if not f:
        return 0
    s = f.get("filesize") or f.get("filesize_approx") or 0
    if not s and duration:
        tbr = f.get("tbr") or f.get("vbr") or f.get("abr") or 0
        if tbr:
            s = int(tbr * 1000 / 8 * duration)
    return int(s or 0)


def _size_of(f: dict | None) -> int:
    if not f:
        return 0
    return int(f.get("filesize") or f.get("filesize_approx") or 0)


class YtdlpEngine:
    """Download a page video with yt-dlp. Matches the engine interface used by
    :class:`DownloadManager` (run / pause / resume / stop)."""

    def __init__(
        self,
        job: DownloadJob,
        user_agent: str = "",
        on_progress: ProgressCallback | None = None,
        cancel_check: CancelCheck | None = None,
        pause_event: threading.Event | None = None,
        max_speed_bps: float = 0.0,
    ) -> None:
        self.job = job
        self.user_agent = user_agent
        self.on_progress = on_progress
        self.cancel_check = cancel_check or (lambda: False)
        self.pause_event = pause_event or threading.Event()
        self.pause_event.set()
        self.max_speed_bps = max(0.0, float(max_speed_bps or 0.0))
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def pause(self) -> None:
        self.pause_event.clear()

    def resume(self) -> None:
        self.pause_event.set()

    def _emit(self) -> None:
        if self.on_progress:
            self.on_progress(self.job)

    def _cancelled(self) -> bool:
        return self._stop or self.cancel_check()

    def _format_selector(self, has_ffmpeg: bool) -> str:
        meta = self.job.media_meta or {}
        fmt = str(meta.get("format_id") or "").strip()
        height = int(meta.get("height") or 0)
        audio_only = bool(meta.get("audio_only"))

        if audio_only:
            return "bestaudio/best"
        if fmt:
            # A "<vid>+bestaudio" selector needs ffmpeg to merge. Without it,
            # fall back to the best single-file (progressive) format rather than
            # silently downloading video with no sound.
            if "+" in fmt and not has_ffmpeg:
                return "best[vcodec!=none][acodec!=none]/best"
            return fmt
        if height:
            if has_ffmpeg:
                return f"bestvideo[height<={height}]+bestaudio/best[height<={height}]/best"
            return f"best[height<={height}][vcodec!=none][acodec!=none]/best[height<={height}]/best"
        if has_ffmpeg:
            return "bestvideo+bestaudio/best"
        return "best[vcodec!=none][acodec!=none]/best"

    def _progress_hook(self, d: dict) -> None:
        # Cooperative cancel/pause happen here (yt-dlp has no native controls).
        if self._cancelled():
            raise _Cancelled()
        while not self.pause_event.is_set():
            if self._cancelled():
                raise _Cancelled()
            self.job.status = DownloadStatus.PAUSED
            self.job.speed_bps = 0.0
            self._emit()
            time.sleep(0.2)

        status = d.get("status")
        if status == "downloading":
            self.job.status = DownloadStatus.DOWNLOADING
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            if total:
                self.job.total_size = int(total)
            self.job.downloaded = int(done)
            self.job.speed_bps = float(d.get("speed") or 0.0)
            frag = d.get("fragment_index")
            frag_total = d.get("fragment_count")
            if frag and frag_total:
                self.job.media_meta["seg_done"] = int(frag)
                self.job.media_meta["seg_total"] = int(frag_total)
            self._emit()
        elif status == "finished":
            # A stream finished; merging/next stream may follow.
            self.job.status = DownloadStatus.PROCESSING
            self.job.speed_bps = 0.0
            self._emit()

    def _download_attempts(self, has_ffmpeg: bool) -> list[tuple[str, dict | None]]:
        """(format_selector, extractor_args) tries, most-preferred first.

        some sites' `ios`/`web_safari`/`tv` clients frequently fail or serve DRM;
        the default and `android` clients are the reliable ones, so we retry
        with progressively simpler formats + a known-good client set.
        """
        primary = self._format_selector(has_ffmpeg)
        android = {"youtube": {"player_client": ["android", "web"]}}
        if bool((self.job.media_meta or {}).get("audio_only")):
            return [("bestaudio/best", None), ("bestaudio/best", android), ("worstaudio/worst", None)]
        merge = "bestvideo*+bestaudio/best" if has_ffmpeg else "best*[vcodec!=none][acodec!=none]/best"
        return [
            (primary, None),
            (primary, android),
            (merge, android),
            ("best", android),
            ("best/bestvideo+bestaudio/best", None),
        ]

    def run(self) -> None:
        try:
            yt_dlp = _import_ytdlp()
            has_ffmpeg = ffmpeg_mod.has_ffmpeg()
            final = Path(self.job.save_path)
            final.parent.mkdir(parents=True, exist_ok=True)
            outtmpl = str(final.with_suffix("")) + ".%(ext)s"

            self.job.status = DownloadStatus.CONNECTING
            self.job.error = ""
            self._emit()

            base = _base_opts(self.job.cookie, self.user_agent, self.job.referrer)
            base.update({
                "skip_download": False,
                "outtmpl": outtmpl,
                "merge_output_format": "mp4",
                "progress_hooks": [self._progress_hook],
                "retries": 5,
                "fragment_retries": 10,
                "concurrent_fragment_downloads": max(1, int(self.job.connections or 4)),
                "overwrites": True,
            })
            if self.max_speed_bps > 0:
                base["ratelimit"] = self.max_speed_bps
            ff = ffmpeg_mod.find_ffmpeg()
            if ff:
                base["ffmpeg_location"] = str(Path(ff).parent)

            attempts = self._download_attempts(has_ffmpeg)
            info = None
            last_err: Exception | None = None
            for i, (fmt, exargs) in enumerate(attempts):
                if self._cancelled():
                    raise _Cancelled()
                opts = dict(base)
                opts["format"] = fmt
                if exargs:
                    opts["extractor_args"] = exargs
                try:
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        info = ydl.extract_info(self.job.url, download=True)
                    last_err = None
                    break
                except _Cancelled:
                    raise
                except Exception as exc:  # noqa: BLE001 — try the next fallback
                    last_err = exc
                    low = str(exc).lower()
                    # Don't bother retrying truly fatal cases.
                    if "drm" in low and "protected" in low and i >= 1:
                        break
                    continue
            if info is None:
                raise last_err or RuntimeError("Download failed")

            if info.get("entries"):
                info = next((e for e in info["entries"] if e), info)

            filepath = self._final_path(info, outtmpl)
            if filepath and Path(filepath).exists():
                p = Path(filepath)
                self.job.save_path = str(p)
                self.job.filename = p.name
                self.job.total_size = p.stat().st_size
                self.job.downloaded = self.job.total_size

            if not has_ffmpeg:
                self.job.media_meta["ffmpeg_note"] = (
                    "Downloaded the best single-file format. Install ffmpeg for "
                    "higher-quality merged video+audio."
                )

            self.job.status = DownloadStatus.COMPLETE
            self.job.finished_at = time.time()
            self.job.speed_bps = 0.0
            self.job.error = ""
            self._emit()
        except _Cancelled:
            self.job.status = (
                DownloadStatus.PAUSED if not self._stop and self.job.status == DownloadStatus.PAUSED
                else DownloadStatus.CANCELLED
            )
            self.job.speed_bps = 0.0
            self._emit()
        except Exception as exc:  # noqa: BLE001 — surface to UI
            # Strip ANSI colour codes yt-dlp adds to error text.
            import re as _re

            msg = _re.sub(r"\x1b\[[0-9;]*m", "", str(exc)).strip()
            low = msg.lower()
            if "drm" in low and "protected" in low:
                msg = "This video is DRM-protected and can't be downloaded."
            elif "format is not available" in low:
                msg = "No downloadable format for this video (may be DRM/region-locked or need a yt-dlp update: pip install -U yt-dlp)."
            elif "ffmpeg" in low and "not" in low:
                msg += " — click Options → Video → Install ffmpeg."
            self.job.status = DownloadStatus.FAILED
            self.job.error = msg[:300]
            self.job.speed_bps = 0.0
            self._emit()

    def _final_path(self, info: dict, outtmpl: str) -> str:
        rd = info.get("requested_downloads")
        if rd:
            fp = rd[0].get("filepath") or rd[0].get("_filename")
            if fp:
                return fp
        fp = info.get("filepath") or info.get("_filename")
        if fp:
            return fp
        # Fallback: guess merged mp4 next to outtmpl.
        base = outtmpl.replace(".%(ext)s", "")
        for ext in (".mp4", ".mkv", ".webm", ".m4a"):
            if Path(base + ext).exists():
                return base + ext
        return ""
