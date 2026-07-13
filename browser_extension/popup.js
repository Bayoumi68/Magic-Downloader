const $ = (id) => document.getElementById(id);

// Firefox = promise-based `browser`; Chrome/Edge MV3 = promise-based `chrome`.
const B = (typeof browser !== "undefined" && browser) || chrome;

let activeTabId = null;
let activeTab = null;

const YTDLP_HOST_RE = /(^|\.)(youtube\.com|youtu\.be|vimeo\.com|dailymotion\.com|twitch\.tv|facebook\.com|instagram\.com|tiktok\.com|twitter\.com|x\.com|reddit\.com|bilibili\.com|soundcloud\.com)$/i;

function hostOf(url) {
  try {
    return new URL(url).hostname;
  } catch {
    return "";
  }
}

function send(payload) {
  return new Promise((resolve) => {
    try {
      const p = B.runtime.sendMessage(payload);
      if (p && typeof p.then === "function") {
        p.then((res) => resolve(res || { ok: false })).catch(() => resolve({ ok: false }));
      } else {
        resolve({ ok: false });
      }
    } catch (_) {
      resolve({ ok: false });
    }
  });
}

async function getActiveTab() {
  const [tab] = await B.tabs.query({ active: true, currentWindow: true });
  return tab;
}

async function load() {
  const cfg = await send({ type: "getConfig" });
  $("enabled").checked = !!cfg.enabled;
  $("captureDownloads").checked = !!cfg.captureDownloads;
  $("showVideoButton").checked = cfg.showVideoButton !== false;
  $("showFloatingButton").checked = cfg.showFloatingButton !== false;
  $("port").value = cfg.port || 7373;
  $("token").value = cfg.token || "";

  const tab = await getActiveTab();
  activeTab = tab || null;
  activeTabId = tab ? tab.id : null;

  await recheck();
  await renderMedia();
}

async function recheck() {
  $("statusText").textContent = "Checking app…";
  $("dot").classList.remove("ok");
  const res = await send({ type: "ping" });
  if (res?.ok) {
    $("dot").classList.add("ok");
    $("statusText").textContent = "Connected to Magic Downloader";
    $("statusDetail").textContent = `${res.name || "App"} v${res.version || "?"} · port ${res.port || ""}`;
    $("ffmpegWarn").style.display = res.ffmpeg === false ? "block" : "none";
  } else {
    $("dot").classList.remove("ok");
    $("statusText").textContent = "App not reachable";
    $("statusDetail").textContent = res?.error || "Start Magic Downloader first";
  }
}

function humanSize(n) {
  if (!n) return "";
  const u = ["B", "KB", "MB", "GB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < u.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(i ? 1 : 0)} ${u[i]}`;
}

function mediaSub(item) {
  if (item.kind === "page") return "Web page video · choose a quality →";
  if (item.kind === "hls") return "HLS adaptive stream";
  if (item.kind === "dash") return "DASH adaptive stream";
  const size = item.size ? ` · ${humanSize(item.size)}` : "";
  return `${item.ext ? item.ext.toUpperCase() : "FILE"}${size}`;
}

function guessName(url) {
  try {
    const u = new URL(url);
    return decodeURIComponent(u.pathname.split("/").filter(Boolean).pop() || "download").split("?")[0];
  } catch {
    return "download";
  }
}

function esc(s) {
  return (s || "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

async function renderMedia() {
  const box = $("mediaList");
  const res = await send({ type: "getMedia", tabId: activeTabId });
  let items = (res && res.items) || [];

  // Fallback: on known video sites, always offer the page-download option even
  // if the content script hasn't reported yet (or is blocked on the page).
  const host = activeTab ? hostOf(activeTab.url) : "";
  const hasPage = items.some((i) => i.kind === "page");
  if (!hasPage && host && YTDLP_HOST_RE.test(host) && /^https?:/i.test(activeTab.url || "")) {
    items = [
      {
        url: activeTab.url,
        kind: "page",
        mclass: "video",
        ext: "mp4",
        name: activeTab.title || "This page's video",
        title: activeTab.title || "",
        pageUrl: activeTab.url,
        size: 0,
      },
      ...items,
    ];
  }

  box.innerHTML = "";
  if (!items.length) {
    box.innerHTML =
      '<div class="empty">No video detected yet. Press ▶ Play on the video, then reopen this popup — the stream appears as it loads. You can also right-click a link → Download with Magic Downloader.</div>';
    return;
  }
  for (const item of items) box.appendChild(renderItem(item));
}

function renderItem(item) {
  const block = document.createElement("div");
  block.className = "item-block";
  const kind = item.kind === "file" ? "file" : item.kind;
  const name = item.title || item.name || guessName(item.url);
  const isPage = item.kind === "page";
  const isStream = item.kind === "hls" || item.kind === "dash";
  const hasPicker = isPage || isStream;

  const head = document.createElement("div");
  head.className = "item";
  head.innerHTML = `
    <span class="kind ${kind}">${(item.kind || "file").toUpperCase()}</span>
    <div class="meta">
      <div class="name" title="${esc(item.url)}">${esc(name)}</div>
      <div class="sub">${esc(mediaSub(item))}</div>
    </div>
    ${hasPicker ? "" : '<button class="get" type="button">Download</button>'}
  `;
  block.appendChild(head);

  if (!hasPicker) {
    head.querySelector(".get").addEventListener("click", (e) => doDownload(item, {}, e.currentTarget, name));
    return block;
  }

  // A real, scrollable list of every quality/format (IDM-style).
  const list = document.createElement("div");
  list.className = "formats";
  list.innerHTML = '<div class="floading">Loading formats…</div>';
  block.appendChild(list);

  send({
    type: "probeMedia",
    url: item.url,
    opts: { media_type: isPage ? "page" : item.kind, pageUrl: item.pageUrl },
  }).then((res) => {
    list.innerHTML = "";
    let formats = [];
    if (isPage) {
      formats = (res && res.formats) || [];
      if (res?.extractor) head.querySelector(".sub").textContent = `${res.extractor} · ${formats.length} formats`;
    } else {
      formats = ((res && res.variants) || [])
        .filter((v) => v.height)
        .map((v) => ({
          label: v.label || `${v.height}p`,
          height: v.height,
          ext: v.ext || "mp4",
          filesize: v.filesize || 0,
          approx: v.approx,
        }));
    }
    // Always offer a one-click "Best".
    list.appendChild(fmtRow(item, { best: true }, name));
    for (const f of formats) list.appendChild(fmtRow(item, f, name));
    if (!formats.length && res && res.ok === false) {
      const w = document.createElement("div");
      w.className = "floading";
      w.textContent = `Couldn't list formats: ${String(res.error || "").slice(0, 55)} — “Best” still works.`;
      list.appendChild(w);
    }
  });

  return block;
}

function fmtRow(item, f, name) {
  const row = document.createElement("div");
  row.className = "fmt";
  const q = f.best ? "⭐ Best" : (f.label || (f.height ? `${f.height}p` : "format"));
  const ext = f.best ? "" : (f.ext ? f.ext.toUpperCase() : "");
  const size = f.filesize ? (f.approx ? "~" : "") + humanSize(f.filesize) : "";
  let meta = [ext, size].filter(Boolean).join(" · ");
  if (f.needs_ffmpeg) meta += ` <span class="ff">⚙ needs ffmpeg</span>`;
  else if (f.audio_only) meta += ` <span class="fa">🎵 audio</span>`;
  else if (f.best) meta = "highest quality available";
  row.innerHTML = `
    <span class="fq">${esc(q)}</span>
    <span class="fmeta">${meta}</span>
    <button class="fdl" type="button" title="Download this format">⬇</button>
  `;
  let sel = {};
  if (!f.best) {
    if (f.audio_only) sel = { audio_only: true, format_id: f.format_id };
    else if (f.format_id) sel = { format_id: f.format_id };
    else if (f.height) sel = { height: f.height };
  }
  row.querySelector(".fdl").addEventListener("click", (e) => doDownload(item, sel, e.currentTarget, name));
  return row;
}

async function doDownload(item, sel, btn, name) {
  const old = btn.textContent;
  btn.disabled = true;
  btn.textContent = "…";
  const res = await send({ type: "downloadMedia", item, sel, tabId: activeTabId });
  btn.textContent = res && res.ok ? "✓" : "✗";
  $("statusDetail").textContent =
    res && res.ok ? `Queued: ${res.filename || name}` : `Failed: ${(res && res.error) || "is the app running?"}`;
  setTimeout(() => {
    btn.textContent = old;
    btn.disabled = false;
  }, 1600);
}

$("save").addEventListener("click", async () => {
  const port = Math.max(1024, Math.min(65535, parseInt($("port").value, 10) || 7373));
  await B.storage.sync.set({
    enabled: $("enabled").checked,
    captureDownloads: $("captureDownloads").checked,
    showVideoButton: $("showVideoButton").checked,
    showFloatingButton: $("showFloatingButton").checked,
    port,
    token: $("token").value.trim(),
  });
  $("port").value = port;
  await recheck();
  $("statusDetail").textContent = "Settings saved";
});

$("recheck").addEventListener("click", async () => {
  await recheck();
  await renderMedia();
});

load();
