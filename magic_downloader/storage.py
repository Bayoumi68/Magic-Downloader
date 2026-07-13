"""Persist jobs and settings to JSON."""

from __future__ import annotations

import json
from typing import Any

from magic_downloader.config import JOBS_PATH, ensure_dirs
from magic_downloader.models import DownloadJob


def load_jobs() -> list[DownloadJob]:
    ensure_dirs()
    if not JOBS_PATH.exists():
        return []
    try:
        with open(JOBS_PATH, "r", encoding="utf-8") as f:
            raw: list[dict[str, Any]] = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []
    return [DownloadJob.from_dict(item) for item in raw]


def save_jobs(jobs: list[DownloadJob]) -> None:
    ensure_dirs()
    payload = [j.to_dict() for j in jobs]
    with open(JOBS_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
