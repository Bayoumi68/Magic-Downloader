# Privacy Policy — Magic Downloader

_Last updated: 2026-07-13_

Magic Downloader is a local download manager and browser companion. **It does
not collect, transmit, or sell your personal data.** There are no analytics, no
tracking, and no remote servers operated by us.

## What the browser extension accesses, and why

| Data / permission | Why it's used | Where it goes |
|---|---|---|
| Page/media URLs (`webRequest`, host access) | Detect downloadable video/audio and show the download button | Only your local app at `http://127.0.0.1:7373` |
| Cookies for the current site (`cookies`) | So downloads of files that require your login work | Sent only to the local app to perform that one download |
| Active tab title/URL (`tabs`, `activeTab`) | Name the file and know which page's video to grab | Only the local app |
| Extension settings (`storage`) | Remember your port/token and preferences | Stored locally in your browser |
| `downloads`, `notifications`, `contextMenus` | Capture browser downloads, show status, right-click menu | Local only |

**Nothing is sent to the internet by the extension except the download request
to your own computer** (`127.0.0.1`). Fetching the actual files goes directly
from the desktop app to the site you chose to download from.

## What the desktop app stores

Everything stays on your computer:

- Your download list and settings (`data/jobs.json`, `data/settings.json`).
- The files you download, in the folders you choose.

No account is required. No data leaves your machine except the network requests
needed to download the files you asked for.

## Third parties

Downloads are performed with `requests` and, for site videos, `yt-dlp`. These
contact only the websites you choose to download from. Magic Downloader has no
servers of its own.

## Contact

Questions about this policy: <bayoumi68@gmail.com>
