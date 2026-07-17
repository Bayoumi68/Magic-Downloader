# Privacy Policy — Magic Downloader

_Last updated: 2026-07-16_

Magic Downloader is a local download manager and browser companion. **It does
not collect, transmit, or sell your personal data.** There are no analytics, no
tracking, and no remote servers operated by us.

## Single purpose

The extension has one purpose: **detect downloadable video and files on the page
you are viewing and hand them to the Magic Downloader desktop app on your own
computer** to download.

## Limited use of data

Consistent with the Chrome Web Store Developer Program Policies (Limited Use):
the data the extension touches (page/media URLs, the current site's cookies, the
active tab's title/URL) is used **only** to provide this user-facing feature. It
is **never** sold, never used for advertising or profiling, and never sent
anywhere except your own local app at `http://127.0.0.1:7373`. No humans read it
and it is not stored by us — we have no servers.

## What the browser extension accesses, and why

| Data / permission | When | Why it's used | Where it goes |
|---|---|---|---|
| Page/media URLs (`webRequest`, host access) | At install | Detect downloadable video/audio and show the download button | Only your local app at `http://127.0.0.1:7373` |
| Active tab title/URL (`tabs`, `activeTab`) | At install | Name the file and know which page's video to grab | Only the local app |
| Extension settings (`storage`) | At install | Remember your port/token and preferences | Stored locally in your browser |
| `contextMenus`, `notifications` | At install | Right-click "Download with…" menu, and status messages | Local only |
| **Cookies (`cookies`)** | **Optional — you turn it on** | Send the current site's login cookies so private/logged-in downloads work | Sent only to the local app for that one download |
| **Download take-over (`downloads`)** | **Off by default — you turn it on** | Intercept a normal browser download and hand it to the app | Local only |

**You are asked explicitly for the sensitive parts:**

- **Cookies are never read until you enable it.** `cookies` is an *optional*
  permission. It stays off until you tick **"Send my login cookies for private
  downloads"** in the popup, which triggers the browser's own permission prompt.
  Until then the extension reads no cookies and sends none — public downloads
  still work; only login-gated ones need this. You can turn it back off any time
  (the permission is revoked immediately).
- **The extension does not touch your browser downloads by default.** Taking
  over ("capturing") a normal download only happens after you tick **"Capture
  normal browser downloads"** in the popup.

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
