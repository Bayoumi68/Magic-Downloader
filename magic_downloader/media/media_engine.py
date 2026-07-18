"""Download engine for streaming media (HLS / DASH).

Turns a manifest URL into a single playable file by:
  1. Fetching + parsing the manifest, choosing the best video (+ audio) track.
  2. Downloading every segment in parallel (multi-connection),
     decrypting AES-128 HLS segments on the fly.
  3. Assembling: concatenate each track's segments, then mux video+audio into
     an MP4 with ffmpeg. Without ffmpeg it degrades gracefully to a raw
     concatenated file and flags that ffmpeg is needed for a clean MP4.

Progress is measured by completed segments (segment sizes are unknown up
front); byte counters still drive the live speed readout.
"""

from __future__ import annotations

import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import requests

from magic_downloader.media import dash, ffmpeg, hls
from magic_downloader.media.detect import MediaKind
from magic_downloader.models import DownloadJob, DownloadStatus

ProgressCallback = Callable[[DownloadJob], None]
CancelCheck = Callable[[], bool]


@dataclass
class _Seg:
    index: int
    url: str
    key: hls.HlsKey | None = None
    seq: int = 0
    byte_length: int | None = None
    byte_offset: int | None = None


@dataclass
class _Track:
    kind: str                    # "video" | "audio"
    segments: list[_Seg] = field(default_factory=list)
    init_url: str = ""
    init_range: str = ""
    is_fmp4: bool = False
    ext: str = ".ts"             # container hint for the assembled track file
    out_path: Path | None = None


class MediaProcessingError(RuntimeError):
    pass


class MediaDownloadEngine:
    """Downloads an HLS/DASH job into a single file."""

    def __init__(
        self,
        job: DownloadJob,
        user_agent: str,
        media_kind: MediaKind,
        chunk_size: int = 256 * 1024,
        on_progress: ProgressCallback | None = None,
        cancel_check: CancelCheck | None = None,
        pause_event: threading.Event | None = None,
        max_workers: int = 8,
        rate_limiter=None,
    ) -> None:
        self.job = job
        self.user_agent = user_agent
        self.media_kind = media_kind
        self.chunk_size = chunk_size
        self.on_progress = on_progress
        self.cancel_check = cancel_check or (lambda: False)
        self.rate_limiter = rate_limiter
        self.pause_event = pause_event or threading.Event()
        self.pause_event.set()
        self.max_workers = max(1, min(16, max_workers))
        self._lock = threading.Lock()
        self._stop = False
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": user_agent})
        if job.cookie:
            self._session.headers["Cookie"] = job.cookie
        if job.referrer:
            self._session.headers["Referer"] = job.referrer
        if job.extra_headers:
            for k, v in job.extra_headers.items():
                if k and v:
                    self._session.headers[str(k)] = str(v)
        self._key_cache: dict[str, bytes] = {}
        self._speed_window: list[tuple[float, int]] = []
        self._seg_done = 0
        self._seg_total = 0

    # ── lifecycle ───────────────────────────────────────────────────────
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

    def _wait_if_paused(self) -> bool:
        while not self.pause_event.is_set():
            if self._cancelled():
                return False
            time.sleep(0.15)
        return not self._cancelled()

    def _update_speed(self, n_bytes: int) -> None:
        now = time.monotonic()
        with self._lock:
            self.job.tick_active()
            self._speed_window.append((now, n_bytes))
            cutoff = now - 3.0
            self._speed_window = [(t, b) for t, b in self._speed_window if t >= cutoff]
            total = sum(b for _, b in self._speed_window)
            if self._speed_window:
                dt = max(0.001, now - self._speed_window[0][0])
                self.job.speed_bps = total / dt

    def _set_progress_meta(self) -> None:
        with self._lock:
            self.job.media_meta["seg_total"] = self._seg_total
            self.job.media_meta["seg_done"] = self._seg_done

    # ── main flow ───────────────────────────────────────────────────────
    def run(self) -> None:
        try:
            self.job.status = DownloadStatus.CONNECTING
            self.job.error = ""
            self._emit()
            tracks = self._plan()
            if self._cancelled():
                self.job.status = DownloadStatus.CANCELLED
                self._emit()
                return
            if not tracks:
                raise MediaProcessingError("No downloadable media found in manifest")

            self._seg_total = sum(len(t.segments) for t in tracks)
            self._set_progress_meta()
            self.job.status = DownloadStatus.DOWNLOADING
            if self.job.started_at is None:
                self.job.started_at = time.time()
            self.job.tick_active()   # start the Avg-speed clock (see engine.run)
            self._emit()

            tmp_dir = self._tmp_dir()
            tmp_dir.mkdir(parents=True, exist_ok=True)

            for track in tracks:
                if self._cancelled():
                    break
                self._download_track(track, tmp_dir)

            if self._cancelled():
                if self.job.status != DownloadStatus.PAUSED:
                    self.job.status = DownloadStatus.CANCELLED
                self.job.speed_bps = 0.0
                self._emit()
                return
            if self.job.status == DownloadStatus.PAUSED:
                self.job.speed_bps = 0.0
                self._emit()
                return

            # ── assemble ──
            self.job.status = DownloadStatus.PROCESSING
            self.job.speed_bps = 0.0
            self._emit()
            self._assemble(tracks)

            self.job.status = DownloadStatus.COMPLETE
            self.job.finished_at = time.time()
            self.job.error = ""
            final = Path(self.job.save_path)
            if final.exists():
                self.job.total_size = final.stat().st_size
                self.job.downloaded = self.job.total_size
            self._cleanup(tmp_dir)
            self._emit()
        except Exception as exc:  # noqa: BLE001 — surface to UI
            self.job.status = DownloadStatus.FAILED
            self.job.error = str(exc)
            self.job.speed_bps = 0.0
            self._emit()

    # ── planning ────────────────────────────────────────────────────────
    def _get_text(self, url: str) -> str:
        r = self._session.get(url, timeout=30, allow_redirects=True)
        r.raise_for_status()
        return r.text

    def _plan(self) -> list[_Track]:
        if self.media_kind == MediaKind.HLS:
            return self._plan_hls()
        if self.media_kind == MediaKind.DASH:
            return self._plan_dash()
        raise MediaProcessingError(f"Unsupported media kind: {self.media_kind}")

    def _plan_hls(self) -> list[_Track]:
        text = self._get_text(self.job.url)
        base = self.job.url
        chosen_height = int(self.job.media_meta.get("height") or 0)

        audio_track: _Track | None = None
        if hls.is_master(text):
            master = hls.parse_master(text, base)
            variant = None
            if chosen_height:
                for v in master.variants:
                    if v.height == chosen_height:
                        variant = v
                        break
            variant = variant or master.best_variant()
            if not variant:
                raise MediaProcessingError("HLS master has no variants")
            self.job.media_meta["quality"] = variant.label()
            self.job.media_meta["variants"] = [
                {"label": v.label(), "height": v.height, "bandwidth": v.bandwidth}
                for v in master.variants
            ]
            # Alternate audio rendition (video-only variant needs it muxed in).
            media_text = self._get_text(variant.url)
            media_pl = hls.parse_media(media_text, variant.url)
            video_track = self._track_from_hls("video", media_pl)

            audio_media = master.audio_for(variant.audio_group) if variant.audio_group else None
            if audio_media and audio_media.uri:
                a_text = self._get_text(audio_media.uri)
                a_pl = hls.parse_media(a_text, audio_media.uri)
                # Only treat as a separate track if it actually has its own segments.
                if a_pl.segments:
                    audio_track = self._track_from_hls("audio", a_pl)
            self._estimate_size(variant.bandwidth, media_pl.total_duration)
        else:
            media_pl = hls.parse_media(text, base)
            video_track = self._track_from_hls("video", media_pl)

        tracks = [video_track]
        if audio_track:
            tracks.append(audio_track)
        return tracks

    def _track_from_hls(self, kind: str, pl: hls.HlsMediaPlaylist) -> _Track:
        track = _Track(kind=kind, is_fmp4=pl.is_fmp4, init_url=pl.map_uri)
        track.ext = ".mp4" if pl.is_fmp4 else ".ts"
        for i, s in enumerate(pl.segments):
            track.segments.append(
                _Seg(
                    index=i,
                    url=s.url,
                    key=s.key,
                    seq=s.seq,
                    byte_length=s.byte_length,
                    byte_offset=s.byte_offset,
                )
            )
        return track

    def _plan_dash(self) -> list[_Track]:
        text = self._get_text(self.job.url)
        manifest = dash.parse(text, self.job.url)
        chosen_height = int(self.job.media_meta.get("height") or 0)

        video = None
        if chosen_height:
            for v in manifest.video:
                if v.height == chosen_height:
                    video = v
                    break
        video = video or manifest.best_video()
        audio = manifest.best_audio()
        if not video and not audio:
            raise MediaProcessingError("DASH manifest has no video/audio representations")

        self.job.media_meta["variants"] = [
            {"label": v.label(), "height": v.height, "bandwidth": v.bandwidth}
            for v in manifest.video
        ]
        tracks: list[_Track] = []
        if video:
            self.job.media_meta["quality"] = video.label()
            tracks.append(self._track_from_dash("video", video))
        if audio:
            tracks.append(self._track_from_dash("audio", audio))
        bw = (video.bandwidth if video else 0) + (audio.bandwidth if audio else 0)
        # DASH duration estimate lives in the representation's segment count; use
        # the manifest-level estimate stored during planning if available.
        self._estimate_size(bw, float(self.job.media_meta.get("duration") or 0))
        return tracks

    def _track_from_dash(self, kind: str, rep: dash.DashRepr) -> _Track:
        track = _Track(kind=kind, is_fmp4=True, init_url=rep.init_url, init_range=rep.init_range)
        track.ext = ".mp4" if kind == "video" else ".m4a"
        if rep.segment_urls:
            for i, u in enumerate(rep.segment_urls):
                track.segments.append(_Seg(index=i, url=u))
        elif rep.media_url:
            # Single-file representation: one "segment" = whole file.
            track.segments.append(_Seg(index=0, url=rep.media_url))
            track.init_url = ""  # init is inside the file (SegmentBase)
        return track

    def _estimate_size(self, bandwidth_bps: int, duration_s: float) -> None:
        if bandwidth_bps > 0 and duration_s > 0:
            est = int(bandwidth_bps / 8 * duration_s)
            if est > 0:
                self.job.total_size = est

    # ── downloading ─────────────────────────────────────────────────────
    def _download_track(self, track: _Track, tmp_dir: Path) -> None:
        track_dir = tmp_dir / track.kind
        track_dir.mkdir(parents=True, exist_ok=True)

        # Init segment first (fMP4).
        if track.init_url:
            init_path = track_dir / "init.seg"
            if not init_path.exists() or init_path.stat().st_size == 0:
                data = self._fetch_bytes(track.init_url)
                init_path.write_bytes(data)
            track_meta_init = init_path
        else:
            track_meta_init = None
        track.out_path = self._concat_target(track, track_dir)
        track._init_path = track_meta_init  # type: ignore[attr-defined]

        pending = [
            s for s in track.segments
            if not (track_dir / f"{s.index:06d}.seg").exists()
            or (track_dir / f"{s.index:06d}.seg").stat().st_size == 0
        ]
        # Count already-present segments toward progress on resume.
        with self._lock:
            self._seg_done += len(track.segments) - len(pending)
        self._set_progress_meta()

        errors: list[str] = []

        def worker(seg: _Seg) -> None:
            if self._cancelled() or self.job.status == DownloadStatus.PAUSED:
                return
            if not self._wait_if_paused():
                return
            seg_path = track_dir / f"{seg.index:06d}.seg"
            try:
                data = self._fetch_segment(seg)
                seg_path.write_bytes(data)
                with self._lock:
                    self._seg_done += 1
                self._update_speed(len(data))
                if self.rate_limiter is not None:
                    self.rate_limiter.throttle(len(data))
                self._set_progress_meta()
                self._emit()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"segment {seg.index}: {exc}")

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = [pool.submit(worker, s) for s in pending]
            for fut in as_completed(futures):
                fut.result()

        if self._cancelled() or self.job.status == DownloadStatus.PAUSED:
            return
        if errors:
            raise MediaProcessingError(f"{len(errors)} segment(s) failed; first: {errors[0]}")

    def _fetch_bytes(self, url: str, byte_range: str | None = None) -> bytes:
        headers = {}
        if byte_range:
            headers["Range"] = f"bytes={byte_range}"
        last_exc: Exception | None = None
        for attempt in range(3):
            if self._cancelled():
                raise MediaProcessingError("cancelled")
            try:
                r = self._session.get(url, headers=headers, timeout=60, stream=True)
                if r.status_code not in (200, 206):
                    raise MediaProcessingError(f"HTTP {r.status_code}")
                return r.content
            except requests.RequestException as exc:
                last_exc = exc
                time.sleep(0.6 * (attempt + 1))
        raise MediaProcessingError(str(last_exc) if last_exc else "request failed")

    def _fetch_segment(self, seg: _Seg) -> bytes:
        byte_range = None
        if seg.byte_length is not None:
            offset = seg.byte_offset if seg.byte_offset is not None else 0
            byte_range = f"{offset}-{offset + seg.byte_length - 1}"
        data = self._fetch_bytes(seg.url, byte_range)
        if seg.key and seg.key.method == "AES-128":
            data = self._decrypt_aes128(data, seg)
        return data

    def _decrypt_aes128(self, data: bytes, seg: _Seg) -> bytes:
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        except ImportError as exc:
            raise MediaProcessingError(
                "This stream is AES-128 encrypted. Install 'cryptography' "
                "(pip install cryptography) to download it."
            ) from exc
        key = self._get_key(seg.key.uri)
        iv = self._iv_for(seg)
        decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
        out = decryptor.update(data) + decryptor.finalize()
        return _pkcs7_unpad(out)

    def _get_key(self, uri: str) -> bytes:
        with self._lock:
            cached = self._key_cache.get(uri)
        if cached is not None:
            return cached
        key = self._fetch_bytes(uri)
        if len(key) != 16:
            raise MediaProcessingError(f"AES key must be 16 bytes, got {len(key)}")
        with self._lock:
            self._key_cache[uri] = key
        return key

    def _iv_for(self, seg: _Seg) -> bytes:
        if seg.key and seg.key.iv:
            hexs = seg.key.iv.lower()
            if hexs.startswith("0x"):
                hexs = hexs[2:]
            iv = bytes.fromhex(hexs.rjust(32, "0"))
            return iv[:16].rjust(16, b"\x00")
        # Default IV = media sequence number, 128-bit big-endian.
        return struct.pack(">QQ", 0, seg.seq)

    # ── assembly ────────────────────────────────────────────────────────
    def _concat_target(self, track: _Track, track_dir: Path) -> Path:
        return track_dir / f"track{track.ext}"

    def _want_ts_output(self) -> bool:
        """User asked to save streams as raw .ts (classic-DM style) not .mp4."""
        try:
            from magic_downloader import config
            return bool(config.load_settings().get("stream_output_ts", False))
        except Exception:  # noqa: BLE001 — a bad settings file must not break a download
            return False

    def _assemble(self, tracks: list[_Track]) -> None:
        final = Path(self.job.save_path)
        final.parent.mkdir(parents=True, exist_ok=True)

        has_ff = ffmpeg.has_ffmpeg()
        has_video = any(t.kind == "video" for t in tracks)
        # Raw-.ts output is opt-in and only meaningful for (remuxable) video.
        want_ts = has_ff and has_video and self._want_ts_output()

        if want_ts:
            if final.suffix.lower() != ".ts":
                final = final.with_suffix(".ts")
        elif final.suffix.lower() not in (".mp4", ".m4a", ".mkv", ".webm", ".ts"):
            final = final.with_suffix(".mp4")
        if str(final) != self.job.save_path:
            self.job.save_path = str(final)
            self.job.filename = final.name

        # Build each track's concatenated file.
        for track in tracks:
            self._concat_track(track)

        video = next((t for t in tracks if t.kind == "video"), None)
        audio = next((t for t in tracks if t.kind == "audio"), None)

        if has_ff:
            self._assemble_ffmpeg(final, video, audio, as_ts=want_ts)
        else:
            self._assemble_fallback(final, video, audio, tracks)

    def _concat_track(self, track: _Track) -> None:
        """Byte-concatenate a track's segments (+ init) into track.out_path."""
        track_dir = track.out_path.parent
        parts: list[Path] = []
        init_path = getattr(track, "_init_path", None)
        if init_path is not None:
            parts.append(init_path)
        for seg in track.segments:
            seg_path = track_dir / f"{seg.index:06d}.seg"
            if seg_path.exists():
                parts.append(seg_path)
        with open(track.out_path, "wb") as out:
            for p in parts:
                with open(p, "rb") as f:
                    while True:
                        buf = f.read(1024 * 1024)
                        if not buf:
                            break
                        out.write(buf)

    def _assemble_ffmpeg(self, final: Path, video: _Track | None, audio: _Track | None,
                         *, as_ts: bool = False) -> None:
        inputs: list[str] = []
        if video and video.out_path:
            inputs.append(str(video.out_path))
        if audio and audio.out_path:
            inputs.append(str(audio.out_path))
        if not inputs:
            raise MediaProcessingError("Nothing to assemble")
        try:
            if as_ts:
                ffmpeg.mux_to_ts(inputs, final, copy=True)
            else:
                ffmpeg.mux_to_mp4(inputs, final, copy=True)
        except RuntimeError as exc:
            raise MediaProcessingError(f"ffmpeg failed: {exc}") from exc

    def _assemble_fallback(
        self,
        final: Path,
        video: _Track | None,
        audio: _Track | None,
        tracks: list[_Track],
    ) -> None:
        """No ffmpeg: deliver the best single-file result we can."""
        if video and audio:
            # Can't mux without ffmpeg. Keep both files next to the target and
            # flag it, delivering the video file as the primary output.
            primary = video
            note = (
                "Saved video and audio separately — install ffmpeg for a merged "
                "MP4 (video+audio in one file)."
            )
            self._deliver_single(final, primary)
            # Copy the audio next to it so nothing is lost.
            audio_out = final.with_name(final.stem + ".audio" + (audio.ext or ".m4a"))
            if audio.out_path and audio.out_path.exists():
                audio.out_path.replace(audio_out)
            self.job.media_meta["ffmpeg_note"] = note
            self.job.error = ""
        else:
            track = video or audio
            if not track:
                raise MediaProcessingError("Nothing to assemble")
            # For raw MPEG-TS, a .ts file plays fine; adjust extension.
            if track.ext == ".ts" and final.suffix.lower() == ".mp4":
                final = final.with_suffix(".ts")
                self.job.save_path = str(final)
                self.job.filename = final.name
                self.job.media_meta["ffmpeg_note"] = (
                    "Saved as .ts (install ffmpeg to get a clean .mp4)."
                )
            self._deliver_single(final, track)

    def _deliver_single(self, final: Path, track: _Track) -> None:
        if track.out_path and track.out_path.exists():
            if final.exists():
                final.unlink()
            track.out_path.replace(final)

    # ── temp management ─────────────────────────────────────────────────
    def _tmp_dir(self) -> Path:
        return Path(self.job.save_path + f".mdtmp-{self.job.id}")

    def _cleanup(self, tmp_dir: Path) -> None:
        import shutil
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except OSError:
            pass


def _pkcs7_unpad(data: bytes) -> bytes:
    if not data:
        return data
    pad = data[-1]
    if 1 <= pad <= 16 and len(data) >= pad and data[-pad:] == bytes([pad]) * pad:
        return data[:-pad]
    return data
