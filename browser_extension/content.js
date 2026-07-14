/**
 * Magic Downloader — on-page controls (the visible "IDM" experience).
 *
 *  • A "⬇ Download" button appears on every <video> player (top-right).
 *  • A floating action button (bottom-right) shows how many videos/streams the
 *    extension sniffed on this page.
 *  • Clicking either opens a panel listing the real media (HLS/DASH/MP4/…) with
 *    a Download button per item — sent straight to the desktop app.
 */

(function () {
  if (window.__magicDownloaderInjected) return;
  window.__magicDownloaderInjected = true;

  const ROOT_ID = "magic-downloader-root";
  const MIN_VIDEO_W = 160;
  const MIN_VIDEO_H = 90;

  let appOnline = false;
  let appHasFfmpeg = true;
  let cfg = { enabled: true, showVideoButton: true, showFloatingButton: true };
  const videoOverlays = new Map(); // videoEl -> button
  // Drag offset (relative to each video's default top-right anchor) so the user
  // can reposition the on-video "Download" button; applies to all videos.
  let videoBtnOffset = { dx: 0, dy: 0 };
  let overlayDragging = false;
  let overlaySuppressClick = false;
  const VIDEO_BTN_OFFSET_KEY = "md_vbtn_offset";

  // ── messaging ─────────────────────────────────────────────────────────
  // Firefox = promise-based `browser`; Chrome/Edge MV3 = promise-based `chrome`.
  const B = (typeof browser !== "undefined" && browser) || chrome;
  function msg(payload) {
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
  const ping = () => msg({ type: "ping" });
  const getConfig = () => msg({ type: "getConfig" });
  const getMedia = () => msg({ type: "getMedia" });
  const downloadMedia = (item, sel) => msg({ type: "downloadMedia", item, sel });
  const sendUrl = (url, opts) => msg({ type: "add", url, opts });

  // Tell the background this page is showing a video → enables the yt-dlp
  // "download this page's video" path (the only thing that works on YouTube).
  let lastReported = "";
  function reportPageVideo() {
    const key = location.href + "|" + document.title;
    if (key === lastReported) return;
    lastReported = key;
    msg({ type: "reportPageVideo", pageUrl: location.href, title: document.title });
  }

  function guessName(url) {
    try {
      const u = new URL(url, location.href);
      const last = u.pathname.split("/").filter(Boolean).pop() || "download";
      return decodeURIComponent(last.split("?")[0]) || "download";
    } catch {
      return "download";
    }
  }

  // ── shared panel ──────────────────────────────────────────────────────
  let panel = null;

  function buildPanel(root) {
    panel = document.createElement("div");
    panel.id = "md-panel";
    panel.innerHTML = `
      <h3>Magic Downloader <button class="md-close" title="Close">×</button></h3>
      <div id="md-status" class="md-status">Checking app…</div>
      <div id="md-list"></div>
    `;
    root.appendChild(panel);
    panel.querySelector(".md-close").addEventListener("click", () => closePanel());
    panel.addEventListener("click", (e) => e.stopPropagation());
  }

  function openPanel() {
    if (!panel) return;
    positionPanel();
    panel.classList.add("md-open");
    refreshStatus();
    renderList();
  }
  function closePanel() {
    if (panel) panel.classList.remove("md-open");
  }
  function togglePanel() {
    if (!panel) return;
    if (panel.classList.contains("md-open")) closePanel();
    else openPanel();
  }

  async function refreshStatus() {
    const el = panel && panel.querySelector("#md-status");
    if (!el) return;
    const res = await ping();
    appOnline = !!res.ok;
    appHasFfmpeg = res.ffmpeg !== false;
    if (res.ok) {
      el.textContent = `Connected · port ${res.port || "7373"}${
        res.ffmpeg === false ? " · ⚠ ffmpeg missing (streams save as .ts)" : ""
      }`;
      el.className = "md-status ok";
    } else {
      el.textContent = "App not running. Start Magic Downloader on your PC.";
      el.className = "md-status bad";
    }
    updateFab();
  }

  function mediaLabel(item) {
    if (item.kind === "page") return "This page's video · pick quality in the popup";
    if (item.kind === "hls") return "HLS stream (adaptive quality)";
    if (item.kind === "dash") return "DASH stream (adaptive quality)";
    const size = item.size ? ` · ${humanSize(item.size)}` : "";
    return `${item.ext ? item.ext.toUpperCase() : "FILE"}${size}`;
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

  async function renderList(extraItems) {
    const listEl = panel && panel.querySelector("#md-list");
    if (!listEl) return;
    const res = await getMedia();
    let items = (res && res.items) || [];
    if (extraItems && extraItems.length) {
      const seen = new Set(items.map((i) => i.url));
      for (const ex of extraItems) if (!seen.has(ex.url)) items.push(ex);
    }
    listEl.innerHTML = "";
    if (!items.length) {
      const empty = document.createElement("div");
      empty.className = "md-empty";
      empty.textContent =
        "No video detected yet. Press ▶ Play on the video, then reopen — the stream shows up as it loads.";
      listEl.appendChild(empty);
      return;
    }
    for (const item of items) {
      listEl.appendChild(renderItem(item));
    }
  }

  function renderItem(item) {
    const row = document.createElement("div");
    row.className = "md-item";
    const kind = item.kind === "file" ? "file" : item.kind;
    const displayName = item.title || item.name || guessName(item.url);
    row.innerHTML = `
      <span class="md-kind ${kind}">${(item.kind || "file").toUpperCase()}</span>
      <div class="md-meta">
        <div class="md-name" title="${escapeHtml(item.url)}">${escapeHtml(displayName)}</div>
        <div class="md-sub">${escapeHtml(mediaLabel(item))}</div>
      </div>
      <button class="md-get" type="button">Download</button>
    `;
    const btn = row.querySelector(".md-get");
    btn.addEventListener("click", async (e) => {
      e.preventDefault();
      e.stopPropagation();
      btn.textContent = "…";
      btn.disabled = true;
      const res = await downloadMedia(item);
      btn.textContent = res && res.ok ? "Queued ✓" : "Failed";
      const st = panel.querySelector("#md-status");
      if (res && res.ok) {
        st.textContent = `Queued: ${res.filename || displayName}`;
        st.className = "md-status ok";
      } else {
        st.textContent = `Failed: ${(res && res.error) || "is the app running?"}`;
        st.className = "md-status bad";
      }
      setTimeout(() => {
        btn.textContent = "Download";
        btn.disabled = false;
      }, 1800);
    });
    return row;
  }

  function escapeHtml(s) {
    return (s || "").replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
    );
  }

  // ── floating action button (draggable) ────────────────────────────────
  let fab = null;
  const FAB_POS_KEY = "md_fab_pos";
  let suppressNextClick = false;

  function buildFab(root) {
    fab = document.createElement("button");
    fab.id = "md-fab";
    fab.type = "button";
    fab.title = "Magic Downloader — click to open · drag to move";
    fab.classList.add("offline");
    fab.innerHTML = `⬇ MD <span class="md-fab-count" style="display:none"></span>`;
    root.appendChild(fab);

    fab.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (suppressNextClick) {
        suppressNextClick = false;
        return; // this "click" was the end of a drag
      }
      togglePanel();
    });

    makeDraggable(fab);
    restoreFabPos();
  }

  function clampFab(left, top) {
    const w = fab.offsetWidth || 52;
    const h = fab.offsetHeight || 52;
    return [
      Math.max(2, Math.min(window.innerWidth - w - 2, left)),
      Math.max(2, Math.min(window.innerHeight - h - 2, top)),
    ];
  }

  function setFabPos(left, top) {
    const [l, t] = clampFab(left, top);
    fab.style.setProperty("left", l + "px", "important");
    fab.style.setProperty("top", t + "px", "important");
    fab.style.setProperty("right", "auto", "important");
    fab.style.setProperty("bottom", "auto", "important");
  }

  function restoreFabPos() {
    try {
      B.storage.local.get(FAB_POS_KEY).then((r) => {
        const pos = r && r[FAB_POS_KEY];
        if (pos && typeof pos.left === "number") setFabPos(pos.left, pos.top);
      });
    } catch (_) {
      /* storage unavailable */
    }
  }

  // Pointer-event drag: setPointerCapture routes every pointermove/up to the
  // element itself — even when the pointer travels over a <video>, canvas or
  // cross-origin content that would otherwise swallow document mouse events.
  // `onStart(rect)` primes positioning; `onMove(dx,dy)` moves; `onEnd(moved)`
  // finalises. Returns nothing.
  function attachDrag(el, { onStart, onMove, onEnd }) {
    let dragging = false;
    let moved = false;
    let startX = 0;
    let startY = 0;
    let pid = null;

    el.addEventListener("pointerdown", (e) => {
      if (e.button != null && e.button !== 0) return; // left button / touch / pen only
      dragging = true;
      moved = false;
      startX = e.clientX;
      startY = e.clientY;
      pid = e.pointerId;
      try {
        el.setPointerCapture(pid);
      } catch (_) {
        /* capture unsupported → falls back to normal bubbling */
      }
      onStart(el.getBoundingClientRect());
      e.preventDefault();
      e.stopPropagation();
    });

    el.addEventListener("pointermove", (e) => {
      if (!dragging) return;
      const dx = e.clientX - startX;
      const dy = e.clientY - startY;
      if (Math.abs(dx) > 3 || Math.abs(dy) > 3) moved = true;
      onMove(dx, dy);
      e.preventDefault();
    });

    function finish(e) {
      if (!dragging) return;
      dragging = false;
      try {
        if (pid != null) el.releasePointerCapture(pid);
      } catch (_) {
        /* ignore */
      }
      pid = null;
      onEnd(moved);
    }
    el.addEventListener("pointerup", finish);
    el.addEventListener("pointercancel", finish);
  }

  function makeDraggable(el) {
    let origLeft = 0;
    let origTop = 0;
    attachDrag(el, {
      onStart: (rect) => {
        origLeft = rect.left;
        origTop = rect.top;
        setFabPos(rect.left, rect.top); // switch from right/bottom to left/top
      },
      onMove: (dx, dy) => setFabPos(origLeft + dx, origTop + dy),
      onEnd: (moved) => {
        if (!moved) return;
        suppressNextClick = true;
        const rect = el.getBoundingClientRect();
        try {
          B.storage.local.set({ [FAB_POS_KEY]: { left: rect.left, top: rect.top } });
        } catch (_) {
          /* ignore */
        }
        if (panel && panel.classList.contains("md-open")) positionPanel();
      },
    });
  }

  function positionPanel() {
    if (!panel || !fab) return;
    const r = fab.getBoundingClientRect();
    const pw = 340;
    const ph = Math.min(window.innerHeight * 0.7, 420);
    let left = r.right - pw;
    let top = r.top - ph - 8; // prefer above the button
    if (top < 8) top = r.bottom + 8; // otherwise below it
    left = Math.max(8, Math.min(window.innerWidth - pw - 8, left));
    top = Math.max(8, Math.min(window.innerHeight - 60, top));
    panel.style.setProperty("left", left + "px", "important");
    panel.style.setProperty("top", top + "px", "important");
    panel.style.setProperty("right", "auto", "important");
    panel.style.setProperty("bottom", "auto", "important");
  }

  async function updateFab() {
    if (!fab) return;
    fab.classList.toggle("offline", !appOnline);
    const res = await getMedia();
    const count = ((res && res.items) || []).length;
    const badge = fab.querySelector(".md-fab-count");
    if (count > 0) {
      badge.textContent = count;
      badge.style.display = "inline-block";
    } else {
      badge.style.display = "none";
    }
  }

  // ── per-<video> overlay button ────────────────────────────────────────
  function videoDirectItem(video) {
    // If the video element exposes a real http(s) src, offer it directly so
    // the button works even before/without network sniffing.
    let src = video.currentSrc || video.src || "";
    if (!src) {
      const source = video.querySelector("source[src]");
      if (source) src = source.src;
    }
    if (!src || !/^https?:\/\//i.test(src)) return null; // blob:/mediasource → rely on sniffing
    return {
      url: src,
      kind: /\.m3u8/i.test(src) ? "hls" : /\.mpd/i.test(src) ? "dash" : "file",
      mclass: "video",
      ext: (guessName(src).split(".").pop() || "").toLowerCase(),
      name: guessName(src),
      title: document.title || "",
      pageUrl: location.href,
      size: 0,
    };
  }

  function ensureOverlay(video) {
    if (videoOverlays.has(video)) return videoOverlays.get(video);
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "md-video-overlay";
    btn.title = "Download this video · drag to move";
    btn.innerHTML = `⬇ Download <span class="md-badge" style="display:none"></span>`;
    btn._mdVideo = video;
    document.documentElement.appendChild(btn);
    btn.addEventListener("click", async (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (overlaySuppressClick) {
        overlaySuppressClick = false;
        return; // was the end of a drag
      }
      const direct = videoDirectItem(video);
      openPanel();
      renderList(direct ? [direct] : []);
    });
    makeVideoBtnDraggable(btn);
    videoOverlays.set(video, btn);
    return btn;
  }

  function overlayAnchor(rect) {
    // default top-right of the video, then the user's saved drag offset
    return {
      left: Math.max(6, Math.min(window.innerWidth - 130, rect.right - 128)) + videoBtnOffset.dx,
      top: Math.max(6, rect.top + 8) + videoBtnOffset.dy,
    };
  }

  function positionOverlay(video, btn) {
    if (overlayDragging) return; // don't fight the user's drag
    const rect = video.getBoundingClientRect();
    const visible =
      rect.width >= MIN_VIDEO_W &&
      rect.height >= MIN_VIDEO_H &&
      rect.bottom > 0 &&
      rect.right > 0 &&
      rect.top < window.innerHeight &&
      rect.left < window.innerWidth;
    if (!visible) {
      btn.classList.remove("md-show");
      return;
    }
    btn.classList.add("md-show");
    const a = overlayAnchor(rect);
    const w = btn.offsetWidth || 120;
    const h = btn.offsetHeight || 30;
    const left = Math.max(2, Math.min(window.innerWidth - w - 2, a.left));
    const top = Math.max(2, Math.min(window.innerHeight - h - 2, a.top));
    btn.style.setProperty("top", `${top}px`, "important");
    btn.style.setProperty("left", `${left}px`, "important");
  }

  function makeVideoBtnDraggable(el) {
    let origLeft = 0;
    let origTop = 0;
    attachDrag(el, {
      onStart: (rect) => {
        overlayDragging = true; // freeze positionOverlay() while we drag
        origLeft = rect.left;
        origTop = rect.top;
      },
      onMove: (dx, dy) => {
        const w = el.offsetWidth || 120;
        const h = el.offsetHeight || 30;
        const nl = Math.max(2, Math.min(window.innerWidth - w - 2, origLeft + dx));
        const nt = Math.max(2, Math.min(window.innerHeight - h - 2, origTop + dy));
        el.style.setProperty("left", nl + "px", "important");
        el.style.setProperty("top", nt + "px", "important");
      },
      onEnd: (moved) => {
        overlayDragging = false;
        if (!moved) return;
        overlaySuppressClick = true;
        // Save the offset relative to this video's default anchor so every
        // video button moves consistently and survives repositioning ticks.
        const video = el._mdVideo;
        if (!video) return;
        const vr = video.getBoundingClientRect();
        const baseLeft = Math.max(6, Math.min(window.innerWidth - 130, vr.right - 128));
        const baseTop = Math.max(6, vr.top + 8);
        const br = el.getBoundingClientRect();
        videoBtnOffset = { dx: Math.round(br.left - baseLeft), dy: Math.round(br.top - baseTop) };
        try {
          B.storage.local.set({ [VIDEO_BTN_OFFSET_KEY]: videoBtnOffset });
        } catch (_) {
          /* ignore */
        }
        repositionAll();
      },
    });
  }

  async function refreshOverlays() {
    if (!cfg.enabled || cfg.showVideoButton === false) {
      for (const [, btn] of videoOverlays) btn.remove();
      videoOverlays.clear();
      return 0;
    }
    const videos = Array.from(document.querySelectorAll("video"));
    if (videos.length) reportPageVideo();
    // Drop overlays for removed videos.
    for (const [vid, btn] of [...videoOverlays.entries()]) {
      if (!videos.includes(vid) || !document.contains(vid)) {
        btn.remove();
        videoOverlays.delete(vid);
      }
    }
    let count = 0;
    const res = await getMedia();
    const streamCount = ((res && res.items) || []).length;
    for (const video of videos) {
      const btn = ensureOverlay(video);
      positionOverlay(video, btn);
      const badge = btn.querySelector(".md-badge");
      if (streamCount > 0) {
        badge.textContent = streamCount;
        badge.style.display = "inline-block";
      } else {
        badge.style.display = "none";
      }
      count++;
    }
    return count;
  }

  function repositionAll() {
    for (const [video, btn] of videoOverlays.entries()) positionOverlay(video, btn);
  }

  // ── boot ──────────────────────────────────────────────────────────────
  async function build() {
    if (document.getElementById(ROOT_ID)) return;
    try {
      const c = await getConfig();
      if (c && typeof c === "object") cfg = { ...cfg, ...c };
    } catch (_) {
      /* use defaults */
    }
    if (cfg.enabled === false) return; // extension turned off in popup

    // Restore the saved on-video button drag offset.
    try {
      B.storage.local.get(VIDEO_BTN_OFFSET_KEY).then((r) => {
        const o = r && r[VIDEO_BTN_OFFSET_KEY];
        if (o && typeof o.dx === "number") videoBtnOffset = o;
      });
    } catch (_) {
      /* storage unavailable */
    }

    const cfgHost = document.createElement("div");
    cfgHost.id = ROOT_ID;
    document.documentElement.appendChild(cfgHost);

    buildPanel(cfgHost);
    if (cfg.showFloatingButton !== false) buildFab(cfgHost); // optional

    document.addEventListener("click", (e) => {
      const root = document.getElementById(ROOT_ID);
      if (root && !root.contains(e.target)) closePanel();
    }, true);

    window.addEventListener("scroll", repositionAll, true);
    window.addEventListener("resize", repositionAll, true);

    // Observe DOM for dynamically added <video> players.
    const mo = new MutationObserver(() => scheduleOverlayRefresh());
    mo.observe(document.documentElement, { childList: true, subtree: true });

    refreshStatus();
    refreshOverlays();
    setInterval(() => {
      refreshOverlays();
      updateFab();
    }, 1500);
  }

  let overlayTimer = null;
  function scheduleOverlayRefresh() {
    if (overlayTimer) return;
    overlayTimer = setTimeout(() => {
      overlayTimer = null;
      refreshOverlays();
    }, 400);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", build);
  } else {
    build();
  }
})();
