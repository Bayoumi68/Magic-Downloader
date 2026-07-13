"""Package the browser extension into store-ready zips.

Produces, in the project root:
  - magic_downloader_chrome.zip    (Chrome / Edge / Brave / Opera; MV3 service worker)
  - magic_downloader_firefox.zip   (Firefox AMO; event-page + gecko settings)
  - magic_downloader_extension.zip  (cross-browser dev build for "Load unpacked")

Each zip has manifest.json at its root (as the stores require). The Chrome and
Firefox zips swap in manifest.chrome.json / manifest.firefox.json as their
manifest.json so you don't ship the other browser's keys.

Usage:  python build_extension.py
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EXT = ROOT / "browser_extension"

# Files shipped in every build (everything except the manifest variants).
ASSETS = [
    "background.js",
    "content.js",
    "content.css",
    "popup.html",
    "popup.js",
    "icons/icon16.png",
    "icons/icon48.png",
    "icons/icon128.png",
]


def _write_zip(out: Path, manifest_src: str) -> None:
    manifest_path = EXT / manifest_src
    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        # manifest is always stored as manifest.json at the archive root
        z.writestr("manifest.json", json.dumps(manifest_data, indent=2))
        for rel in ASSETS:
            f = EXT / rel
            if f.exists():
                z.write(f, rel)
            else:
                print(f"  ! missing: {rel}")
    print(f"  {out.name}  ({out.stat().st_size} bytes, manifest: {manifest_src})")


def main() -> None:
    print("Building extension packages:")
    _write_zip(ROOT / "magic_downloader_chrome.zip", "manifest.chrome.json")
    _write_zip(ROOT / "magic_downloader_firefox.zip", "manifest.firefox.json")
    _write_zip(ROOT / "magic_downloader_extension.zip", "manifest.json")
    print("Done. Upload the matching zip to each store (see PUBLISHING.md).")


if __name__ == "__main__":
    main()
