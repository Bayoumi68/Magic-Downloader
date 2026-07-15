"""Download queue orchestration (pause / resume / concurrent limits)."""

from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path
from typing import Callable

from magic_downloader.config import category_for_filename, load_settings, resolve_save_path, save_settings
from magic_downloader.engine import (
    DownloadEngine,
    looks_like_junk_name,
    resolve_download_name,
    suggest_filename,
)
from magic_downloader.media.detect import MediaKind, detect_kind
from magic_downloader.media.media_engine import MediaDownloadEngine
from magic_downloader.media.ytdlp_engine import YtdlpEngine
from magic_downloader.models import DownloadJob, DownloadStatus
from magic_downloader.ratelimit import RateLimiter
from magic_downloader.storage import load_jobs, save_jobs

Listener = Callable[[], None]

# Statuses that occupy a running slot (network download or post-processing).
BUSY_STATUSES = (
    DownloadStatus.DOWNLOADING,
    DownloadStatus.CONNECTING,
    DownloadStatus.PROCESSING,
)


class DownloadManager:
    def __init__(self) -> None:
        self.settings = load_settings()
        # Collapse any historical duplicates that point to the exact same file
        # (accumulated before Overwrite replaced entries in place).
        self.jobs: list[DownloadJob] = _dedupe_jobs_by_path(load_jobs())
        self._engines: dict[str, DownloadEngine] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._pause_events: dict[str, threading.Event] = {}
        self._lock = threading.RLock()
        self._listeners: list[Listener] = []
        self._persist_timer: float = 0.0
        self._last_progress_notify: float = 0.0
        self._stop_scheduler = False
        self.rate_limiter = RateLimiter(self._speed_cap_bps())
        self._apply_ffmpeg_path()
        self._scheduler = threading.Thread(target=self._schedule_loop, daemon=True)
        self._scheduler.start()

    def _speed_cap_bps(self) -> float:
        try:
            return max(0.0, float(self.settings.get("max_speed_kbps") or 0) * 1024.0)
        except (TypeError, ValueError):
            return 0.0

    def _apply_ffmpeg_path(self) -> None:
        path = str(self.settings.get("ffmpeg_path") or "").strip()
        if path:
            from magic_downloader.media import ffmpeg as ff

            ff.reset_cache()
            ff.find_ffmpeg(extra_hint=path)

    def add_listener(self, fn: Listener) -> None:
        self._listeners.append(fn)

    def _notify(self) -> None:
        for fn in list(self._listeners):
            try:
                fn()
            except Exception:
                pass

    def _persist(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._persist_timer < 1.0:
            return
        self._persist_timer = now
        with self._lock:
            save_jobs(self.jobs)

    def save_settings(self) -> None:
        save_settings(self.settings)
        self.rate_limiter.set_rate(self._speed_cap_bps())
        self._apply_ffmpeg_path()
        self._notify()

    def get_job(self, job_id: str) -> DownloadJob | None:
        with self._lock:
            for j in self.jobs:
                if j.id == job_id:
                    return j
        return None

    def add_job(self, job: DownloadJob, start: bool = True) -> DownloadJob:
        with self._lock:
            # Replace any existing entry that targets the SAME file path — e.g.
            # the user chose "Overwrite" — so the list never shows two rows for
            # one file. ("Keep both" uses a different path, so it's preserved.)
            for d in [j for j in self.jobs if j.save_path == job.save_path and j.id != job.id]:
                eng = self._engines.pop(d.id, None)
                if eng:
                    try:
                        eng.stop()
                    except Exception:
                        pass
                self._threads.pop(d.id, None)
                self._pause_events.pop(d.id, None)
            self.jobs = [j for j in self.jobs if j.save_path != job.save_path or j.id == job.id]
            self.jobs.insert(0, job)
            self._persist(force=True)
        self._notify()
        if start:
            self.start_job(job.id)
        else:
            self._kick_queue()
        return job

    def suggest_capture(self, data: dict) -> dict:
        """Compute a suggested job spec from an extension payload — NO side
        effects. Used both by :meth:`add_from_browser` and by the 
        capture dialog (which lets the user edit before committing).
        """
        url = str(data.get("url") or "").strip()
        media_kind = detect_kind(data)
        media_type = media_kind.value
        is_stream = media_type in ("hls", "dash", "page")
        audio_only = bool(data.get("audio_only"))
        title = str(data.get("title") or "").strip()

        cookie = str(data.get("cookie") or data.get("cookies") or "")
        referrer = str(data.get("referrer") or data.get("referer") or data.get("page_url") or "")
        extra = data.get("headers") or data.get("extra_headers") or {}
        if not isinstance(extra, dict):
            extra = {}

        raw_name = str(data.get("filename") or "").strip()
        probe_size = 0
        if is_stream:
            base = _strip_ext(title or raw_name or "video")
            name = f"{base}.{'m4a' if audio_only else 'mp4'}"
        else:
            name = raw_name or suggest_filename(url)
            # Junk name (GUID / no extension)? Ask the server for the real one.
            if looks_like_junk_name(name):
                try:
                    resolved, size = resolve_download_name(
                        url, cookie=cookie, referrer=referrer,
                        user_agent=str(self.settings.get("user_agent") or ""),
                    )
                    if resolved and not looks_like_junk_name(resolved):
                        name = resolved
                    if size:
                        probe_size = size
                except Exception:  # noqa: BLE001 — best-effort
                    pass
        for ch in '<>:"/\\|?*':
            name = name.replace(ch, "_")
        name = name.strip() or "download"

        if is_stream:
            category = "Music" if audio_only else "Video"
        else:
            category = str(data.get("category") or category_for_filename(name, self.settings))
        folder = str(Path(resolve_save_path(self.settings, name, category)).parent)

        connections = max(1, min(32, int(data.get("connections") or self.settings.get("connections") or 8)))

        media_meta: dict = {}
        if is_stream:
            for key in ("height", "duration"):
                if data.get(key):
                    try:
                        media_meta[key] = int(float(data[key]))
                    except (TypeError, ValueError):
                        pass
            if title:
                media_meta["title"] = title
            if data.get("page_url"):
                media_meta["page_url"] = str(data["page_url"])
            if data.get("quality"):
                media_meta["quality"] = str(data["quality"])
            if data.get("format_id"):
                media_meta["format_id"] = str(data["format_id"])
            if audio_only:
                media_meta["audio_only"] = True
            if not audio_only and not media_meta.get("format_id") and not media_meta.get("height"):
                dq = str(self.settings.get("default_video_quality") or "best")
                if dq.isdigit():
                    media_meta["height"] = int(dq)

        return {
            "url": url,
            "filename": name,
            "category": category,
            "folder": folder,
            "connections": connections,
            "media_type": media_type,
            "media_meta": media_meta,
            "cookie": cookie,
            "referrer": referrer,
            "extra_headers": {str(k): str(v) for k, v in extra.items()},
            "is_stream": is_stream,
            "audio_only": audio_only,
            "title": title,
            "size": probe_size,
        }

    def add_capture_confirmed(
        self,
        *,
        url: str,
        filename: str,
        folder: str,
        category: str,
        connections: int = 8,
        media_type: str = "http",
        media_meta: dict | None = None,
        cookie: str = "",
        referrer: str = "",
        extra_headers: dict | None = None,
        source: str = "browser",
        start: bool = True,
        overwrite: bool = False,
    ) -> dict:
        """Create + queue a job from finalized (possibly user-edited) values.

        ``overwrite=True`` uses the given name as-is (replacing any existing
        file); otherwise a colliding name is auto-versioned with "(1)", "(2)"…
        """
        folder_p = Path(folder or self.settings.get("default_save_path") or ".")
        folder_p.mkdir(parents=True, exist_ok=True)
        name = filename.strip() or "download"
        for ch in '<>:"/\\|?*':
            name = name.replace(ch, "_")
        if overwrite:
            save_path = folder_p / name
            # Fresh start: drop any stale partial so it doesn't try to resume.
            Path(str(save_path) + ".part").unlink(missing_ok=True)
        else:
            save_path = _dedupe_path(folder_p / name)

        job = DownloadJob(
            url=url,
            save_path=str(save_path),
            filename=save_path.name,
            connections=max(1, min(32, int(connections or 8))),
            category=category or "General",
            referrer=referrer,
            cookie=cookie,
            extra_headers=extra_headers or {},
            source=source,
            media_type=media_type or "http",
            media_meta=dict(media_meta or {}),
        )
        self.add_job(job, start=start)
        return {
            "id": job.id,
            "filename": job.filename,
            "save_path": job.save_path,
            "status": job.status.value,
            "media_type": job.media_type,
        }

    def add_from_browser(self, data: dict) -> dict:
        """Create a job from a browser extension payload (no user prompt)."""
        sug = self.suggest_capture(data)
        start = data.get("start")
        if start is None:
            start = bool(self.settings.get("browser_auto_start", True))
        return self.add_capture_confirmed(
            url=sug["url"], filename=sug["filename"], folder=sug["folder"],
            category=sug["category"], connections=sug["connections"],
            media_type=sug["media_type"], media_meta=sug["media_meta"],
            cookie=sug["cookie"], referrer=sug["referrer"],
            extra_headers=sug["extra_headers"], start=bool(start),
        )

    def add_video_job(
        self,
        url: str,
        media_type: str,
        sel: dict | None = None,
        title: str = "",
        folder: str | None = None,
        start: bool = True,
        category: str | None = None,
    ) -> DownloadJob:
        """Create a video job from the in-app quality picker."""
        sel = sel or {}
        audio_only = bool(sel.get("audio_only"))
        base = _strip_ext(title or suggest_filename(url) or "video")
        for ch in '<>:"/\\|?*':
            base = base.replace(ch, "_")
        base = base.strip() or "video"
        name = f"{base}.{'m4a' if audio_only else 'mp4'}"
        category = category or ("Music" if audio_only else "Video")

        if folder:
            save_dir = Path(folder)
        else:
            save_dir = Path(resolve_save_path(self.settings, name, category)).parent
        save_dir.mkdir(parents=True, exist_ok=True)
        save_path = _dedupe_path(save_dir / name)

        media_meta: dict = {}
        if sel.get("format_id"):
            media_meta["format_id"] = str(sel["format_id"])
        if sel.get("height"):
            try:
                media_meta["height"] = int(sel["height"])
            except (TypeError, ValueError):
                pass
        if audio_only:
            media_meta["audio_only"] = True
        if title:
            media_meta["title"] = title

        job = DownloadJob(
            url=url,
            save_path=str(save_path),
            filename=save_path.name,
            connections=int(self.settings.get("connections") or 8),
            category=category,
            media_type=media_type,
            media_meta=media_meta,
            source="manual",
        )
        self.add_job(job, start=start)
        return job

    def add_category(self, name: str, folder: str | None = None) -> str:
        """Create a new download category (name + folder) and persist it."""
        name = (name or "").strip()
        if not name:
            return ""
        cats = self.settings.setdefault("category_paths", {})
        if name not in cats:
            base = folder or str(Path(self.settings.get("default_save_path") or ".") / name)
            cats[name] = base
            self.settings.setdefault("category_extensions", {}).setdefault(name, [])
            try:
                Path(base).mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
            self.save_settings()
        return name

    #: Categories that ship with the app and can't be deleted.
    BUILTIN_CATEGORIES = frozenset({"General", "Compressed", "Documents", "Music", "Video"})

    def remove_category(self, name: str) -> bool:
        """Delete a user-created category (mapping only — files are untouched).
        Built-in categories can't be removed. Returns True on success."""
        name = (name or "").strip()
        if not name or name in self.BUILTIN_CATEGORIES:
            return False
        cats = self.settings.get("category_paths") or {}
        if name not in cats:
            return False
        cats.pop(name, None)
        (self.settings.get("category_extensions") or {}).pop(name, None)
        self.save_settings()
        self._notify()
        return True

    def set_job_quality(self, job_id: str, sel: dict, title: str = "") -> None:
        """Change a job's chosen quality/format and re-download it."""
        with self._lock:
            job = self.get_job(job_id)
            if not job:
                return
            sel = sel or {}
            job.media_meta.pop("format_id", None)
            job.media_meta.pop("height", None)
            job.media_meta.pop("audio_only", None)
            if sel.get("format_id"):
                job.media_meta["format_id"] = str(sel["format_id"])
            if sel.get("height"):
                job.media_meta["height"] = int(sel["height"])
            if sel.get("audio_only"):
                job.media_meta["audio_only"] = True
            job.downloaded = 0
            job.total_size = 0
            job.error = ""
            job.status = DownloadStatus.QUEUED
            self._persist(force=True)
        self.start_job(job_id)

    def probe_video(self, url: str, media_type: str = "", cookie: str = "", referrer: str = "") -> dict:
        """Return available qualities/formats for a page or streaming URL.

        Shape: {kind, title?, duration?, formats:[{label, format_id?, height?,
        ext?, filesize?, audio_only?, needs_ffmpeg?}]}. Used by the in-app
        quality picker. Runs network calls — call it off the UI thread.
        """
        mtype = str(media_type or "").lower()
        if not mtype:
            # A bare web page → let yt-dlp handle it; only manifests use the
            # built-in stream engine.
            kind = detect_kind({"url": url})
            mtype = kind.value if kind.value in ("hls", "dash") else "page"
        ua = str(self.settings.get("user_agent") or "")
        if mtype == "page":
            from magic_downloader.media.ytdlp_engine import probe_formats

            return probe_formats(url=url, cookie=cookie, user_agent=ua, referrer=referrer)
        if mtype in ("hls", "dash"):
            from magic_downloader.media.probe import probe_media

            res = probe_media(url=url, media_type=mtype, cookie=cookie, referrer=referrer, user_agent=ua)
            # Normalise HLS/DASH variants into the picker's format shape.
            res["formats"] = [
                {"label": v.get("label") or f"{v.get('height')}p", "height": v.get("height", 0),
                 "ext": v.get("ext", "mp4"), "filesize": v.get("filesize", 0),
                 "approx": v.get("approx", False), "fps": v.get("fps", ""),
                 "audio_only": False, "needs_ffmpeg": False}
                for v in res.get("variants", []) if v.get("height")
            ]
            return res
        return {"kind": mtype, "formats": []}

    def status_snapshot(self) -> dict:
        with self._lock:
            active = sum(1 for j in self.jobs if j.status in BUSY_STATUSES)
            return {
                "total": len(self.jobs),
                "active": active,
                "complete": sum(1 for j in self.jobs if j.status == DownloadStatus.COMPLETE),
                "queued": sum(1 for j in self.jobs if j.status == DownloadStatus.QUEUED),
            }

    def start_job(self, job_id: str) -> None:
        with self._lock:
            job = self.get_job(job_id)
            if not job:
                return
            if job.status == DownloadStatus.COMPLETE:
                return
            if job_id in self._threads and self._threads[job_id].is_alive():
                # Resume paused engine
                job.status = DownloadStatus.DOWNLOADING
                job.error = ""
                pe = self._pause_events.get(job_id)
                if pe:
                    pe.set()
                eng = self._engines.get(job_id)
                if eng:
                    eng.resume()
                self._persist(force=True)
                self._notify()
                return

            active = sum(1 for j in self.jobs if j.status in BUSY_STATUSES)
            max_sim = int(self.settings.get("max_simultaneous") or 3)
            if active >= max_sim and job.status != DownloadStatus.DOWNLOADING:
                job.status = DownloadStatus.QUEUED
                job.error = ""
                self._persist(force=True)
                self._notify()
                return

            job.status = DownloadStatus.QUEUED
            job.error = ""
            pe = threading.Event()
            pe.set()
            self._pause_events[job_id] = pe

            if job.media_type == "page":
                engine = YtdlpEngine(
                    job=job,
                    user_agent=self.settings.get("user_agent", ""),
                    on_progress=self._on_progress,
                    cancel_check=lambda jid=job_id: self._is_cancelled(jid),
                    pause_event=pe,
                    max_speed_bps=self._speed_cap_bps(),
                )
            elif job.media_type in ("hls", "dash"):
                engine = MediaDownloadEngine(
                    job=job,
                    user_agent=self.settings.get("user_agent", ""),
                    media_kind=MediaKind(job.media_type),
                    chunk_size=int(self.settings.get("chunk_size") or 256 * 1024),
                    on_progress=self._on_progress,
                    cancel_check=lambda jid=job_id: self._is_cancelled(jid),
                    pause_event=pe,
                    max_workers=int(self.settings.get("media_workers") or self.settings.get("connections") or 8),
                    rate_limiter=self.rate_limiter,
                )
            else:
                engine = DownloadEngine(
                    job=job,
                    user_agent=self.settings.get("user_agent", ""),
                    chunk_size=int(self.settings.get("chunk_size") or 256 * 1024),
                    on_progress=self._on_progress,
                    cancel_check=lambda jid=job_id: self._is_cancelled(jid),
                    pause_event=pe,
                    rate_limiter=self.rate_limiter,
                    timeout=int(self.settings.get("timeout") or 60),
                )
            self._engines[job_id] = engine

            def runner(jid: str = job_id, eng: DownloadEngine = engine) -> None:
                eng.run()
                with self._lock:
                    self._threads.pop(jid, None)
                    self._engines.pop(jid, None)
                    self._pause_events.pop(jid, None)
                    self._persist(force=True)
                self._notify()
                self._kick_queue()

            t = threading.Thread(target=runner, name=f"dl-{job_id}", daemon=True)
            self._threads[job_id] = t
            t.start()
        self._notify()

    def _is_cancelled(self, job_id: str) -> bool:
        job = self.get_job(job_id)
        if not job:
            return True
        return job.status == DownloadStatus.CANCELLED

    def _on_progress(self, job: DownloadJob) -> None:
        # Called from download worker threads on EVERY chunk (per connection).
        # Persist is already debounced; throttle the UI notify too, otherwise a
        # multi-connection download fires thousands of cross-thread GUI refreshes
        # per second and locks up the whole machine. ~8/sec is plenty smooth
        # (the GUI also self-refreshes on a 400ms timer).
        self._persist(force=False)
        now = time.monotonic()
        if now - self._last_progress_notify >= 0.12:
            self._last_progress_notify = now
            self._notify()

    def pause_job(self, job_id: str) -> None:
        with self._lock:
            job = self.get_job(job_id)
            if not job:
                return
            if job.status not in (
                DownloadStatus.DOWNLOADING,
                DownloadStatus.CONNECTING,
                DownloadStatus.QUEUED,
            ):
                return
            job.status = DownloadStatus.PAUSED
            pe = self._pause_events.get(job_id)
            if pe:
                pe.clear()
            eng = self._engines.get(job_id)
            if eng:
                eng.pause()
            self._persist(force=True)
        self._notify()
        self._kick_queue()

    def cancel_job(self, job_id: str) -> None:
        with self._lock:
            job = self.get_job(job_id)
            if not job:
                return
            job.status = DownloadStatus.CANCELLED
            eng = self._engines.get(job_id)
            if eng:
                eng.stop()
            pe = self._pause_events.get(job_id)
            if pe:
                pe.set()  # unblock waiters so they can exit
            self._persist(force=True)
        self._notify()
        self._kick_queue()

    def delete_job(self, job_id: str, delete_files: bool = False) -> None:
        self.cancel_job(job_id)
        with self._lock:
            job = self.get_job(job_id)
            if not job:
                return
            if delete_files:
                for p in (job.save_path, job.save_path + ".part"):
                    try:
                        Path(p).unlink(missing_ok=True)
                    except OSError:
                        pass
            self.jobs = [j for j in self.jobs if j.id != job_id]
            self._persist(force=True)
        self._notify()

    def retry_job(self, job_id: str) -> None:
        with self._lock:
            job = self.get_job(job_id)
            if not job:
                return
            if job.status in (DownloadStatus.FAILED, DownloadStatus.CANCELLED, DownloadStatus.PAUSED):
                # Keep partial progress for resume
                if job.status != DownloadStatus.PAUSED:
                    # cancel/fail: allow resume from .part if present
                    pass
                job.error = ""
                job.status = DownloadStatus.QUEUED
                self._persist(force=True)
        self.start_job(job_id)

    # ── file operations on a job (right-click menu) ──────────────────

    @staticmethod
    def _sanitize_name(name: str) -> str:
        name = (name or "").strip()
        for ch in '<>:"/\\|?*':
            name = name.replace(ch, "_")
        return name.strip()

    def rename_job(self, job_id: str, new_name: str) -> tuple[bool, str]:
        """Rename a job's file on disk (and its ``.part``) and update the job.
        Returns (ok, error_message)."""
        new_name = self._sanitize_name(new_name)
        if not new_name:
            return False, "Enter a file name."
        with self._lock:
            job = self.get_job(job_id)
            if not job:
                return False, "Download not found."
            if job.status in (DownloadStatus.DOWNLOADING, DownloadStatus.CONNECTING,
                              DownloadStatus.PROCESSING):
                return False, "Pause the download before renaming."
            old = Path(job.save_path)
            new = old.with_name(new_name)
            if new == old:
                return True, ""
            if new.exists() or Path(str(new) + ".part").exists():
                return False, f"“{new_name}” already exists in this folder."
            try:
                if old.exists():
                    old.rename(new)
                old_part = Path(str(old) + ".part")
                if old_part.exists():
                    old_part.rename(Path(str(new) + ".part"))
            except OSError as exc:
                return False, str(exc)
            job.save_path = str(new)
            job.filename = new.name
            self._persist(force=True)
        self._notify()
        return True, ""

    def move_job(self, job_id: str, new_folder: str) -> tuple[bool, str]:
        """Move a job's file (and its ``.part``) to *new_folder*."""
        with self._lock:
            job = self.get_job(job_id)
            if not job:
                return False, "Download not found."
            if job.status in (DownloadStatus.DOWNLOADING, DownloadStatus.CONNECTING,
                              DownloadStatus.PROCESSING):
                return False, "Pause the download before moving."
            dest_dir = Path(new_folder)
            try:
                dest_dir.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                return False, str(exc)
            old = Path(job.save_path)
            new = dest_dir / old.name
            if new == old:
                return True, ""
            if new.exists() or Path(str(new) + ".part").exists():
                return False, f"A file named “{old.name}” already exists there."
            try:
                # shutil.move (NOT Path.replace): replace is a rename and fails
                # with "cannot move to a different disk drive" when the target
                # folder is on another volume. shutil.move falls back to
                # copy+delete across drives.
                if old.exists():
                    shutil.move(str(old), str(new))
                old_part = Path(str(old) + ".part")
                if old_part.exists():
                    shutil.move(str(old_part), str(new) + ".part")
            except (OSError, shutil.Error) as exc:
                return False, str(exc)
            job.save_path = str(new)
            self._persist(force=True)
        self._notify()
        return True, ""

    def move_to_category(self, job_id: str, category: str) -> tuple[bool, str]:
        """Move a job's file into *category*'s folder and re-tag the job."""
        category = (category or "").strip()
        if not category:
            return False, "Choose a category."
        folder = (self.settings.get("category_paths") or {}).get(category)
        if not folder:
            folder = str(Path(self.settings.get("default_save_path") or ".") / category)
        ok, err = self.move_job(job_id, folder)   # moves file + updates save_path
        if not ok:
            return False, err
        with self._lock:
            job = self.get_job(job_id)
            if job:
                job.category = category
                self._persist(force=True)
        self._notify()
        return True, ""

    def redownload_job(self, job_id: str) -> None:
        """Discard progress and download the job again from scratch."""
        self.cancel_job(job_id)  # stop any running engine first
        with self._lock:
            job = self.get_job(job_id)
            if not job:
                return
            Path(str(job.save_path) + ".part").unlink(missing_ok=True)
            job.downloaded = 0
            job.total_size = 0
            job.segments = []
            job.speed_bps = 0.0
            job.error = ""
            if isinstance(job.media_meta, dict):
                job.media_meta.pop("seg_done", None)
            job.status = DownloadStatus.QUEUED
            self._persist(force=True)
        self.start_job(job_id)

    def _kick_queue(self) -> None:
        with self._lock:
            max_sim = int(self.settings.get("max_simultaneous") or 3)
            active = sum(1 for j in self.jobs if j.status in BUSY_STATUSES)
            slots = max(0, max_sim - active)
            if slots <= 0:
                return
            for j in self.jobs:
                if slots <= 0:
                    break
                if j.status == DownloadStatus.QUEUED:
                    alive = j.id in self._threads and self._threads[j.id].is_alive()
                    if not alive:
                        slots -= 1
                        # start outside strict recursion issues
                        threading.Thread(
                            target=self.start_job, args=(j.id,), daemon=True
                        ).start()

    def _schedule_loop(self) -> None:
        while not self._stop_scheduler:
            time.sleep(2.0)
            try:
                self._kick_queue()
            except Exception:
                pass

    def shutdown(self) -> None:
        self._stop_scheduler = True
        with self._lock:
            for j in self.jobs:
                if j.status in BUSY_STATUSES:
                    j.status = DownloadStatus.PAUSED
            for eng in self._engines.values():
                eng.pause()
            for pe in self._pause_events.values():
                pe.clear()
            self._persist(force=True)


def _job_score(j: DownloadJob) -> tuple:
    """Rank duplicate entries so the 'best' copy is the one kept."""
    return (1 if j.status == DownloadStatus.COMPLETE else 0,
            int(getattr(j, "downloaded", 0) or 0),
            float(getattr(j, "created_at", 0) or 0))


def _dedupe_jobs_by_path(jobs: list[DownloadJob]) -> list[DownloadJob]:
    """Collapse entries that target the exact same file path, keeping the most
    complete/most-downloaded one and preserving list order. Entries in different
    folders (different paths) are all kept — that's what the Folder column shows."""
    best: dict[str, DownloadJob] = {}
    for j in jobs:
        prev = best.get(j.save_path)
        if prev is None or _job_score(j) > _job_score(prev):
            best[j.save_path] = j
    keep = {id(v) for v in best.values()}
    return [j for j in jobs if id(j) in keep]


def _dedupe_path(p: Path) -> Path:
    """Return a non-colliding path by appending (1), (2), … if needed."""
    if not p.exists() and not Path(str(p) + ".part").exists():
        return p
    stem, suf = p.stem, p.suffix
    i = 1
    while True:
        cand = p.with_name(f"{stem} ({i}){suf}")
        if not cand.exists() and not Path(str(cand) + ".part").exists():
            return cand
        i += 1


def _strip_ext(name: str) -> str:
    """Drop a trailing media/manifest extension from a title-derived name."""
    from pathlib import PurePosixPath

    stem = PurePosixPath(name).name
    lowered = stem.lower()
    for ext in (
        ".m3u8", ".m3u", ".mpd", ".mp4", ".m4v", ".webm", ".mkv",
        ".ts", ".m4s", ".mov", ".mp3", ".m4a", ".aac",
    ):
        if lowered.endswith(ext):
            return stem[: -len(ext)]
    return stem
