"""Classify a URL / content-type into a media kind (http | hls | dash).

The browser extension already does a first pass, but the app double-checks so
that manually pasted URLs and captured downloads get the right handler.
"""

from __future__ import annotations

from enum import Enum
from urllib.parse import urlparse


class MediaKind(str, Enum):
    HTTP = "http"   # progressive file: direct download (mp4, mp3, zip, ...)
    HLS = "hls"     # HTTP Live Streaming manifest (.m3u8)
    DASH = "dash"   # MPEG-DASH manifest (.mpd)
    PAGE = "page"   # a web page whose video yt-dlp should extract (supported sites)


# Extensions that indicate a progressive media *file* (worth a video badge)
VIDEO_EXTS = {".mp4", ".m4v", ".webm", ".mov", ".mkv", ".avi", ".flv", ".wmv", ".ts", ".m4s"}
AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".wav", ".flac", ".wma"}
MEDIA_EXTS = VIDEO_EXTS | AUDIO_EXTS

# Content-type fragments → kind
_CT_HLS = ("mpegurl", "vnd.apple.mpegurl", "x-mpegurl")
_CT_DASH = ("dash+xml",)


def _ext(url: str) -> str:
    path = urlparse(url).path.lower()
    dot = path.rfind(".")
    slash = path.rfind("/")
    if dot > slash:
        return path[dot:]
    return ""


def classify_url(url: str, content_type: str = "") -> MediaKind:
    """Best-effort classification from the URL and optional Content-Type."""
    ct = (content_type or "").lower()
    if any(tok in ct for tok in _CT_HLS):
        return MediaKind.HLS
    if any(tok in ct for tok in _CT_DASH):
        return MediaKind.DASH

    ext = _ext(url)
    if ext in (".m3u8", ".m3u"):
        return MediaKind.HLS
    if ext == ".mpd":
        return MediaKind.DASH

    # Some CDNs hide the extension behind query strings; sniff the raw url too.
    low = url.lower()
    if ".m3u8" in low:
        return MediaKind.HLS
    if ".mpd" in low:
        return MediaKind.DASH
    return MediaKind.HTTP


def detect_kind(data: dict) -> MediaKind:
    """Classify from an extension/browser payload dict.

    Honours an explicit ``media_type`` hint from the extension, then falls back
    to URL/Content-Type sniffing.
    """
    hint = str(data.get("media_type") or data.get("kind") or "").lower().strip()
    if hint in ("hls", "m3u8"):
        return MediaKind.HLS
    if hint in ("dash", "mpd"):
        return MediaKind.DASH
    if hint in ("page", "site", "ytdlp", "video-page"):
        return MediaKind.PAGE
    if hint in ("http", "https", "file", "progressive"):
        # Still verify — the URL is authoritative for manifests.
        pass
    return classify_url(str(data.get("url") or ""), str(data.get("content_type") or ""))


def is_media_file(url: str) -> bool:
    """True if the URL looks like a direct audio/video file."""
    return _ext(url) in MEDIA_EXTS
