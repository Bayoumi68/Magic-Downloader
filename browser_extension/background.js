/**
 * Magic Downloader — browser service worker (Chrome / Edge / Brave, MV3)
 *
 * Behaviour:
 *   1. Sniff network traffic for video/audio (HLS .m3u8, DASH .mpd, progressive
 *      .mp4/.webm/.mp3 …) and remember what each tab is playing.
 *   2. Expose that list to the on-page button and the popup so the user can
 *      download the real stream — not just the page URL.
 *   3. Right-click menus + capture of normal browser downloads (unchanged).
 */

// Cross-browser alias: Firefox exposes promise-based `browser`; Chrome/Edge/
// Brave expose promise-based `chrome` (MV3). Event listeners work via `chrome`
// in both, so we only need this for the promise-returning calls below.
const B = (typeof browser !== "undefined" && browser) || chrome;

const DEFAULTS = {
  port: 7373,
  token: "",
  // OFF by default: taking over the browser's own downloads is a surprising,
  // heavy behaviour, so the user opts in explicitly from the popup.
  captureDownloads: false,
  minSizeBytes: 0, // 0 = capture all; set e.g. 102400 to skip tiny files
  enabled: true,
  showVideoButton: true,
  showFloatingButton: true,
};

// Prevent re-entrancy when we cancel browser downloads
const handledDownloadIds = new Set();

// Per-tab detected media:  tabId -> Map<dedupeKey, item>
const tabMedia = new Map();
const MAX_ITEMS_PER_TAB = 40;

// URL patterns we ask the browser to notify us about (keeps the listener cheap).
const MEDIA_FILTER = {
  urls: [
    "*://*/*.m3u8*",
    "*://*/*.m3u*",
    "*://*/*.mpd*",
    "*://*/*.mp4*",
    "*://*/*.ts*",
    "*://*/*.m4v*",
    "*://*/*.webm*",
    "*://*/*.mov*",
    "*://*/*.mkv*",
    "*://*/*.flv*",
    "*://*/*.mp3*",
    "*://*/*.m4a*",
    "*://*/*.aac*",
    "*://*/*.ogg*",
    "*://*/*.opus*",
    "*://*/*.flac*",
    "*://*/*.wav*",
  ],
};

const VIDEO_EXTS = ["mp4", "m4v", "webm", "mov", "mkv", "flv", "ts"];
const AUDIO_EXTS = ["mp3", "m4a", "aac", "ogg", "opus", "flac", "wav"];

// Junk we must never offer as a "video": thumbnails, avatars, UI sounds, ads,
// analytics. (This is what produced files like "no_input.mp3".)
const IGNORE_HOST_RE = /(^|\.)(ytimg\.com|ggpht\.com|googleusercontent\.com|gstatic\.com|doubleclick\.net|googlesyndication\.com|google-analytics\.com|scorecardresearch\.com|adservice\.google\.com)$/i;
const IGNORE_PATH_RE = /\/(s\/search|s\/player|generate_204|api\/stats|ptracking|pagead|log_event)/i;
const SMALL_FILE_BYTES = 50 * 1024; // progressive files below this are UI sounds/sprites

// Sites where the real media isn't a catchable file — always offer the whole
// page to yt-dlp instead. (Extra safety on top of "page has <video>".)
const YTDLP_HINT_HOST_RE = /(^|\.)(youtube\.com|youtu\.be|vimeo\.com|dailymotion\.com|twitch\.tv|facebook\.com|instagram\.com|tiktok\.com|twitter\.com|x\.com|reddit\.com|bilibili\.com|soundcloud\.com)$/i;

function isJunk(url, size, kind) {
  try {
    const u = new URL(url);
    if (IGNORE_HOST_RE.test(u.hostname)) return true;
    if (IGNORE_PATH_RE.test(u.pathname)) return true;
  } catch (_) {
    /* ignore */
  }
  if (kind === "file" && size && size < SMALL_FILE_BYTES) return true;
  return false;
}

chrome.runtime.onInstalled.addListener(async () => {
  const stored = await B.storage.sync.get(DEFAULTS);
  await B.storage.sync.set({ ...DEFAULTS, ...stored });
  rebuildMenus();
});

chrome.runtime.onStartup.addListener(rebuildMenus);

// ── media sniffing ────────────────────────────────────────────────────────

function extOf(url) {
  try {
    const u = new URL(url);
    const path = u.pathname.toLowerCase();
    const dot = path.lastIndexOf(".");
    return dot >= 0 ? path.slice(dot + 1) : "";
  } catch (_) {
    return "";
  }
}

function classify(url) {
  const ext = extOf(url);
  const low = url.toLowerCase();
  if (ext === "m3u8" || ext === "m3u" || low.includes(".m3u8")) {
    return { kind: "hls", mclass: "video" };
  }
  if (ext === "mpd" || low.includes(".mpd")) {
    return { kind: "dash", mclass: "video" };
  }
  if (VIDEO_EXTS.includes(ext)) return { kind: "file", mclass: "video" };
  if (AUDIO_EXTS.includes(ext)) return { kind: "file", mclass: "audio" };
  return null;
}

// Filename that looks like a streaming fragment rather than a whole file.
function looksLikeSegment(url) {
  try {
    const u = new URL(url);
    const name = (u.pathname.split("/").pop() || "").toLowerCase();
    if (/^\d+\.\w+$/.test(name)) return true; // 00012.mp4
    if (/(seg|segment|chunk|frag|fragment|init|media|part)[-_]?\d+/.test(name)) return true;
    if (/\.m4s$/.test(name) || /\.ts$/.test(name)) return true;
    return false;
  } catch (_) {
    return false;
  }
}

function guessTitleName(url, mclass) {
  try {
    const u = new URL(url);
    const last = decodeURIComponent(u.pathname.split("/").filter(Boolean).pop() || "");
    return last.split("?")[0] || (mclass === "audio" ? "audio" : "video");
  } catch (_) {
    return "video";
  }
}

function badgeColorFor(count) {
  return count > 0 ? "#2b579a" : "#00000000";
}

function updateBadge(tabId) {
  const media = tabMedia.get(tabId);
  const count = media ? media.size : 0;
  try {
    chrome.action.setBadgeBackgroundColor({ tabId, color: badgeColorFor(count) });
    chrome.action.setBadgeText({ tabId, text: count ? String(count) : "" });
  } catch (_) {
    /* tab may be gone */
  }
}

async function tabTitle(tabId) {
  try {
    const tab = await B.tabs.get(tabId);
    return { title: tab?.title || "", pageUrl: tab?.url || "" };
  } catch (_) {
    return { title: "", pageUrl: "" };
  }
}

async function addMedia(tabId, url, meta = {}) {
  if (tabId < 0 || !/^https?:/i.test(url)) return;
  const info = classify(url);
  if (!info) return;
  const ext = extOf(url);

  let media = tabMedia.get(tabId);
  if (!media) {
    media = new Map();
    tabMedia.set(tabId, media);
  }

  // Reject (and remove) junk like thumbnails / UI sounds / tiny sprites.
  if (isJunk(url, meta.size, info.kind)) {
    const jk = url.split("#")[0];
    if (media.has(jk)) {
      media.delete(jk);
      updateBadge(tabId);
    }
    return;
  }

  const hasStream = [...media.values()].some((m) => m.kind === "hls" || m.kind === "dash");
  const segDir = url.split("#")[0].split("?")[0].replace(/\/[^/]*$/, "/");

  if (info.kind === "hls" || info.kind === "dash") {
    // A manifest arrived — drop any progressive "files" already collected;
    // on a streaming page those were almost certainly fragments.
    for (const [k, v] of [...media.entries()]) {
      if (v.kind === "file") media.delete(k);
    }
  } else if (ext === "ts") {
    // Raw MPEG-TS (.ts). When the tab already has a manifest, these are its
    // fragments and the manifest is the better download — so skip them.
    // Otherwise offer the stream ONCE: collapse every .ts that shares a folder
    // into a single entry, so a site that exposes only .ts is downloadable
    // without flooding the list with every segment.
    if (hasStream) return;
    for (const v of media.values()) {
      if (v.ext === "ts" && v._segDir === segDir) return;
    }
  } else {
    // Progressive file. Skip obvious fragments, and skip entirely if this tab
    // is already streaming (HLS/DASH) — those .mp4/.webm hits are fragments.
    if (hasStream || looksLikeSegment(url)) return;
  }

  const key = url.split("#")[0];
  if (media.has(key)) {
    if (meta.size) media.get(key).size = meta.size;
    return;
  }
  if (media.size >= MAX_ITEMS_PER_TAB) return;

  const { title, pageUrl } = await tabTitle(tabId);
  media.set(key, {
    url,
    kind: info.kind,
    mclass: info.mclass,
    ext,
    size: meta.size || 0,
    contentType: meta.contentType || "",
    name: ext === "ts" ? (title || "Video stream (.ts)") : guessTitleName(url, info.mclass),
    title: title || "",
    pageUrl: pageUrl || meta.pageUrl || "",
    _segDir: ext === "ts" ? segDir : undefined,
    ts: Date.now(),
  });
  updateBadge(tabId);
}

chrome.webRequest.onBeforeRequest.addListener(
  (details) => {
    if (details.tabId >= 0) addMedia(details.tabId, details.url);
  },
  MEDIA_FILTER
);

chrome.webRequest.onHeadersReceived.addListener(
  (details) => {
    if (details.tabId < 0) return;
    let size = 0;
    let ctype = "";
    for (const h of details.responseHeaders || []) {
      const n = h.name.toLowerCase();
      if (n === "content-length") size = parseInt(h.value, 10) || 0;
      else if (n === "content-type") ctype = (h.value || "").toLowerCase();
    }
    addMedia(details.tabId, details.url, { size, contentType: ctype });
  },
  MEDIA_FILTER,
  ["responseHeaders"]
);

// Reset a tab's media when it navigates to a new page.
chrome.webNavigation.onCommitted.addListener((details) => {
  if (details.frameId === 0) {
    tabMedia.delete(details.tabId);
    updateBadge(details.tabId);
  }
});

chrome.tabs.onRemoved.addListener((tabId) => {
  tabMedia.delete(tabId);
});

// A "page" item = "download whatever video this page is showing" via yt-dlp.
// Reported by the content script when it sees a <video>, or inferred from the
// hostname. This is the ONLY path that works on such sites.
function setPageItem(tabId, pageUrl, title) {
  if (tabId < 0 || !/^https?:/i.test(pageUrl || "")) return;
  let media = tabMedia.get(tabId);
  if (!media) {
    media = new Map();
    tabMedia.set(tabId, media);
  }
  media.set("__page__", {
    url: pageUrl,
    kind: "page",
    mclass: "video",
    ext: "mp4",
    size: 0,
    name: title || "This page's video",
    title: title || "",
    pageUrl,
    isPage: true,
    ts: Date.now(),
  });
  updateBadge(tabId);
}

function mediaList(tabId) {
  const media = tabMedia.get(tabId);
  if (!media) return [];
  // Page video first, then streams, then largest files.
  return [...media.values()].sort((a, b) => {
    const rank = (m) => (m.kind === "page" ? 0 : m.kind === "hls" || m.kind === "dash" ? 1 : 2);
    if (rank(a) !== rank(b)) return rank(a) - rank(b);
    return (b.size || 0) - (a.size || 0);
  });
}

// ── context menus ─────────────────────────────────────────────────────────

function rebuildMenus() {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({
      id: "md-link",
      title: "Download with Magic Downloader",
      contexts: ["link"],
    });
    chrome.contextMenus.create({
      id: "md-page",
      title: "Download page URL with Magic Downloader",
      contexts: ["page"],
    });
    chrome.contextMenus.create({
      id: "md-media",
      title: "Download video/media with Magic Downloader",
      contexts: ["image", "video", "audio"],
    });
    chrome.contextMenus.create({
      id: "md-selection",
      title: "Download selected URL with Magic Downloader",
      contexts: ["selection"],
    });
  });
}

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  let url = "";
  if (info.menuItemId === "md-link") url = info.linkUrl || "";
  else if (info.menuItemId === "md-page") url = info.pageUrl || tab?.url || "";
  else if (info.menuItemId === "md-media") url = info.srcUrl || "";
  else if (info.menuItemId === "md-selection") {
    const t = (info.selectionText || "").trim();
    if (/^https?:\/\//i.test(t)) url = t;
  }
  if (!url || !/^https?:\/\//i.test(url)) {
    notify("Magic Downloader", "No valid http(s) URL found.");
    return;
  }
  const referrer = info.pageUrl || tab?.url || "";
  const info2 = classify(url);
  await sendToApp(url, {
    referrer,
    pageUrl: referrer,
    filename: guessName(url),
    media_type: info2 ? info2.kind : "http",
    title: tab?.title || "",
  });
});

// ── capture browser downloads (unchanged behaviour) ────────────────────────

// A brand-new download begins in this state; anything else is history.
// startTime within this window of "now" is what tells a fresh download apart
// from a replayed one.
const FRESH_DOWNLOAD_MS = 15000;

chrome.downloads.onCreated.addListener(async (item) => {
  const cfg = await getConfig();
  if (!cfg.enabled || !cfg.captureDownloads) return;
  if (!item || !item.id) return;
  if (handledDownloadIds.has(item.id)) return;

  // MV3 service workers are ephemeral. Every time this one wakes — including
  // when the desktop app connects — Chrome RE-FIRES onCreated for the downloads
  // already in its list, i.e. the recent history, not just new downloads. And
  // handledDownloadIds lives in memory, so it's empty after each wake and can't
  // dedupe them. Left unguarded, that replays the whole history at the app: with
  // the app running it re-queues every past download; with it off, each hand-off
  // fails and the offline toast storms. So: only a download that is actually
  // starting right now. A completed/interrupted item, or one whose startTime is
  // more than a few seconds old, is history — ignore it.
  if (item.state && item.state !== "in_progress") return;
  const startedAt = item.startTime ? Date.parse(item.startTime) : NaN;
  if (!Number.isNaN(startedAt) && Date.now() - startedAt > FRESH_DOWNLOAD_MS) return;

  const url = item.finalUrl || item.url || "";
  if (!url || !/^https?:\/\//i.test(url)) return;
  if (url.startsWith("blob:") || url.startsWith("data:") || url.startsWith("chrome")) return;
  if (item.filename && item.filename.includes("MagicDownloader") && item.state === "complete") return;

  handledDownloadIds.add(item.id);
  try {
    // Check the app is actually reachable BEFORE touching the browser's
    // download. The old order cancelled + erased first, then tried to hand off:
    // if the app was off, the file was destroyed and gone (and every capture
    // fired an offline toast). Now, when the app isn't running we leave the
    // browser to download normally and just say so, once.
    const health = await pingApp();
    if (!health.ok) {
      handledDownloadIds.delete(item.id);
      notifyOffline();
      return;
    }

    let finalUrl = url;
    if (!item.finalUrl) {
      await sleep(150);
      try {
        const [fresh] = await B.downloads.search({ id: item.id });
        if (fresh?.finalUrl) finalUrl = fresh.finalUrl;
        if (fresh?.fileSize > 0 && cfg.minSizeBytes > 0 && fresh.fileSize < cfg.minSizeBytes) {
          handledDownloadIds.delete(item.id);
          return;
        }
      } catch (_) {
        /* ignore */
      }
    }

    await B.downloads.cancel(item.id).catch(() => {});
    await B.downloads.erase({ id: item.id }).catch(() => {});

    const filename = basename(item.filename) || guessName(finalUrl);
    const referrer = item.referrer || "";
    const info = classify(finalUrl);
    await sendToApp(finalUrl, {
      referrer,
      filename,
      media_type: info ? info.kind : "http",
    });
  } catch (err) {
    console.error("Magic Downloader capture failed", err);
    handledDownloadIds.delete(item.id);
  }

  if (handledDownloadIds.size > 200) handledDownloadIds.clear();
});

// ── message bus ─────────────────────────────────────────────────────────────

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg?.type === "ping") {
    pingApp().then(sendResponse);
    return true;
  }
  if (msg?.type === "add" && msg.url) {
    sendToApp(msg.url, msg.opts || {}).then(sendResponse);
    return true;
  }
  if (msg?.type === "getConfig") {
    getConfig().then(sendResponse);
    return true;
  }
  if (msg?.type === "getMedia") {
    const tabId = msg.tabId != null ? msg.tabId : sender?.tab?.id;
    sendResponse({ ok: true, items: tabId != null ? mediaList(tabId) : [] });
    return false;
  }
  if (msg?.type === "reportPageVideo") {
    const tabId = sender?.tab?.id;
    if (tabId != null) setPageItem(tabId, msg.pageUrl, msg.title);
    sendResponse({ ok: true });
    return false;
  }
  if (msg?.type === "downloadMedia" && msg.item) {
    const tabId = msg.tabId != null ? msg.tabId : sender?.tab?.id;
    downloadMedia(msg.item, msg.sel || {}, tabId).then(sendResponse);
    return true;
  }
  if (msg?.type === "probeMedia" && msg.url) {
    probeApp(msg.url, msg.opts || {}).then(sendResponse);
    return true;
  }
  return false;
});

async function downloadMedia(item, sel, tabId) {
  const opts = {
    filename: filenameForMedia(item, sel),
    referrer: item.pageUrl || "",
    pageUrl: item.pageUrl || "",
    title: item.title || "",
    media_type: item.kind === "file" ? "http" : item.kind, // page | hls | dash | http
  };
  if (sel) {
    if (sel.format_id) opts.format_id = sel.format_id;
    if (sel.height) opts.height = sel.height;
    if (sel.audio_only) opts.audio_only = true;
  }
  return sendToApp(item.url, opts);
}

function filenameForMedia(item, sel) {
  const base = sanitize(item.title || item.name || "video").slice(0, 120);
  if (sel && sel.audio_only) return `${base}.m4a`;
  if (item.kind === "hls" || item.kind === "dash" || item.kind === "page") {
    return `${base}.mp4`;
  }
  return item.name || `${base}.${item.ext || "bin"}`;
}

function sanitize(s) {
  return (s || "").replace(/[<>:"/\\|?*\n\r\t]+/g, "_").trim() || "video";
}

// ── app API ─────────────────────────────────────────────────────────────────

async function getConfig() {
  return { ...DEFAULTS, ...(await B.storage.sync.get(DEFAULTS)) };
}

async function apiBase() {
  const cfg = await getConfig();
  return `http://127.0.0.1:${cfg.port}`;
}

async function authHeaders() {
  const cfg = await getConfig();
  const h = { "Content-Type": "application/json" };
  if (cfg.token) {
    h["X-Magic-Token"] = cfg.token;
    h["Authorization"] = `Bearer ${cfg.token}`;
  }
  return h;
}

async function pingApp() {
  try {
    const base = await apiBase();
    const res = await fetch(`${base}/api/ping`, { method: "GET" });
    if (!res.ok) return { ok: false, error: `HTTP ${res.status}` };
    const data = await res.json();
    return { ok: true, ...data };
  } catch (e) {
    return { ok: false, error: String(e.message || e) };
  }
}

async function probeApp(url, opts = {}) {
  try {
    const base = await apiBase();
    const cookie = opts.cookie ?? (await collectCookies(url));
    const res = await fetch(`${base}/api/probe`, {
      method: "POST",
      headers: await authHeaders(),
      body: JSON.stringify({
        url,
        media_type: opts.media_type || "",
        referrer: opts.referrer || opts.pageUrl || "",
        page_url: opts.pageUrl || "",
        cookie,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.ok === false) return { ok: false, error: data.error || `HTTP ${res.status}` };
    return { ok: true, ...data };
  } catch (e) {
    return { ok: false, error: String(e.message || e) };
  }
}

async function collectCookies(url) {
  try {
    // `cookies` is an OPTIONAL permission the user grants explicitly (a toggle
    // in the popup). Until then we read nothing and send no cookies — public
    // downloads still work; only login-gated ones need the opt-in.
    const granted = await B.permissions.contains({ permissions: ["cookies"] });
    if (!granted) return "";
    const cookies = await B.cookies.getAll({ url });
    if (!cookies?.length) return "";
    return cookies.map((c) => `${c.name}=${c.value}`).join("; ");
  } catch (_) {
    return "";
  }
}

async function sendToApp(url, opts = {}) {
  const cfg = await getConfig();
  if (!cfg.enabled) {
    notify("Magic Downloader", "Extension is disabled in the popup.");
    return { ok: false, error: "disabled" };
  }

  const cookie = opts.cookie ?? (await collectCookies(url));
  const payload = {
    url,
    filename: opts.filename || guessName(url),
    referrer: opts.referrer || "",
    page_url: opts.pageUrl || opts.referrer || "",
    title: opts.title || "",
    media_type: opts.media_type || "http",
    cookie,
    start: true,
  };
  if (opts.height) payload.height = opts.height;
  if (opts.format_id) payload.format_id = opts.format_id;
  if (opts.audio_only) payload.audio_only = true;

  try {
    const base = await apiBase();
    const res = await fetch(`${base}/api/add`, {
      method: "POST",
      headers: await authHeaders(),
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.ok === false) {
      const err = data.error || `HTTP ${res.status}`;
      notify("Magic Downloader", `Failed: ${err}. Is the app running?`, "md-add-failed");
      return { ok: false, error: err };
    }
    if (data.prompted) {
      notify("Magic Downloader", `Choose options in the app for: ${data.filename || payload.filename}`);
    } else {
      const label = data.media_type && data.media_type !== "http" ? " (video)" : "";
      notify("Magic Downloader", `Queued${label}: ${data.filename || payload.filename}`);
    }
    return { ok: true, ...data };
  } catch (e) {
    notifyOffline();   // throttled, single reusable toast — never a storm
    return { ok: false, error: String(e.message || e) };
  }
}

// ── helpers ──────────────────────────────────────────────────────────────────

function guessName(url) {
  try {
    const u = new URL(url);
    const last = u.pathname.split("/").filter(Boolean).pop() || "download";
    return decodeURIComponent(last.split("?")[0]) || "download";
  } catch (_) {
    return "download";
  }
}

function basename(path) {
  if (!path) return "";
  const parts = path.replace(/\\/g, "/").split("/");
  return parts[parts.length - 1] || "";
}

// Passing an id makes chrome REPLACE any existing notification with that id
// instead of stacking a new toast. Without one, every call spawns a fresh
// notification, and the OS drip-feeds a burst of them out one at a time — which
// is exactly the flickering "Cannot reach app" storm when the app is off.
function notify(title, message, id) {
  try {
    const opts = {
      type: "basic",
      iconUrl: "icons/icon128.png",
      title,
      message,
      silent: true, // no notification sound/beep
    };
    if (id) chrome.notifications.create(id, opts);
    else chrome.notifications.create(opts);
  } catch (_) {
    /* notifications may be blocked */
  }
}

// The "app isn't running" notice. One reusable toast (fixed id), shown at most
// once every OFFLINE_NOTICE_MS — so a page firing several downloads, or any
// repeated failure, can never turn into a wall of toasts.
const OFFLINE_NOTICE_ID = "md-app-offline";
const OFFLINE_NOTICE_MS = 10000;
let lastOfflineNotice = 0;
async function notifyOffline() {
  const now = Date.now();
  if (now - lastOfflineNotice < OFFLINE_NOTICE_MS) return;
  lastOfflineNotice = now;
  // getConfig() rather than a bare `cfg`: this is a top-level helper with no
  // config in scope, and the port is user-configurable.
  const port = (await getConfig()).port;
  notify(
    "Magic Downloader",
    "Cannot reach the app. Start Magic Downloader (API on port " + port + ").",
    OFFLINE_NOTICE_ID
  );
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}
