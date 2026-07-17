"""Package the browser extension into store-ready zips.

Produces, in the project root:
  - magic_downloader_chrome.zip    (Chrome / Edge / Brave / Opera; MV3 service worker)
  - magic_downloader_firefox.zip   (Firefox AMO; event-page scripts + gecko settings)

Each zip has manifest.json at its root (as the stores require), assembled from
manifests/manifest.base.json plus that browser's overlay — so neither store ever
sees the other browser's background format. This mirrors build.js, which writes
the same merged manifest into browser_extension/ for "Load unpacked".

Single source of truth: manifests/. There are no per-browser manifest files
inside browser_extension/ any more (that's what made Chrome choke on Firefox's
`background.scripts`).

Usage:  python build_extension.py
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EXT = ROOT / "browser_extension"
MANIFESTS = ROOT / "manifests"

# Files shipped in every build (the manifest is assembled separately).
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


def _merged_manifest(overlay_name: str) -> dict:
    """base.json + the per-browser overlay (top-level overlay wins). `background`
    comes entirely from the overlay, so the two browsers never mix formats."""
    base = json.loads((MANIFESTS / "manifest.base.json").read_text(encoding="utf-8"))
    overlay = json.loads((MANIFESTS / overlay_name).read_text(encoding="utf-8"))
    base.update(overlay)
    return base


def _write_zip(out: Path, overlay_name: str) -> None:
    manifest = _merged_manifest(overlay_name)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        # manifest is always stored as manifest.json at the archive root
        z.writestr("manifest.json", json.dumps(manifest, indent=2))
        for rel in ASSETS:
            f = EXT / rel
            if f.exists():
                z.write(f, rel)
            else:
                print(f"  ! missing: {rel}")
    print(f"  {out.name}  ({out.stat().st_size} bytes, base + {overlay_name})")


def main() -> None:
    print("Building extension packages:")
    _write_zip(ROOT / "magic_downloader_chrome.zip", "manifest.chrome.json")
    _write_zip(ROOT / "magic_downloader_firefox.zip", "manifest.firefox.json")
    print("Done. Upload the matching zip to each store (see PUBLISHING.md).")


if __name__ == "__main__":
    main()
