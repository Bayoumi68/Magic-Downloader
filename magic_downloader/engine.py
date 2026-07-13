"""Multi-segment HTTP download engine (IDM-style)."""

from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlparse

import requests

from magic_downloader.models import DownloadJob, DownloadStatus, SegmentState

ProgressCallback = Callable[[DownloadJob], None]
CancelCheck = Callable[[], bool]


class DownloadEngine:
    """Downloads a single job with multiple parallel connections."""

    def __init__(
        self,
        job: DownloadJob,
        user_agent: str,
        chunk_size: int = 256 * 1024,
        on_progress: ProgressCallback | None = None,
        cancel_check: CancelCheck | None = None,
        pause_event: threading.Event | None = None,
        rate_limiter=None,
        timeout: int = 60,
    ) -> None:
        self.job = job
        self.user_agent = user_agent
        self.chunk_size = chunk_size
        self.on_progress = on_progress
        self.cancel_check = cancel_check or (lambda: False)
        self.rate_limiter = rate_limiter
        self.timeout = max(5, int(timeout or 60))
        self.pause_event = pause_event or threading.Event()
        self.pause_event.set()  # set = running; clear = paused
        self._lock = threading.Lock()
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": user_agent})
        # Browser-captured session cookies / referrer
        if job.cookie:
            self._session.headers["Cookie"] = job.cookie
        if job.referrer:
            self._session.headers["Referer"] = job.referrer
        if job.extra_headers:
            for k, v in job.extra_headers.items():
                if k and v:
                    self._session.headers[str(k)] = str(v)
        self._speed_window: list[tuple[float, int]] = []
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def pause(self) -> None:
        self.pause_event.clear()

    def resume(self) -> None:
        self.pause_event.set()

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        h = {"User-Agent": self.user_agent}
        if self.job.cookie:
            h["Cookie"] = self.job.cookie
        if self.job.referrer:
            h["Referer"] = self.job.referrer
        if self.job.extra_headers:
            h.update({str(k): str(v) for k, v in self.job.extra_headers.items() if k and v})
        if extra:
            h.update(extra)
        return h

    def _emit(self) -> None:
        if self.on_progress:
            self.on_progress(self.job)

    def _wait_if_paused(self) -> bool:
        """Return False if cancelled while waiting."""
        while not self.pause_event.is_set():
            if self._stop or self.cancel_check():
                return False
            time.sleep(0.15)
        return not (self._stop or self.cancel_check())

    def _account(self, n_bytes: int) -> None:
        """Record throughput for the speed readout and apply the global cap."""
        self._update_speed(n_bytes)
        if self.rate_limiter is not None:
            self.rate_limiter.throttle(n_bytes)

    def _update_speed(self, n_bytes: int) -> None:
        now = time.monotonic()
        with self._lock:
            self._speed_window.append((now, n_bytes))
            # Keep ~3 seconds of samples
            cutoff = now - 3.0
            self._speed_window = [(t, b) for t, b in self._speed_window if t >= cutoff]
            total = sum(b for _, b in self._speed_window)
            if self._speed_window:
                dt = max(0.001, now - self._speed_window[0][0])
                self.job.speed_bps = total / dt

    def probe(self) -> None:
        """HEAD/GET to learn size, filename, range support."""
        self.job.status = DownloadStatus.CONNECTING
        self._emit()

        url = self.job.url
        filename = self.job.filename

        # Try HEAD first
        try:
            r = self._session.head(url, allow_redirects=True, timeout=30)
            if r.status_code >= 400 or not r.headers.get("Content-Length"):
                r = self._session.get(url, stream=True, allow_redirects=True, timeout=30)
                r.close()
        except requests.RequestException:
            r = self._session.get(url, stream=True, allow_redirects=True, timeout=30)
            r.close()

        final_url = str(r.url)
        self.job.url = final_url

        cl = r.headers.get("Content-Length")
        if cl and cl.isdigit():
            self.job.total_size = int(cl)

        accept = (r.headers.get("Accept-Ranges") or "").lower()
        # Some servers omit Accept-Ranges but still honor Range
        self.job.supports_ranges = accept != "none"

        self.job.etag = r.headers.get("ETag") or ""
        self.job.last_modified = r.headers.get("Last-Modified") or ""

        cd = r.headers.get("Content-Disposition") or ""
        guessed = _filename_from_content_disposition(cd)
        if not guessed:
            guessed = _filename_from_url(final_url)
        if guessed and (not filename or filename == "download"):
            filename = guessed
            self.job.filename = filename
            # Update save path directory + new name if path was placeholder
            parent = Path(self.job.save_path).parent
            self.job.save_path = str(parent / filename)

        # Verify range support with a tiny range request when size known
        if self.job.total_size > 1:
            try:
                tr = self._session.get(
                    self.job.url,
                    headers=self._headers({"Range": "bytes=0-0"}),
                    timeout=20,
                    stream=True,
                )
                if tr.status_code in (206, 200) and tr.status_code == 206:
                    self.job.supports_ranges = True
                elif tr.status_code == 200 and not accept:
                    # Server ignored Range
                    self.job.supports_ranges = False
                tr.close()
            except requests.RequestException:
                pass

        self._emit()

    def run(self) -> None:
        try:
            if self.job.total_size <= 0 or not self.job.segments:
                self.probe()
            if self._stop or self.cancel_check():
                self.job.status = DownloadStatus.CANCELLED
                self._emit()
                return

            self.job.status = DownloadStatus.DOWNLOADING
            if self.job.started_at is None:
                self.job.started_at = time.time()
            self._emit()

            Path(self.job.save_path).parent.mkdir(parents=True, exist_ok=True)

            if (
                self.job.supports_ranges
                and self.job.total_size > 0
                and self.job.connections > 1
            ):
                self._run_multipart()
            else:
                self._run_single()

            if self._stop or self.cancel_check():
                if self.job.status != DownloadStatus.PAUSED:
                    self.job.status = DownloadStatus.CANCELLED
            elif self.job.status == DownloadStatus.PAUSED:
                pass
            elif self.job.total_size > 0 and self.job.downloaded >= self.job.total_size:
                self._finalize()
            elif self.job.total_size == 0 and self.job.downloaded > 0:
                # Unknown size single-stream completed
                self._finalize()
            elif self.job.status == DownloadStatus.DOWNLOADING:
                # Partial without pause flag → treat as failed if incomplete
                if self.job.total_size > 0 and self.job.downloaded < self.job.total_size:
                    self.job.status = DownloadStatus.FAILED
                    self.job.error = "Download incomplete"
                else:
                    self._finalize()

            self.job.speed_bps = 0.0
            self._emit()
        except Exception as exc:  # noqa: BLE001 — surface to UI
            self.job.status = DownloadStatus.FAILED
            self.job.error = str(exc)
            self.job.speed_bps = 0.0
            self._emit()

    def _finalize(self) -> None:
        part = Path(self.job.save_path + ".part")
        final = Path(self.job.save_path)
        if part.exists():
            if final.exists():
                final.unlink()
            part.rename(final)
        self.job.status = DownloadStatus.COMPLETE
        self.job.finished_at = time.time()
        self.job.error = ""
        if self.job.total_size <= 0 and final.exists():
            self.job.total_size = final.stat().st_size
            self.job.downloaded = self.job.total_size

    def _ensure_part_file(self, size: int) -> Path:
        part = Path(self.job.save_path + ".part")
        if not part.exists() or part.stat().st_size != size:
            # Pre-allocate sparse-ish empty file
            with open(part, "wb") as f:
                if size > 0:
                    f.truncate(size)
        return part

    def _build_segments(self) -> list[SegmentState]:
        if self.job.segments and sum(s.downloaded for s in self.job.segments) > 0:
            return self.job.segments

        n = max(1, min(self.job.connections, 32))
        total = self.job.total_size
        if total <= 0:
            return []

        # Prefer fewer connections for small files
        if total < 2 * 1024 * 1024:
            n = 1
        elif total < 10 * 1024 * 1024:
            n = min(n, 4)

        part = total // n
        segments: list[SegmentState] = []
        for i in range(n):
            start = i * part
            end = total - 1 if i == n - 1 else (start + part - 1)
            segments.append(SegmentState(index=i, start=start, end=end, downloaded=0))
        self.job.segments = segments
        return segments

    def _run_multipart(self) -> None:
        segments = self._build_segments()
        part_path = self._ensure_part_file(self.job.total_size)
        self.job.downloaded = sum(s.downloaded for s in segments)
        self._emit()

        pending = [s for s in segments if s.remaining > 0]
        if not pending:
            return

        workers = min(len(pending), self.job.connections)

        def worker(seg: SegmentState) -> None:
            self._download_segment(part_path, seg)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(worker, s) for s in pending]
            for fut in as_completed(futures):
                fut.result()  # re-raise worker errors

        self.job.downloaded = sum(s.downloaded for s in self.job.segments)

    def _download_segment(self, part_path: Path, seg: SegmentState) -> None:
        while seg.remaining > 0:
            if not self._wait_if_paused():
                self.job.status = DownloadStatus.CANCELLED
                return
            if self.job.status == DownloadStatus.PAUSED:
                return

            current = seg.start + seg.downloaded
            end = seg.end
            headers = self._headers({"Range": f"bytes={current}-{end}"})
            try:
                with self._session.get(
                    self.job.url,
                    headers=headers,
                    stream=True,
                    timeout=self.timeout,
                ) as resp:
                    if resp.status_code not in (200, 206):
                        raise RuntimeError(f"HTTP {resp.status_code} for segment {seg.index}")
                    with open(part_path, "r+b") as f:
                        f.seek(current)
                        for chunk in resp.iter_content(chunk_size=self.chunk_size):
                            if not chunk:
                                continue
                            if not self._wait_if_paused():
                                self.job.status = DownloadStatus.CANCELLED
                                return
                            if self.job.status == DownloadStatus.PAUSED:
                                return
                            f.write(chunk)
                            n = len(chunk)
                            with self._lock:
                                seg.downloaded += n
                                self.job.downloaded = sum(s.downloaded for s in self.job.segments)
                            self._account(n)
                            self._emit()
            except requests.RequestException as exc:
                # Retry segment after brief wait
                time.sleep(1.0)
                if self._stop or self.cancel_check():
                    return
                # One more attempt then fail
                try:
                    with self._session.get(
                        self.job.url,
                        headers=headers,
                        stream=True,
                        timeout=self.timeout,
                    ) as resp:
                        if resp.status_code not in (200, 206):
                            raise RuntimeError(str(exc))
                        with open(part_path, "r+b") as f:
                            f.seek(seg.start + seg.downloaded)
                            for chunk in resp.iter_content(chunk_size=self.chunk_size):
                                if not chunk:
                                    continue
                                if self._stop or self.cancel_check() or self.job.status == DownloadStatus.PAUSED:
                                    return
                                f.write(chunk)
                                n = len(chunk)
                                with self._lock:
                                    seg.downloaded += n
                                    self.job.downloaded = sum(s.downloaded for s in self.job.segments)
                                self._account(n)
                                self._emit()
                except Exception as exc2:  # noqa: BLE001
                    raise RuntimeError(f"Segment {seg.index} failed: {exc2}") from exc2

    def _run_single(self) -> None:
        part_path = Path(self.job.save_path + ".part")
        mode = "ab" if part_path.exists() and self.job.downloaded > 0 else "wb"
        headers = self._headers()
        if mode == "ab" and self.job.downloaded > 0 and self.job.supports_ranges:
            headers["Range"] = f"bytes={self.job.downloaded}-"

        with self._session.get(self.job.url, headers=headers, stream=True, timeout=self.timeout) as resp:
            if resp.status_code == 200 and mode == "ab":
                # Server ignored resume — restart
                mode = "wb"
                self.job.downloaded = 0
            elif resp.status_code not in (200, 206):
                raise RuntimeError(f"HTTP {resp.status_code}")

            cl = resp.headers.get("Content-Length")
            if self.job.total_size <= 0 and cl and cl.isdigit():
                if resp.status_code == 206:
                    # Content-Length is remaining; try parse Content-Range
                    cr = resp.headers.get("Content-Range", "")
                    m = re.match(r"bytes\s+\d+-\d+/(\d+)", cr)
                    if m:
                        self.job.total_size = int(m.group(1))
                else:
                    self.job.total_size = int(cl)

            with open(part_path, mode) as f:
                for chunk in resp.iter_content(chunk_size=self.chunk_size):
                    if not chunk:
                        continue
                    if not self._wait_if_paused():
                        self.job.status = DownloadStatus.CANCELLED
                        return
                    if self.job.status == DownloadStatus.PAUSED:
                        return
                    f.write(chunk)
                    n = len(chunk)
                    with self._lock:
                        self.job.downloaded += n
                    self._account(n)
                    self._emit()


def _filename_from_url(url: str) -> str:
    path = urlparse(url).path
    name = unquote(path.rstrip("/").split("/")[-1] if path else "")
    if not name or name in (".", ".."):
        return "download"
    # Strip query-like junk sometimes left in path
    name = name.split("?")[0]
    return name or "download"


def _filename_from_content_disposition(cd: str) -> str | None:
    if not cd:
        return None
    # filename*=UTF-8''...
    m = re.search(r"filename\*\s*=\s*UTF-8''([^;]+)", cd, re.I)
    if m:
        return unquote(m.group(1).strip().strip('"'))
    m = re.search(r'filename\s*=\s*"([^"]+)"', cd, re.I)
    if m:
        return m.group(1)
    m = re.search(r"filename\s*=\s*([^;]+)", cd, re.I)
    if m:
        return m.group(1).strip().strip('"')
    return None


def suggest_filename(url: str) -> str:
    return _filename_from_url(url)
