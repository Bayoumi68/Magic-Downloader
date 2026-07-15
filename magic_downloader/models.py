"""Download job data model."""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class DownloadStatus(str, Enum):
    QUEUED = "Queued"
    CONNECTING = "Connecting"
    DOWNLOADING = "Downloading"
    PROCESSING = "Processing"  # merging/muxing streamed segments (HLS/DASH)
    PAUSED = "Paused"
    COMPLETE = "Complete"
    FAILED = "Failed"
    CANCELLED = "Cancelled"


@dataclass
class SegmentState:
    index: int
    start: int
    end: int  # inclusive
    downloaded: int = 0

    @property
    def remaining(self) -> int:
        length = self.end - self.start + 1
        return max(0, length - self.downloaded)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SegmentState:
        return cls(
            index=int(data["index"]),
            start=int(data["start"]),
            end=int(data["end"]),
            downloaded=int(data.get("downloaded", 0)),
        )


@dataclass
class DownloadJob:
    url: str
    save_path: str
    filename: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: DownloadStatus = DownloadStatus.QUEUED
    total_size: int = 0  # 0 = unknown
    downloaded: int = 0
    connections: int = 8
    supports_ranges: bool = True
    error: str = ""
    category: str = "General"
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    speed_bps: float = 0.0
    # Seconds this job actually spent transferring, summed across every run.
    # NOT the same as finished_at - started_at: started_at is stamped once and
    # never reset, so wall-clock elapsed counts pauses, stalls and overnight
    # gaps as download time and makes a resumed job look glacially slow.
    active_seconds: float = 0.0
    segments: list[SegmentState] = field(default_factory=list)
    etag: str = ""
    last_modified: str = ""
    # Captured from browser extension (authenticated downloads)
    referrer: str = ""
    cookie: str = ""
    extra_headers: dict[str, str] = field(default_factory=dict)
    source: str = "manual"  # manual | browser | clipboard
    # Streaming media (video grabber)
    media_type: str = "http"  # http | hls | dash
    media_meta: dict[str, Any] = field(default_factory=dict)

    # A burst of bytes further apart than this means the transfer wasn't running
    # (paused, stalled, waiting on a server, or the app was restarted), so the
    # gap isn't counted as download time.
    ACTIVE_GAP_MAX = 5.0

    def __post_init__(self) -> None:
        # Per-run marker for tick_active(); deliberately not a field, so it's
        # never persisted or copied.
        self._tick: float | None = None

    def tick_active(self) -> None:
        """Call whenever bytes arrive: accumulates real transferring time.

        Several connections deliver bytes at once, so this measures the gap
        since the previous burst from *any* connection — which approximates
        wall-clock time spent transferring, not the sum over connections.
        """
        now = time.monotonic()
        last, self._tick = self._tick, now
        if last is not None:
            gap = now - last
            if 0.0 < gap <= self.ACTIVE_GAP_MAX:
                self.active_seconds += gap

    @property
    def is_stream(self) -> bool:
        return self.media_type in ("hls", "dash")

    @property
    def avg_speed_bps(self) -> float:
        """Average throughput over the time actually spent transferring.

        0.0 (rendered "—") only when there is genuinely no timing to report:
        a job that never ran, or one downloaded by a version before this was
        tracked. Any real transfer records time, however brief.
        """
        if self.active_seconds <= 0.0 or self.downloaded <= 0:
            return 0.0
        return self.downloaded / self.active_seconds

    @property
    def progress(self) -> float:
        # For streams, size is unknown up front — measure by segments done.
        if self.is_stream:
            total = int(self.media_meta.get("seg_total") or 0)
            done = int(self.media_meta.get("seg_done") or 0)
            if total > 0:
                return min(100.0, (done / total) * 100.0)
            return 0.0
        if self.total_size <= 0:
            return 0.0
        return min(100.0, (self.downloaded / self.total_size) * 100.0)

    @property
    def eta_seconds(self) -> float | None:
        if self.speed_bps <= 0:
            return None
        if self.is_stream:
            total = int(self.media_meta.get("seg_total") or 0)
            done = int(self.media_meta.get("seg_done") or 0)
            if total <= 0 or done <= 0:
                return None
            # Estimate remaining bytes from average bytes/segment so far.
            avg = self.downloaded / done if done else 0
            remaining_segs = max(0, total - done)
            remaining_bytes = avg * remaining_segs
            return remaining_bytes / self.speed_bps if remaining_bytes else None
        if self.total_size <= 0:
            return None
        remaining = max(0, self.total_size - self.downloaded)
        return remaining / self.speed_bps

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "url": self.url,
            "save_path": self.save_path,
            "filename": self.filename,
            "status": self.status.value,
            "total_size": self.total_size,
            "downloaded": self.downloaded,
            "connections": self.connections,
            "supports_ranges": self.supports_ranges,
            "error": self.error,
            "category": self.category,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "active_seconds": self.active_seconds,
            "etag": self.etag,
            "last_modified": self.last_modified,
            "referrer": self.referrer,
            "cookie": self.cookie,
            "extra_headers": self.extra_headers,
            "source": self.source,
            "media_type": self.media_type,
            "media_meta": self.media_meta,
            "segments": [s.to_dict() for s in self.segments],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DownloadJob:
        status_raw = data.get("status", DownloadStatus.QUEUED.value)
        try:
            status = DownloadStatus(status_raw)
        except ValueError:
            status = DownloadStatus.QUEUED
        # Incomplete jobs restore as paused so user can resume
        if status in (
            DownloadStatus.DOWNLOADING,
            DownloadStatus.CONNECTING,
            DownloadStatus.PROCESSING,
            DownloadStatus.QUEUED,
        ):
            media_meta = data.get("media_meta") or {}
            has_progress = data.get("downloaded", 0) > 0 or int(media_meta.get("seg_done") or 0) > 0
            if has_progress:
                status = DownloadStatus.PAUSED
            else:
                status = DownloadStatus.QUEUED
        segments = [SegmentState.from_dict(s) for s in data.get("segments") or []]
        return cls(
            id=data.get("id") or uuid.uuid4().hex[:12],
            url=data["url"],
            save_path=data["save_path"],
            filename=data.get("filename") or "download",
            status=status,
            total_size=int(data.get("total_size") or 0),
            downloaded=int(data.get("downloaded") or 0),
            connections=int(data.get("connections") or 8),
            supports_ranges=bool(data.get("supports_ranges", True)),
            error=data.get("error") or "",
            category=data.get("category") or "General",
            created_at=float(data.get("created_at") or time.time()),
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
            active_seconds=float(data.get("active_seconds") or 0.0),
            etag=data.get("etag") or "",
            last_modified=data.get("last_modified") or "",
            referrer=data.get("referrer") or "",
            cookie=data.get("cookie") or "",
            extra_headers=dict(data.get("extra_headers") or {}),
            source=data.get("source") or "manual",
            media_type=data.get("media_type") or "http",
            media_meta=dict(data.get("media_meta") or {}),
            segments=segments,
        )


def format_bytes(n: int | float) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024.0:
            return f"{n:3.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024.0
    return f"{n:.1f} PB"


def format_speed(bps: float) -> str:
    if bps <= 0:
        return "—"
    return f"{format_bytes(bps)}/s"


def format_eta(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "—"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"
