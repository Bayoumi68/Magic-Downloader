"""Probe a manifest URL to discover kind, available qualities and duration.

Used by the ``/api/probe`` endpoint so the browser 'download panel' can offer a
quality picker (1080p / 720p / …) before the download starts — just like IDM.
Kept lightweight: one or two small HTTP GETs, no segment downloads.
"""

from __future__ import annotations

import requests

from magic_downloader.media import dash, hls
from magic_downloader.media.detect import MediaKind, classify_url, detect_kind


def probe_media(
    url: str,
    media_type: str | None = None,
    cookie: str = "",
    referrer: str = "",
    user_agent: str = "",
    timeout: int = 20,
) -> dict:
    kind = detect_kind({"url": url, "media_type": media_type})
    session = requests.Session()
    if user_agent:
        session.headers["User-Agent"] = user_agent
    if cookie:
        session.headers["Cookie"] = cookie
    if referrer:
        session.headers["Referer"] = referrer

    result: dict = {"kind": kind.value, "url": url, "variants": [], "duration": 0}

    if kind == MediaKind.HTTP:
        return result

    resp = session.get(url, timeout=timeout, allow_redirects=True)
    resp.raise_for_status()
    text = resp.text
    final_url = str(resp.url)

    # A URL may be mislabeled; re-check by content.
    kind = classify_url(final_url, resp.headers.get("Content-Type", "")) if kind == MediaKind.HTTP else kind
    if kind == MediaKind.HLS or hls.is_master(text) or text.lstrip().startswith("#EXTM3U"):
        result["kind"] = MediaKind.HLS.value
        if hls.is_master(text):
            master = hls.parse_master(text, final_url)
            # One extra GET on the best variant gives the duration, which lets
            # us estimate every variant's size (bandwidth × duration / 8).
            duration = 0
            best = master.best_variant()
            if best:
                try:
                    mtext = session.get(best.url, timeout=timeout).text
                    duration = int(hls.parse_media(mtext, best.url).total_duration)
                except requests.RequestException:
                    duration = 0
            result["duration"] = duration
            result["variants"] = [
                _variant(v.label(), v.height, _res_width(v.resolution), v.bandwidth, duration, v.frame_rate)
                for v in sorted(master.variants, key=lambda v: (v.height, v.bandwidth), reverse=True)
            ]
        else:
            pl = hls.parse_media(text, final_url)
            result["duration"] = int(pl.total_duration)
            result["variants"] = [_variant("source", 0, 0, 0, 0, "")]
    elif kind == MediaKind.DASH or "<MPD" in text:
        result["kind"] = MediaKind.DASH.value
        manifest = dash.parse(text, final_url)
        duration = int(manifest.duration)
        result["duration"] = duration
        result["variants"] = [
            _variant(v.label(), v.height, v.width, v.bandwidth, duration, "")
            for v in sorted(manifest.video, key=lambda v: (v.height, v.bandwidth), reverse=True)
        ]
    return result


def _res_width(resolution: str) -> int:
    import re

    m = re.match(r"(\d+)x\d+", resolution or "")
    return int(m.group(1)) if m else 0


def _variant(label: str, height: int, width: int, bandwidth: int, duration: int, fps: str) -> dict:
    size = int(bandwidth / 8 * duration) if bandwidth and duration else 0
    return {
        "label": label,
        "height": height,
        "width": width,
        "bandwidth": bandwidth,
        "fps": fps or "",
        "filesize": size,
        "approx": bool(size),
        "ext": "mp4",
    }
