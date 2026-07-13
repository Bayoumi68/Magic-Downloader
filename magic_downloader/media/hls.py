"""Minimal but practical HLS (.m3u8) parser.

Supports what real-world video sites use:
  * Master playlists  → pick the best (or a chosen) variant by bandwidth
  * Media playlists    → ordered segment list with durations
  * EXT-X-KEY          → AES-128 segment encryption (key + IV)
  * EXT-X-MAP          → fMP4 initialization segment
  * EXT-X-BYTERANGE    → byte-range segments
  * Alternate audio    → EXT-X-MEDIA TYPE=AUDIO groups

No third-party dependency — parsing is line based per the HLS spec (RFC 8216).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin


@dataclass
class HlsKey:
    method: str = "NONE"          # NONE | AES-128 | SAMPLE-AES
    uri: str = ""
    iv: str = ""                  # hex string, optional


@dataclass
class HlsSegment:
    url: str
    duration: float = 0.0
    key: HlsKey | None = None
    byte_length: int | None = None
    byte_offset: int | None = None
    seq: int = 0                  # media sequence number (for default AES IV)


@dataclass
class HlsMedia:
    """EXT-X-MEDIA entry (alternate audio/subtitle rendition)."""
    type: str
    group_id: str
    name: str = ""
    uri: str = ""
    default: bool = False
    language: str = ""


@dataclass
class HlsVariant:
    url: str
    bandwidth: int = 0
    resolution: str = ""
    codecs: str = ""
    frame_rate: str = ""
    audio_group: str = ""

    @property
    def height(self) -> int:
        m = re.search(r"\d+x(\d+)", self.resolution)
        return int(m.group(1)) if m else 0

    def label(self) -> str:
        if self.height:
            q = f"{self.height}p"
        elif self.bandwidth:
            q = f"{self.bandwidth // 1000}kbps"
        else:
            q = "stream"
        return q


@dataclass
class HlsMediaPlaylist:
    segments: list[HlsSegment] = field(default_factory=list)
    map_uri: str = ""             # fMP4 init segment
    map_key: HlsKey | None = None
    is_fmp4: bool = False
    target_duration: float = 0.0

    @property
    def total_duration(self) -> float:
        return sum(s.duration for s in self.segments)


@dataclass
class HlsMaster:
    variants: list[HlsVariant] = field(default_factory=list)
    audio: list[HlsMedia] = field(default_factory=list)

    def best_variant(self) -> HlsVariant | None:
        if not self.variants:
            return None
        return max(self.variants, key=lambda v: (v.height, v.bandwidth))

    def audio_for(self, group_id: str) -> HlsMedia | None:
        cands = [m for m in self.audio if m.group_id == group_id and m.uri]
        if not cands:
            return None
        for m in cands:
            if m.default:
                return m
        return cands[0]


_ATTR_RE = re.compile(r'([A-Z0-9\-]+)=("[^"]*"|[^,]*)')


def _parse_attrs(line: str) -> dict[str, str]:
    """Parse an ``KEY=VALUE,KEY="val"`` attribute list."""
    attrs: dict[str, str] = {}
    for m in _ATTR_RE.finditer(line):
        key = m.group(1)
        val = m.group(2)
        if val.startswith('"') and val.endswith('"'):
            val = val[1:-1]
        attrs[key] = val
    return attrs


def is_master(text: str) -> bool:
    return "#EXT-X-STREAM-INF" in text


def parse_master(text: str, base_url: str) -> HlsMaster:
    master = HlsMaster()
    lines = [ln.strip() for ln in text.splitlines()]
    pending: dict[str, str] | None = None
    for ln in lines:
        if not ln:
            continue
        if ln.startswith("#EXT-X-MEDIA:"):
            a = _parse_attrs(ln[len("#EXT-X-MEDIA:"):])
            if a.get("TYPE") == "AUDIO":
                master.audio.append(
                    HlsMedia(
                        type="AUDIO",
                        group_id=a.get("GROUP-ID", ""),
                        name=a.get("NAME", ""),
                        uri=urljoin(base_url, a["URI"]) if a.get("URI") else "",
                        default=a.get("DEFAULT", "").upper() == "YES",
                        language=a.get("LANGUAGE", ""),
                    )
                )
        elif ln.startswith("#EXT-X-STREAM-INF:"):
            pending = _parse_attrs(ln[len("#EXT-X-STREAM-INF:"):])
        elif not ln.startswith("#"):
            if pending is not None:
                res = pending.get("RESOLUTION", "")
                master.variants.append(
                    HlsVariant(
                        url=urljoin(base_url, ln),
                        bandwidth=int(pending.get("BANDWIDTH", pending.get("AVERAGE-BANDWIDTH", "0")) or 0),
                        resolution=res,
                        codecs=pending.get("CODECS", ""),
                        frame_rate=pending.get("FRAME-RATE", ""),
                        audio_group=pending.get("AUDIO", ""),
                    )
                )
                pending = None
    return master


def parse_media(text: str, base_url: str) -> HlsMediaPlaylist:
    pl = HlsMediaPlaylist()
    lines = [ln.strip() for ln in text.splitlines()]
    cur_key: HlsKey | None = None
    next_dur = 0.0
    next_byterange: tuple[int, int | None] | None = None
    media_seq = 0
    seq = 0
    for ln in lines:
        if not ln:
            continue
        if ln.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            try:
                media_seq = int(ln.split(":", 1)[1].strip())
                seq = media_seq
            except ValueError:
                pass
        elif ln.startswith("#EXT-X-TARGETDURATION:"):
            try:
                pl.target_duration = float(ln.split(":", 1)[1].strip())
            except ValueError:
                pass
        elif ln.startswith("#EXT-X-KEY:"):
            a = _parse_attrs(ln[len("#EXT-X-KEY:"):])
            method = a.get("METHOD", "NONE")
            if method == "NONE":
                cur_key = None
            else:
                cur_key = HlsKey(
                    method=method,
                    uri=urljoin(base_url, a["URI"]) if a.get("URI") else "",
                    iv=a.get("IV", ""),
                )
        elif ln.startswith("#EXT-X-MAP:"):
            a = _parse_attrs(ln[len("#EXT-X-MAP:"):])
            if a.get("URI"):
                pl.map_uri = urljoin(base_url, a["URI"])
                pl.map_key = cur_key
                pl.is_fmp4 = True
        elif ln.startswith("#EXTINF:"):
            val = ln[len("#EXTINF:"):].split(",", 1)[0].strip()
            try:
                next_dur = float(val)
            except ValueError:
                next_dur = 0.0
        elif ln.startswith("#EXT-X-BYTERANGE:"):
            spec = ln.split(":", 1)[1].strip()
            if "@" in spec:
                length_s, off_s = spec.split("@", 1)
                next_byterange = (int(length_s), int(off_s))
            else:
                next_byterange = (int(spec), None)
        elif not ln.startswith("#"):
            seg = HlsSegment(
                url=urljoin(base_url, ln),
                duration=next_dur,
                key=cur_key,
                seq=seq,
            )
            if next_byterange:
                seg.byte_length, seg.byte_offset = next_byterange[0], next_byterange[1]
            pl.segments.append(seg)
            # fMP4 detection also via file extension
            if ln.lower().split("?")[0].endswith(".m4s") or ".mp4" in ln.lower():
                pl.is_fmp4 = True
            next_dur = 0.0
            next_byterange = None
            seq += 1
    return pl
