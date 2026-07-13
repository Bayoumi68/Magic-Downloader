"""Minimal MPEG-DASH (.mpd) parser.

Extracts the best video and audio Representations and expands their segment
URLs. Covers the common addressing schemes used in the wild:

  * SegmentTemplate with $Number$ (+ startNumber / duration / timescale)
  * SegmentTemplate with a SegmentTimeline (<S t= d= r=>)
  * SegmentTemplate with $Time$
  * SegmentList (explicit <SegmentURL>)
  * SegmentBase (single file addressed by byte ranges — progressive-ish)

DASH nearly always separates video and audio into distinct files, so muxing
(ffmpeg) is required to produce a single playable MP4. Callers should fall back
gracefully when ffmpeg is unavailable.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from urllib.parse import urljoin


@dataclass
class DashRepr:
    id: str
    mime: str = ""
    codecs: str = ""
    bandwidth: int = 0
    width: int = 0
    height: int = 0
    audio_rate: int = 0
    init_url: str = ""
    segment_urls: list[str] = field(default_factory=list)
    # For SegmentBase (single-file) representations:
    media_url: str = ""
    init_range: str = ""       # "start-end" byte range for the init section

    @property
    def is_audio(self) -> bool:
        return self.mime.startswith("audio") or (not self.mime and self.audio_rate > 0)

    @property
    def is_video(self) -> bool:
        return self.mime.startswith("video") or self.height > 0

    def label(self) -> str:
        if self.height:
            return f"{self.height}p"
        if self.audio_rate:
            return f"{self.audio_rate // 1000}kHz"
        if self.bandwidth:
            return f"{self.bandwidth // 1000}kbps"
        return self.id or "stream"


@dataclass
class DashManifest:
    video: list[DashRepr] = field(default_factory=list)
    audio: list[DashRepr] = field(default_factory=list)
    duration: float = 0.0  # mediaPresentationDuration in seconds

    def best_video(self) -> DashRepr | None:
        return max(self.video, key=lambda r: (r.height, r.bandwidth)) if self.video else None

    def best_audio(self) -> DashRepr | None:
        return max(self.audio, key=lambda r: r.bandwidth) if self.audio else None


def _localname(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def _find(el: ET.Element, name: str) -> ET.Element | None:
    for child in el:
        if _localname(child.tag) == name:
            return child
    return None


def _iter(el: ET.Element, name: str) -> list[ET.Element]:
    return [c for c in el if _localname(c.tag) == name]


def _base_url(el: ET.Element, parent_base: str) -> str:
    """Resolve <BaseURL> children against the inherited base."""
    b = _find(el, "BaseURL")
    if b is not None and (b.text or "").strip():
        return urljoin(parent_base, b.text.strip())
    return parent_base


def _fill_template(tmpl: str, repr_id: str, bandwidth: int, number: int | None = None, time: int | None = None) -> str:
    out = tmpl.replace("$RepresentationID$", repr_id).replace("$Bandwidth$", str(bandwidth))

    def _num(m: re.Match) -> str:
        fmt = m.group(1)
        if number is None:
            return m.group(0)
        if fmt:
            return format(number, fmt.lstrip("%"))
        return str(number)

    out = re.sub(r"\$Number(%[0-9]*d)?\$", _num, out)

    def _time(m: re.Match) -> str:
        if time is None:
            return m.group(0)
        fmt = m.group(1)
        if fmt:
            return format(time, fmt.lstrip("%"))
        return str(time)

    out = re.sub(r"\$Time(%[0-9]*d)?\$", _time, out)
    out = out.replace("$$", "$")
    return out


def _expand_timeline(timeline: ET.Element) -> list[int]:
    """Return the list of $Time$ start values from a SegmentTimeline."""
    times: list[int] = []
    current = 0
    first = True
    for s in _iter(timeline, "S"):
        t = s.get("t")
        d = int(s.get("d", "0"))
        r = int(s.get("r", "0"))
        if t is not None:
            current = int(t)
        elif first:
            current = 0
        first = False
        for _ in range(r + 1):
            times.append(current)
            current += d
    return times


def _count_timeline(timeline: ET.Element) -> int:
    return sum(int(s.get("r", "0")) + 1 for s in _iter(timeline, "S"))


def _segment_template(
    tmpl_el: ET.Element,
    repr_id: str,
    bandwidth: int,
    base: str,
    media_duration: float,
) -> tuple[str, list[str]]:
    """Expand a SegmentTemplate into (init_url, [segment_urls])."""
    init_tmpl = tmpl_el.get("initialization", "")
    media_tmpl = tmpl_el.get("media", "")
    start_number = int(tmpl_el.get("startNumber", "1"))
    timescale = int(tmpl_el.get("timescale", "1"))
    seg_duration = tmpl_el.get("duration")

    init_url = urljoin(base, _fill_template(init_tmpl, repr_id, bandwidth)) if init_tmpl else ""

    segs: list[str] = []
    timeline = _find(tmpl_el, "SegmentTimeline")
    if timeline is not None:
        if "$Time$" in media_tmpl:
            for t in _expand_timeline(timeline):
                segs.append(urljoin(base, _fill_template(media_tmpl, repr_id, bandwidth, time=t)))
        else:
            count = _count_timeline(timeline)
            for i in range(count):
                segs.append(
                    urljoin(base, _fill_template(media_tmpl, repr_id, bandwidth, number=start_number + i))
                )
    elif seg_duration is not None and media_duration > 0:
        seg_dur_s = float(seg_duration) / timescale
        if seg_dur_s > 0:
            count = int(media_duration / seg_dur_s + 0.999)
            for i in range(count):
                segs.append(
                    urljoin(base, _fill_template(media_tmpl, repr_id, bandwidth, number=start_number + i))
                )
    return init_url, segs


def _parse_duration(dur: str | None) -> float:
    """Parse an ISO-8601 duration like PT1H2M3.5S into seconds."""
    if not dur:
        return 0.0
    m = re.match(
        r"P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:([\d.]+)S)?",
        dur,
    )
    if not m:
        return 0.0
    _y, _mo, d, h, mi, s = m.groups()
    total = 0.0
    total += float(d or 0) * 86400
    total += float(h or 0) * 3600
    total += float(mi or 0) * 60
    total += float(s or 0)
    return total


def parse(text: str, base_url: str) -> DashManifest:
    manifest = DashManifest()
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return manifest

    media_duration = _parse_duration(root.get("mediaPresentationDuration"))
    manifest.duration = media_duration
    mpd_base = _base_url(root, base_url)

    for period in _iter(root, "Period"):
        period_base = _base_url(period, mpd_base)
        for aset in _iter(period, "AdaptationSet"):
            aset_base = _base_url(aset, period_base)
            aset_mime = aset.get("mimeType", "")
            aset_tmpl = _find(aset, "SegmentTemplate")
            for rep in _iter(aset, "Representation"):
                rep_base = _base_url(rep, aset_base)
                mime = rep.get("mimeType", aset_mime)
                r = DashRepr(
                    id=rep.get("id", ""),
                    mime=mime,
                    codecs=rep.get("codecs", aset.get("codecs", "")),
                    bandwidth=int(rep.get("bandwidth", "0") or 0),
                    width=int(rep.get("width", aset.get("maxWidth", "0")) or 0),
                    height=int(rep.get("height", aset.get("maxHeight", "0")) or 0),
                    audio_rate=int(rep.get("audioSamplingRate", aset.get("audioSamplingRate", "0")) or 0),
                )

                rep_tmpl = _find(rep, "SegmentTemplate") or aset_tmpl
                seg_list = _find(rep, "SegmentList")
                seg_base = _find(rep, "SegmentBase")

                if rep_tmpl is not None:
                    r.init_url, r.segment_urls = _segment_template(
                        rep_tmpl, r.id, r.bandwidth, rep_base, media_duration
                    )
                elif seg_list is not None:
                    init = _find(seg_list, "Initialization")
                    if init is not None and init.get("sourceURL"):
                        r.init_url = urljoin(rep_base, init.get("sourceURL"))
                    for su in _iter(seg_list, "SegmentURL"):
                        media = su.get("media")
                        if media:
                            r.segment_urls.append(urljoin(rep_base, media))
                elif seg_base is not None:
                    # Single-file representation; addressable by byte ranges.
                    r.media_url = rep_base
                    init = _find(seg_base, "Initialization")
                    if init is not None and init.get("range"):
                        r.init_range = init.get("range")
                else:
                    # Bare BaseURL = whole file is one segment (progressive).
                    r.media_url = rep_base

                if r.is_video:
                    manifest.video.append(r)
                elif r.is_audio:
                    manifest.audio.append(r)
                elif mime.startswith("video"):
                    manifest.video.append(r)
    return manifest
