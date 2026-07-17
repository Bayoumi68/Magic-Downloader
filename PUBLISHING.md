# Publishing Magic Downloader (free) and getting the extension trusted

This is the honest, practical playbook for putting the app online and getting
the browser extension **signed and recognized** by the major browsers.

---

## ⚠️ Read this first — the video-downloader policy problem

Getting an extension "trusted" means getting it **accepted into a browser's
official store**, where it is reviewed and signed. But:

- **Chrome Web Store (Google) prohibits extensions that download from certain video sites**
  and content that violates a site's Terms of Service. Video/stream downloaders
  are routinely **rejected or removed**. This is *why such Chrome extensions
  keep getting pulled.* It is a policy decision, not a technical one — no
  packaging trick gets around it.
- **Microsoft Edge Add-ons** has historically been more lenient, but the same
  ToS rules apply and enforcement can change.
- **Firefox (AMO)** is the most permissive for downloaders and is the realistic
  home for this kind of tool.

**Practical takeaways**
1. **Firefox first.** It's free, it signs everything, and downloaders are
   tolerated. This is your best shot at "trusted + recognized."
2. **Edge second.** Free, often accepts what Chrome won't.
3. **Chrome:** expect friction. Options: don't foreground video-site names, submit and
   see, or distribute **outside** the store (self-hosted / enterprise), which
   means it *won't* be one-click-trusted.
4. To reduce ToS exposure, market it as a **general download manager** (files,
   HLS/DASH streams) rather than "video downloader," and keep video features
   generic.

A more store-friendly architecture note is at the end (**Native Messaging**).

---

## Part 1 — Make it a free, open project

- ✅ **License:** `LICENSE` (MIT) — anyone can use/modify/redistribute for free.
- ✅ **Privacy policy:** `PRIVACY.md` — required by every store. Host it at a
  public URL (e.g., your GitHub repo's `PRIVACY.md`, or GitHub Pages) and use
  that URL in each store listing.
- **Put the code on GitHub** (free, public). This is where you host releases and
  the privacy policy, and it's what reviewers and users look at.

---

## Part 2 — Publish the extension (get it signed & recognized)

### Assets you need once (all stores reuse them)
- Icons: 16 / 48 / 128 px ✅ (already in `browser_extension/icons/`).
- 1–5 **screenshots** (1280×800 or 640×400) of the popup and the on-page button.
- A short + long **description** (draft below).
- The **privacy policy URL**.
- **Permission justifications** (Chrome/Edge require one line per permission):

  | Permission | Justification |
  |---|---|
  | `host_permissions` `*://*/*` | Detect downloadable media on whatever site the user is viewing |
  | `webRequest` | Observe media (HLS/DASH/MP4) request URLs to offer them for download |
  | `cookies` | Pass the user's existing session cookies so downloads that require login succeed |
  | `downloads` | Optionally capture downloads the browser starts and hand them to the app |
  | `tabs` / `activeTab` | Read the current page's title/URL to name files and grab the page's video |
  | `notifications` | Show "queued / failed" status |
  | `contextMenus` | Right-click "Download with Magic Downloader" |
  | `webNavigation` | Reset the detected-media list when the tab navigates |

### 2a. Firefox — Add-ons (AMO)  ·  FREE  ·  recommended first
Two ways, both give a **signed** add-on:
- **Listed** (public, searchable): [Developer Hub → Submit](https://addons.mozilla.org/developers/addon/submit/distribution)
  → "On this site" → upload `magic_downloader_firefox.zip` → passes a review →
  appears on addons.mozilla.org, one-click install, fully trusted.
- **Unlisted** (self-distribution): same page → "On your own" → Mozilla signs it
  instantly with no public review → you get a signed `.xpi` you host yourself
  (installs permanently in normal Firefox). See `FIREFOX_INSTALL.md`.

### 2b. Microsoft Edge — Add-ons  ·  FREE
1. Register at the [Partner Center — Edge program](https://partner.microsoft.com/dashboard/microsoftedge/) (no fee).
2. Upload the extension zip, fill the listing, submit for certification.
3. Edge uses the same MV3 package as Chrome.

### 2c. Chrome — Web Store  ·  one-time **$5**  ·  strict (see warning above)
1. Pay the one-time $5 developer fee at the
   [Chrome Web Store Developer Dashboard](https://chrome.google.com/webstore/devconsole/).
2. Upload a **Chrome-specific** zip (see manifest note below), add screenshots,
   description, privacy policy URL, and permission justifications.
3. Submit for review. **Be prepared for rejection** on the downloader policy; if
   rejected, fall back to Edge/Firefox or self-distribution.

### 2d. Opera / Brave
- **Brave** uses the Chrome Web Store directly — publishing to Chrome covers it.
- **Opera** has its own free [add-ons store](https://addons.opera.com/developer/).

### Per-store manifest note
Chrome and Firefox need different background formats, and Chrome **refuses to
load** any MV3 manifest that contains `background.scripts` ("requires manifest
version of 2 or lower"). So the two are never mixed: the manifest is assembled
per-browser from an isolated `manifests/` folder that lives **outside**
`browser_extension/`.

`manifests/` holds three files:
- `manifest.base.json` — everything shared (name, version, permissions,
  content_scripts, action, icons); **no** `background` key.
- `manifest.chrome.json` — overlay: `background.service_worker`.
- `manifest.firefox.json` — overlay: `background.scripts` + `browser_specific_settings.gecko`.

Build the per-store zips (each gets base + its overlay as `manifest.json`):

```powershell
python build_extension.py
#  -> magic_downloader_chrome.zip   (service_worker only)
#  -> magic_downloader_firefox.zip  (scripts + gecko)
```

For **Load unpacked** dev testing, generate the single manifest into
`browser_extension/` first (it is git-ignored, so a fresh clone has none):

```powershell
node build.js --chrome     # or:  node build.js --firefox
```

`build.js` also deletes any stale `manifest.chrome.json` / `manifest.firefox.json`
left inside `browser_extension/`, so the folder Chrome loads never contains a
`scripts` key. **Bump the version in `manifests/manifest.base.json`** (not in the
generated `browser_extension/manifest.json`).

---

## Part 3 — Ship the desktop app for free

Right now users need Python. Bundle it into a standalone `.exe` using the
committed build spec (`MagicDownloader.spec`):

```powershell
.\.venv\Scripts\python.exe -m pip install pyinstaller pillow
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm MagicDownloader.spec
# result: dist\MagicDownloader\MagicDownloader.exe  (portable, ~62 MB, no Python needed)
```

The spec bundles the browser extension and **all of yt-dlp's site extractors**
(so video downloads work in the frozen build). When run as an exe, the app
keeps its settings/jobs in `%LOCALAPPDATA%\MagicDownloader` and copies the
extension there too, so the "Install browser extension" button points at a real
folder. ffmpeg stays a separate download — the in-app installer (Options →
Video) handles it.

Distribute the whole `dist\MagicDownloader\` folder (zipped). For a single-file
build add `--onefile` to the EXE section of the spec (slower startup).

### Windows installer (Setup.exe)
A committed Inno Setup script (`MagicDownloader.iss`) turns the build into a
proper installer with a Start-menu shortcut, optional desktop icon, and an
uninstaller. Install [Inno Setup](https://jrsoftware.org/isdl.php) (free), then:

```powershell
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" MagicDownloader.iss
# result: installer\MagicDownloader-Setup-0.5.0.exe
```

It installs **per-user by default (no admin prompt)**; the user can choose
all-users. The app's data still lives in `%LOCALAPPDATA%\MagicDownloader`, so it
works cleanly from Program Files. Note: the installer is unsigned, so SmartScreen
still warns until you sign it or ship via the Microsoft Store.

**Distribute it for free:** upload the built folder (zipped) or installer to
**GitHub Releases**. That's the free, standard home for downloads.

### Making the desktop app "trusted" (no Windows SmartScreen warning)
Unsigned `.exe`s trigger a "Windows protected your PC / unknown publisher"
warning. To remove it you need one of:

| Option | Cost | Result |
|---|---|---|
| Ship unsigned | Free | Works, but shows the SmartScreen warning until it earns reputation |
| **Microsoft Store** (package as MSIX) | **~$19 one-time** dev account | Microsoft signs it; no warnings; "trusted"; auto-updates — the cheapest trusted path |
| **winget** (Windows Package Manager) | Free | `winget install` distribution; still needs a signed installer to avoid warnings |
| OV code-signing certificate | ~$150–250/yr (hardware token) | Signs your `.exe`; warning clears after reputation builds |
| EV code-signing certificate | ~$300–400/yr | Instant SmartScreen trust |

**Recommended free/cheap path:** GitHub Releases (unsigned) to start, then the
**Microsoft Store (MSIX, ~$19)** when you want it warning-free and
auto-updating.

---

## Recommended plan (cheapest path to "trusted")

1. Put the repo + `PRIVACY.md` on **GitHub** (free).
2. Publish the extension to **Firefox AMO** (free) and **Edge Add-ons** (free) —
   this gets it signed and one-click-installable in two major browsers.
3. Try **Chrome** ($5) knowing it may be rejected; if so, self-distribute the
   unlisted-signed Firefox build and the Chrome unpacked/self-hosted build.
4. Ship the desktop `.exe` via **GitHub Releases**; move to the **Microsoft
   Store (~$19)** when you want no SmartScreen warning.

Total to be "free + trusted on Firefox & Edge": **$0.** Add Chrome: **$5**. Add
a warning-free Windows app: **~$19**.

---

## Appendix — the more store-friendly architecture (optional, bigger change)

Stores scrutinize extensions that talk to a local HTTP server and hold broad
host permissions. The officially-blessed way for an extension to talk to a
desktop app is **Native Messaging** (`runtime.connectNative`) instead of
`fetch('http://127.0.0.1:7373')`. It removes the localhost-server red flag and
some host-permission concerns, improving review odds. It's a moderate rewrite
(a native-messaging host manifest + stdin/stdout bridge in the app). Worth doing
if Chrome/Edge rejects the current localhost approach — ask and I'll implement it.

---

### Draft store description

**Short:** "Multi-connection download manager: accelerate downloads with multiple
connections, capture browser downloads, and grab video/audio streams — saved to
your own PC."

**Long:** "Magic Downloader is a free, open-source download manager for Windows.
Speed up any download with multi-connection segmented downloading, pause/resume,
a queue, and file categories. Its browser companion adds a download button to
web pages, detects video and audio streams (HLS/DASH and progressive files), and
can capture normal browser downloads — all handled by the desktop app on your
own computer. No account, no tracking, no data leaves your machine. Not
affiliated with any other product. Please respect the terms of service of the sites you
download from."
