# Magic Downloader — Browser Extension

Grabs **videos** and downloads from **Chrome**, **Edge**, or **Brave** and sends them to the Magic Downloader desktop app — the same idea as IDM's browser integration, including the **“⬇ Download” button on video players**.

## What it does

- **YouTube, Vimeo & ~1800 sites** — when a page is playing a video, the popup offers **“This page's video”** with a quality dropdown listing **every** format (handled by yt-dlp in the app). This is the path that actually works on YouTube.
- **Sniffs streaming video** on other sites (HLS `.m3u8`, DASH `.mpd`, progressive `.mp4`/`.webm`/`.mp3` …) from network traffic — even with `blob:` URLs and no visible file.
- **Overlays a Download button** on `<video>` players and shows a **badge** with the number of videos found.
- **Ignores junk** (thumbnails, UI sounds, tiny sprites) so you never get a stray 6 KB file.
- Right-click links/media, and optional **capture** of normal browser downloads.

## Prerequisites

1. **Magic Downloader is running** (green **Browser: :7373** badge in the toolbar).
2. Browser API enabled in **Options** (default port `7373`).

## Install (unpacked)

### Chrome / Brave

1. Open `chrome://extensions`
2. Turn on **Developer mode**
3. Click **Load unpacked**
4. Select this folder:

   `E:\PycharmProjects\Magic_downloader\browser_extension`

### Edge

1. Open `edge://extensions`
2. Turn on **Developer mode**
3. **Load unpacked** → same folder as above

### Firefox

The extension is cross-browser, but **Firefox requires add-ons to be signed to
install permanently** (this is why IDM's Firefox add-on is on addons.mozilla.org
— it's signed). See **[../FIREFOX_INSTALL.md](../FIREFOX_INSTALL.md)** for the
full guide. Short version:

- **Permanent, any Firefox:** upload `../magic_downloader_extension.zip` to
  [AMO Developer Hub](https://addons.mozilla.org/developers/addon/submit/distribution)
  as **“On your own”** (self-distribution) → Mozilla auto-signs it (free, no
  review) → download the signed `.xpi` → `about:addons` → ⚙ → **Install Add-on
  From File…**
- **Permanent, unsigned:** only in Firefox **Developer Edition / Nightly / ESR**
  with `xpinstall.signatures.required=false` in `about:config`.
- **Temporary (test only):** `about:debugging` → **Load Temporary Add-on…** →
  pick `manifest.json` (wiped on restart).

After installing, grant **“Access your data for all websites”** in the
extension's Permissions (Firefox MV3 makes site access opt-in), and keep the
desktop app running.

## Usage

| Action | Result |
|--------|--------|
| On **YouTube** etc., click the **toolbar icon** | Popup shows **“This page's video”** → pick a quality → downloaded & merged in the app (via yt-dlp) |
| Press ▶ **Play** a video, then click **⬇ Download** on the player | Detected stream(s)/page video shown; pick one → downloaded in the app |
| Click the **toolbar icon** (badge shows video count) | Popup lists detected media + quality/format picker |
| Right-click a **link** → *Download with Magic Downloader* | URL + cookies sent to the app |
| Right-click **image/video/audio** | Media URL captured |
| Click a normal download (if **Capture browser downloads** is on) | Browser download is cancelled; Magic Downloader takes over |

> **Why press Play first?** Streams are only requested by the page once playback starts. The button/badge populate as the video begins loading.

> **ffmpeg** — the popup shows whether the app found ffmpeg. It's needed to merge streamed video+audio into one `.mp4`; without it HLS saves as `.ts` and DASH keeps audio/video separate.

## Settings

Match the popup **API port** with **Options → API port** in the app (default `7373`).

If you set an **API token** in the app, enter the same token in the extension popup.

## Troubleshooting

| Problem | Fix |
|---------|-----|
| “App not reachable” | Start `main.py`; check port; Windows Firewall allow local Python |
| No video detected / empty list | Press ▶ **Play** first, wait a second, reopen. Some players load the stream only on play. Reload the page after installing the extension. |
| Button not on the player | Enable **Show “Download” button on video players** in the popup; some sites render video in cross-origin iframes (use right-click or the toolbar popup instead) |
| Downloads but won’t merge to MP4 | Install **ffmpeg** and put it on `PATH` (popup shows ffmpeg status) |
| Port in use | Change port in app Options **and** extension popup |
| Download stays in Chrome | Enable **Capture browser downloads** in the popup |
| 401 Unauthorized | Token mismatch — clear token on both sides or match them |

## Security

The desktop API listens only on `127.0.0.1` (this machine). Optional token adds a shared secret for local callers.
