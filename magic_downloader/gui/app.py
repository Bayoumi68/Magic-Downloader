"""Main window — Internet Download Manager style layout."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from magic_downloader.gui import quiet_dialogs as messagebox
from urllib.parse import urlparse

from magic_downloader.browser_server import BrowserAPIServer
from magic_downloader.config import ROOT
from magic_downloader.gui import theme as T
from magic_downloader.gui.dialogs import (
    AddDownloadDialog,
    AddVideoDialog,
    CaptureDialog,
    DownloadProgressDialog,
    SettingsDialog,
)
from magic_downloader.gui.widgets import ProgressBar, SegmentBar, ToolbarButton
from magic_downloader.manager import DownloadManager
from magic_downloader.models import (
    DownloadJob,
    DownloadStatus,
    format_bytes,
    format_eta,
    format_speed,
)

COLUMNS = ("filename", "size", "status", "progress", "speed", "eta", "conn", "category")

# Sidebar filter keys
FILTER_ALL = "all"
FILTER_DOWNLOADING = "downloading"
FILTER_QUEUED = "queued"
FILTER_PAUSED = "paused"
FILTER_COMPLETE = "complete"
FILTER_FAILED = "failed"
FILTER_CAT_PREFIX = "cat:"


class MagicDownloaderApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        # Silence Tk's bell so no widget ever beeps (backspace in an empty
        # field, a Spinbox hitting its limit, etc.). Replace the built-in
        # `bell` command with a no-op — purely removes the sound.
        try:
            self.tk.eval("catch {rename bell {}}; proc bell args {}")
        except tk.TclError:
            pass
        self.title("Magic Downloader — Multi-connection Download Manager")
        self.geometry("1200x700")
        self.minsize(960, 560)
        self.configure(bg=T.BG)
        self._set_window_icon()

        self.manager = DownloadManager()
        self.manager.add_listener(self._schedule_refresh)
        self._filter = FILTER_ALL
        self._sort_col = "filename"
        self._sort_reverse = False
        self._browser: BrowserAPIServer | None = None
        self._toast_after: str | None = None
        self._tray = None
        self._tray_thread: object | None = None
        self._quitting = False
        self._single_instance = None
        self._capture_queue: list[dict] = []
        self._capture_active = False
        self._progress_dialogs: dict[str, DownloadProgressDialog] = {}

        self._build_menu()
        self._build_toolbar()
        self._build_body()
        self._build_statusbar()
        self._apply_style()
        self._start_browser_server()
        self._setup_tray()

        self.bind("<Control-n>", lambda e: self._add_url())
        self.bind("<Control-N>", lambda e: self._add_url())
        self.bind("<Control-d>", lambda e: self._add_video())
        self.bind("<Control-D>", lambda e: self._add_video())
        self.bind("<Control-v>", lambda e: self._paste_url())
        self.bind("<Control-V>", lambda e: self._paste_url())
        self.bind("<Delete>", lambda e: self._delete_selected())
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Unmap>", self._on_minimize)

        self._refresh_all()
        self.after(400, self._tick)

    # ── chrome ──────────────────────────────────────────────────────────

    def _apply_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Treeview",
            rowheight=28,
            font=T.FONT_UI,
            background=T.BG_LIST,
            fieldbackground=T.BG_LIST,
            foreground=T.FG,
            borderwidth=0,
        )
        style.configure(
            "Treeview.Heading",
            font=T.FONT_UI_BOLD,
            background=T.BG_STATUS,
            foreground=T.FG,
            relief="flat",
            borderwidth=1,
        )
        style.map(
            "Treeview",
            background=[("selected", T.SELECT)],
            foreground=[("selected", T.SELECT_FG)],
        )
        style.configure("TScrollbar", troughcolor=T.BG, background=T.BORDER)
        style.configure("Vertical.TScrollbar", background=T.BORDER)
        style.configure("TButton", font=T.FONT_UI, padding=4)
        style.configure("TEntry", font=T.FONT_UI)
        style.configure("TSpinbox", font=T.FONT_UI)
        style.configure("TCombobox", font=T.FONT_UI)

    def _build_menu(self) -> None:
        menubar = tk.Menu(self)
        tasks = tk.Menu(menubar, tearoff=0)
        tasks.add_command(label="Add new download…\tCtrl+N", command=self._add_url)
        tasks.add_command(label="Download video (choose quality)…\tCtrl+D", command=self._add_video)
        tasks.add_command(label="Add from clipboard\tCtrl+V", command=self._paste_url)
        tasks.add_separator()
        tasks.add_command(label="Install browser extension…", command=self._open_extension_help)
        tasks.add_command(label="Options…", command=self._open_settings)
        tasks.add_separator()
        tasks.add_command(label="Hide to tray", command=self._hide_to_tray)
        tasks.add_command(label="Exit", command=self._quit)
        menubar.add_cascade(label="Tasks", menu=tasks)

        downloads = tk.Menu(menubar, tearoff=0)
        downloads.add_command(label="Resume / Start", command=self._resume_selected)
        downloads.add_command(label="Pause", command=self._pause_selected)
        downloads.add_command(label="Stop / Cancel", command=self._cancel_selected)
        downloads.add_command(label="Delete", command=self._delete_selected)
        downloads.add_separator()
        downloads.add_command(label="Open complete file", command=self._open_file)
        downloads.add_command(label="Open containing folder", command=self._open_folder)
        menubar.add_cascade(label="Downloads", menu=downloads)

        help_m = tk.Menu(menubar, tearoff=0)
        help_m.add_command(label="About Magic Downloader", command=self._about)
        menubar.add_cascade(label="Help", menu=help_m)
        self.config(menu=menubar)

    def _build_toolbar(self) -> None:
        bar = tk.Frame(self, bg=T.BG_TOOLBAR, height=72)
        bar.pack(fill=tk.X)
        bar.pack_propagate(False)

        # Brand
        brand = tk.Frame(bar, bg=T.BG_TOOLBAR)
        brand.pack(side=tk.LEFT, padx=(12, 8), pady=8)
        tk.Label(
            brand,
            text="Magic\nDownloader",
            bg=T.BG_TOOLBAR,
            fg=T.FG_ON_DARK,
            font=("Segoe UI", 10, "bold"),
            justify=tk.LEFT,
        ).pack()

        sep = tk.Frame(bar, bg="#1e3f73", width=1)
        sep.pack(side=tk.LEFT, fill=tk.Y, pady=10, padx=4)

        actions = [
            ("add", "➕", "Add URL", self._add_url),
            ("video", "🎬", "Video", self._add_video),
            ("resume", "▶", "Resume", self._resume_selected),
            ("pause", "⏸", "Pause", self._pause_selected),
            ("stop", "⏹", "Stop", self._cancel_selected),
            ("delete", "🗑", "Delete", self._delete_selected),
            ("folder", "📂", "Folder", self._open_folder),
            ("open", "📄", "Open", self._open_file),
            ("browser", "🌐", "Browser", self._open_extension_help),
            ("options", "⚙", "Options", self._open_settings),
        ]
        self._buttons: dict[str, ToolbarButton] = {}
        for key, icon, text, cmd in actions:
            btn = ToolbarButton(bar, icon, text, cmd)
            btn.pack(side=tk.LEFT, padx=1, pady=4)
            self._buttons[key] = btn

        # Live speed badge on the right
        right = tk.Frame(bar, bg=T.BG_TOOLBAR)
        right.pack(side=tk.RIGHT, padx=16)
        self.browser_badge = tk.Label(
            right,
            text="Browser: off",
            bg=T.BG_TOOLBAR,
            fg="#ffb84d",
            font=T.FONT_SMALL,
        )
        self.browser_badge.pack(anchor="e")
        tk.Label(
            right, text="TOTAL SPEED", bg=T.BG_TOOLBAR, fg="#a8c4e8", font=T.FONT_SMALL
        ).pack(anchor="e")
        self.speed_badge = tk.Label(
            right,
            text="0 B/s",
            bg=T.BG_TOOLBAR,
            fg="#7dff9a",
            font=("Segoe UI", 16, "bold"),
        )
        self.speed_badge.pack(anchor="e")

    def _build_body(self) -> None:
        body = tk.Frame(self, bg=T.BG)
        body.pack(fill=tk.BOTH, expand=True)

        # ── Left category sidebar (IDM hallmark) ──
        side = tk.Frame(body, bg=T.BG_SIDEBAR, width=200)
        side.pack(side=tk.LEFT, fill=tk.Y)
        side.pack_propagate(False)

        tk.Label(
            side,
            text="  CATEGORIES",
            bg=T.BG_DARK,
            fg=T.FG_ON_DARK,
            font=T.FONT_SMALL,
            anchor="w",
        ).pack(fill=tk.X)

        self.cat_list = tk.Listbox(
            side,
            bg=T.BG_SIDEBAR,
            fg=T.FG,
            font=T.FONT_UI,
            selectbackground=T.SELECT,
            selectforeground=T.SELECT_FG,
            activestyle="none",
            highlightthickness=0,
            bd=0,
            exportselection=False,
        )
        self.cat_list.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)
        self.cat_list.bind("<<ListboxSelect>>", self._on_category_select)
        self.cat_list.bind("<Button-3>", self._sidebar_context)

        # Sidebar items are built dynamically from the current categories.
        self._sidebar_items: list[tuple[str, str]] = []
        self._sidebar_sig: tuple | None = None
        self._rebuild_sidebar()

        # Vertical separator
        tk.Frame(body, bg=T.BORDER, width=1).pack(side=tk.LEFT, fill=tk.Y)

        # ── Right: list + detail ──
        right = tk.Frame(body, bg=T.BG)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # List header strip
        list_hdr = tk.Frame(right, bg=T.BG_STATUS, height=28)
        list_hdr.pack(fill=tk.X)
        list_hdr.pack_propagate(False)
        self.list_title = tk.Label(
            list_hdr,
            text="All downloads",
            bg=T.BG_STATUS,
            fg=T.FG,
            font=T.FONT_UI_BOLD,
            anchor="w",
        )
        self.list_title.pack(side=tk.LEFT, padx=10)
        self.list_count = tk.Label(
            list_hdr, text="0 items", bg=T.BG_STATUS, fg=T.FG_MUTED, font=T.FONT_SMALL
        )
        self.list_count.pack(side=tk.RIGHT, padx=10)

        # Download list
        list_wrap = tk.Frame(right, bg=T.BG_LIST)
        list_wrap.pack(fill=tk.BOTH, expand=True)

        self.tree = ttk.Treeview(
            list_wrap,
            columns=COLUMNS,
            show="headings",
            selectmode="extended",
        )
        headings = {
            "filename": ("File name", 260),
            "size": ("Size", 130),
            "status": ("Status", 120),
            "progress": ("Progress", 90),
            "speed": ("Speed", 100),
            "eta": ("Time left", 90),
            "conn": ("Parts", 55),
            "category": ("Category", 100),
        }
        for key, (label, width) in headings.items():
            self.tree.heading(key, text=label, command=lambda k=key: self._sort_by(k))
            stretch = key == "filename"
            self.tree.column(key, width=width, minwidth=40, stretch=stretch, anchor="w")

        yscroll = ttk.Scrollbar(list_wrap, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=yscroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        yscroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Row tags for status colors
        self.tree.tag_configure("Downloading", foreground=T.GREEN)
        self.tree.tag_configure("Connecting", foreground=T.BLUE)
        self.tree.tag_configure("Processing", foreground=T.BLUE)
        self.tree.tag_configure("Paused", foreground=T.ORANGE)
        self.tree.tag_configure("Complete", foreground="#1a7a32")
        self.tree.tag_configure("Failed", foreground=T.RED)
        self.tree.tag_configure("Cancelled", foreground=T.GRAY)
        self.tree.tag_configure("Queued", foreground=T.GRAY)

        self.tree.bind("<<TreeviewSelect>>", lambda e: self._on_selection_change())
        self.tree.bind("<Double-1>", lambda e: self._on_double_click())
        self.tree.bind("<Button-3>", self._context_menu)

        self._ctx = tk.Menu(self, tearoff=0)
        self._ctx.add_command(label="▶  Resume / Start", command=self._resume_selected)
        self._ctx.add_command(label="⏸  Pause", command=self._pause_selected)
        self._ctx.add_command(label="⏹  Stop", command=self._cancel_selected)
        self._ctx.add_command(label="🔁  Re-download", command=self._redownload_selected)
        self._ctx.add_command(label="🎬  Choose quality…", command=self._choose_quality)
        self._ctx.add_command(label="📊  Show progress window", command=self._show_progress_selected)
        self._ctx.add_separator()
        self._ctx.add_command(label="✏  Rename…", command=self._rename_selected)
        self._ctx.add_command(label="📁  Move to…", command=self._move_selected)
        self._ctx.add_command(label="📄  Open file", command=self._open_file)
        self._ctx.add_command(label="📂  Open folder", command=self._open_folder)
        self._ctx.add_separator()
        self._ctx.add_command(label="🗑  Delete from list", command=self._delete_selected)
        self._ctx.add_command(label="🗑  Delete + remove files", command=self._delete_with_files)

        # ── Bottom detail panel (IDM-style) ──
        tk.Frame(right, bg=T.BORDER, height=1).pack(fill=tk.X)
        detail = tk.Frame(right, bg=T.BG_DETAIL, height=150)
        detail.pack(fill=tk.X, side=tk.BOTTOM)
        detail.pack_propagate(False)

        # Left info
        info = tk.Frame(detail, bg=T.BG_DETAIL)
        info.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=12, pady=8)

        self.detail_name = tk.Label(
            info, text="No download selected", bg=T.BG_DETAIL, fg=T.FG, font=T.FONT_TITLE, anchor="w"
        )
        self.detail_name.pack(fill=tk.X)
        self.detail_url = tk.Label(
            info, text="", bg=T.BG_DETAIL, fg=T.BLUE, font=T.FONT_SMALL, anchor="w"
        )
        self.detail_url.pack(fill=tk.X)
        self.detail_path = tk.Label(
            info, text="", bg=T.BG_DETAIL, fg=T.FG_MUTED, font=T.FONT_SMALL, anchor="w"
        )
        self.detail_path.pack(fill=tk.X)

        self.progress_bar = ProgressBar(info, height=20)
        self.progress_bar.pack(fill=tk.X, pady=(8, 4))

        self.detail_stats = tk.Label(
            info, text="", bg=T.BG_DETAIL, fg=T.FG, font=T.FONT_SMALL, anchor="w"
        )
        self.detail_stats.pack(fill=tk.X)

        # Right: segment map (signature IDM look)
        seg_fr = tk.Frame(detail, bg=T.BG_DETAIL, width=320)
        seg_fr.pack(side=tk.RIGHT, fill=tk.Y, padx=12, pady=8)
        seg_fr.pack_propagate(False)
        tk.Label(
            seg_fr,
            text="Download progress (connections)",
            bg=T.BG_DETAIL,
            fg=T.FG_MUTED,
            font=T.FONT_SMALL,
            anchor="w",
        ).pack(fill=tk.X)
        self.segment_bar = SegmentBar(seg_fr, height=36)
        self.segment_bar.pack(fill=tk.X, pady=6)
        self.seg_legend = tk.Label(
            seg_fr,
            text="Green = downloaded block   ·   Dark = remaining",
            bg=T.BG_DETAIL,
            fg=T.FG_MUTED,
            font=("Segoe UI", 8),
            anchor="w",
        )
        self.seg_legend.pack(fill=tk.X)

    def _build_statusbar(self) -> None:
        self._status_frame = tk.Frame(self, bg=T.BG_STATUS, height=26)
        self._status_frame.pack(fill=tk.X, side=tk.BOTTOM)
        self._status_frame.pack_propagate(False)
        self.status_var = tk.StringVar(value="Ready")
        tk.Label(
            self._status_frame,
            textvariable=self.status_var,
            bg=T.BG_STATUS,
            fg=T.FG,
            font=T.FONT_SMALL,
            anchor="w",
        ).pack(side=tk.LEFT, padx=10)
        self.status_right = tk.Label(
            self._status_frame, text="", bg=T.BG_STATUS, fg=T.FG_MUTED, font=T.FONT_SMALL, anchor="e"
        )
        self.status_right.pack(side=tk.RIGHT, padx=10)

        # Toast strip for browser captures
        self.toast_var = tk.StringVar(value="")
        self.toast_bar = tk.Label(
            self,
            textvariable=self.toast_var,
            bg="#1a7a32",
            fg="white",
            font=T.FONT_UI_BOLD,
            anchor="w",
            padx=12,
            pady=6,
        )

    # ── filtering ───────────────────────────────────────────────────────

    _CAT_ICONS = {
        "General": "📁", "Compressed": "📦", "Documents": "📄",
        "Music": "🎵", "Video": "🎬",
    }

    def _rebuild_sidebar(self) -> None:
        """(Re)build the sidebar so it reflects the current categories,
        including any the user added. Preserves the active filter."""
        cats = list((self.manager.settings.get("category_paths") or {}).keys())
        items: list[tuple[str, str]] = [
            ("📥  All downloads", FILTER_ALL),
            ("⬇  Downloading", FILTER_DOWNLOADING),
            ("⏳  Queued", FILTER_QUEUED),
            ("⏸  Paused", FILTER_PAUSED),
            ("✅  Complete", FILTER_COMPLETE),
            ("❌  Failed / Cancelled", FILTER_FAILED),
            ("──  Categories  ──", ""),
        ]
        counts: dict[str, int] = {}
        for j in self.manager.jobs:
            counts[j.category] = counts.get(j.category, 0) + 1
        for c in cats:
            icon = self._CAT_ICONS.get(c, "📂")
            n = counts.get(c, 0)
            label = f"{icon}  {c}" + (f"  ({n})" if n else "")
            items.append((label, FILTER_CAT_PREFIX + c))
        # Show categories that have files but aren't in category_paths.
        for c in sorted(counts):
            if c not in cats:
                items.append((f"📂  {c}  ({counts[c]})", FILTER_CAT_PREFIX + c))

        self._sidebar_items = items
        self._sidebar_sig = (tuple(cats), tuple(sorted(counts.items())))
        self.cat_list.delete(0, tk.END)
        for label, _key in items:
            self.cat_list.insert(tk.END, label)
        # Restore the selection matching the active filter.
        for i, (_lbl, key) in enumerate(items):
            if key == self._filter:
                self.cat_list.selection_clear(0, tk.END)
                self.cat_list.selection_set(i)
                break
        else:
            self.cat_list.selection_set(0)

    def _on_category_select(self, _event=None) -> None:
        sel = self.cat_list.curselection()
        if not sel:
            return
        idx = sel[0]
        _label, key = self._sidebar_items[idx]
        if not key:
            return
        self._filter = key
        # Update list title
        self.list_title.configure(text=_label.strip())
        self._refresh_tree()

    def _filtered_jobs(self) -> list[DownloadJob]:
        jobs = list(self.manager.jobs)
        f = self._filter
        if f == FILTER_ALL:
            return jobs
        if f == FILTER_DOWNLOADING:
            return [
                j
                for j in jobs
                if j.status in (DownloadStatus.DOWNLOADING, DownloadStatus.CONNECTING)
            ]
        if f == FILTER_QUEUED:
            return [j for j in jobs if j.status == DownloadStatus.QUEUED]
        if f == FILTER_PAUSED:
            return [j for j in jobs if j.status == DownloadStatus.PAUSED]
        if f == FILTER_COMPLETE:
            return [j for j in jobs if j.status == DownloadStatus.COMPLETE]
        if f == FILTER_FAILED:
            return [
                j
                for j in jobs
                if j.status in (DownloadStatus.FAILED, DownloadStatus.CANCELLED)
            ]
        if f.startswith(FILTER_CAT_PREFIX):
            cat = f[len(FILTER_CAT_PREFIX) :]
            return [j for j in jobs if j.category == cat]
        return jobs

    # ── refresh ─────────────────────────────────────────────────────────

    def _schedule_refresh(self) -> None:
        try:
            self.after(0, self._refresh_all)
        except tk.TclError:
            pass

    def _tick(self) -> None:
        self._refresh_all()
        self.after(400, self._tick)

    def _refresh_all(self) -> None:
        self._maybe_rebuild_sidebar()
        self._refresh_tree()
        self._update_detail()
        self._update_status()
        self._update_toolbar_state()
        self._update_progress_dialogs()

    def _on_selection_change(self) -> None:
        self._update_detail()
        self._update_toolbar_state()

    def _update_toolbar_state(self) -> None:
        """Dim toolbar buttons whose action doesn't apply to the current
        selection, so a coloured button never looks active while it's inert."""
        if not getattr(self, "_buttons", None):
            return
        sel = [j for j in (self.manager.get_job(i) for i in self._selected_ids()) if j]
        S = DownloadStatus

        def any_in(*statuses) -> bool:
            return any(j.status in statuses for j in sel)

        self._buttons["resume"].set_enabled(any_in(S.PAUSED, S.FAILED, S.CANCELLED, S.QUEUED))
        self._buttons["pause"].set_enabled(any_in(S.DOWNLOADING, S.CONNECTING, S.QUEUED))
        self._buttons["stop"].set_enabled(any(j.status not in (S.COMPLETE, S.CANCELLED) for j in sel))
        self._buttons["delete"].set_enabled(bool(sel))
        self._buttons["open"].set_enabled(any_in(S.COMPLETE))
        # add / video / folder / browser / options are always applicable.

    def _show_progress_selected(self) -> None:
        for jid in self._selected_ids():
            self._open_progress(jid)

    def _on_double_click(self) -> None:
        ids = self._selected_ids()
        if not ids:
            return
        job = self.manager.get_job(ids[0])
        if not job:
            return
        # Complete → open the file; still going → open its progress window.
        if job.status == DownloadStatus.COMPLETE:
            self._open_file()
        else:
            self._open_progress(job.id)

    def _open_progress(self, job_id: str) -> None:
        """Pop (or focus) a per-download progress window, IDM-style."""
        if not self.manager.settings.get("show_progress_dialog", True):
            return
        existing = self._progress_dialogs.get(job_id)
        if existing is not None and existing.winfo_exists():
            existing.deiconify()
            existing.lift()
            return
        dlg = DownloadProgressDialog(self, self.manager, job_id, self._open_path)
        self._progress_dialogs[job_id] = dlg

    def _update_progress_dialogs(self) -> None:
        for jid, dlg in list(self._progress_dialogs.items()):
            try:
                if not dlg.winfo_exists():
                    del self._progress_dialogs[jid]
                    continue
                dlg.update_view()
            except tk.TclError:
                self._progress_dialogs.pop(jid, None)

    def _maybe_rebuild_sidebar(self) -> None:
        cats = tuple((self.manager.settings.get("category_paths") or {}).keys())
        counts: dict[str, int] = {}
        for j in self.manager.jobs:
            counts[j.category] = counts.get(j.category, 0) + 1
        sig = (cats, tuple(sorted(counts.items())))
        if sig != self._sidebar_sig:
            self._rebuild_sidebar()

    def _selected_ids(self) -> list[str]:
        return list(self.tree.selection())

    def _refresh_tree(self) -> None:
        selected = set(self.tree.selection())
        try:
            yview = self.tree.yview()
        except tk.TclError:
            yview = (0.0, 1.0)

        jobs = self._filtered_jobs()
        existing = set(self.tree.get_children())
        job_ids = {j.id for j in jobs}

        for iid in existing - job_ids:
            self.tree.delete(iid)

        for job in jobs:
            values = self._job_row(job)
            tag = job.status.value
            if job.id in existing:
                self.tree.item(job.id, values=values, tags=(tag,))
            else:
                self.tree.insert("", "end", iid=job.id, values=values, tags=(tag,))

        for idx, job in enumerate(jobs):
            try:
                self.tree.move(job.id, "", idx)
            except tk.TclError:
                pass

        for iid in selected:
            if self.tree.exists(iid):
                self.tree.selection_add(iid)

        try:
            self.tree.yview_moveto(yview[0])
        except tk.TclError:
            pass

        self.list_count.configure(text=f"{len(jobs)} item{'s' if len(jobs) != 1 else ''}")

    def _job_row(self, job: DownloadJob) -> tuple:
        if job.status == DownloadStatus.COMPLETE:
            size = format_bytes(job.total_size or job.downloaded)
        elif job.total_size:
            size = f"{format_bytes(job.downloaded)} / {format_bytes(job.total_size)}"
        elif job.downloaded:
            size = format_bytes(job.downloaded)
        else:
            size = "Unknown"

        status = job.status.value
        if job.status == DownloadStatus.FAILED and job.error:
            status = f"Failed"
        elif job.status == DownloadStatus.PROCESSING:
            status = "Merging…"

        if job.is_stream and job.media_meta.get("seg_total"):
            pct = job.progress
            filled = int(pct / 10)
            bar = "█" * filled + "░" * (10 - filled)
            progress = f"{bar} {pct:.0f}%"
        elif job.total_size:
            # Visual mini bar in text
            pct = job.progress
            filled = int(pct / 10)
            bar = "█" * filled + "░" * (10 - filled)
            progress = f"{bar} {pct:.0f}%"
        elif job.downloaded:
            progress = format_bytes(job.downloaded)
        else:
            progress = "—"

        speed = (
            format_speed(job.speed_bps)
            if job.status == DownloadStatus.DOWNLOADING
            else "—"
        )
        eta = (
            format_eta(job.eta_seconds)
            if job.status == DownloadStatus.DOWNLOADING
            else "—"
        )
        if job.is_stream:
            conn = job.media_type.upper()
        else:
            conn = str(job.connections) if job.supports_ranges else "1"
        return (job.filename, size, status, progress, speed, eta, conn, job.category)

    def _update_detail(self) -> None:
        ids = self._selected_ids()
        if not ids:
            # If one active download, show that
            active = [
                j
                for j in self.manager.jobs
                if j.status in (DownloadStatus.DOWNLOADING, DownloadStatus.CONNECTING)
            ]
            job = active[0] if len(active) == 1 else None
        else:
            job = self.manager.get_job(ids[0])

        if not job:
            self.detail_name.configure(text="No download selected")
            self.detail_url.configure(text="Select a download or click Add URL to begin")
            self.detail_path.configure(text="")
            self.detail_stats.configure(text="")
            self.progress_bar.set_progress(0)
            self.segment_bar.set_job(None)
            return

        self.detail_name.configure(text=job.filename)
        self.detail_url.configure(text=job.url)
        self.detail_path.configure(text=f"Save to: {job.save_path}")

        active = job.status == DownloadStatus.DOWNLOADING
        if job.is_stream:
            self.progress_bar.set_progress(job.progress, active=active or job.status == DownloadStatus.PROCESSING)
        else:
            self.progress_bar.set_progress(job.progress if job.total_size else 0, active=active)

        if job.is_stream:
            seg_total = int(job.media_meta.get("seg_total") or 0)
            seg_done = int(job.media_meta.get("seg_done") or 0)
            quality = job.media_meta.get("quality") or ""
            status_label = "Merging streams…" if job.status == DownloadStatus.PROCESSING else job.status.value
            stats = (
                f"Status: {status_label}   ·   "
                f"{job.media_type.upper()} stream"
                + (f" · {quality}" if quality else "")
                + (f"   ·   Segments: {seg_done}/{seg_total}" if seg_total else "")
                + f"   ·   Downloaded: {format_bytes(job.downloaded)}"
                + f"   ·   Speed: {format_speed(job.speed_bps) if active else '—'}"
            )
            note = job.media_meta.get("ffmpeg_note")
            if note and job.status == DownloadStatus.COMPLETE:
                stats += f"   ·   ⚠ {note}"
        else:
            parts = len(job.segments) if job.segments else (job.connections if job.supports_ranges else 1)
            stats = (
                f"Status: {job.status.value}   ·   "
                f"Downloaded: {format_bytes(job.downloaded)}"
                + (f" / {format_bytes(job.total_size)}" if job.total_size else "")
                + f"   ·   Speed: {format_speed(job.speed_bps) if active else '—'}   ·   "
                f"ETA: {format_eta(job.eta_seconds) if active else '—'}   ·   "
                f"Connections: {parts}"
            )
        if job.error:
            stats += f"   ·   Error: {job.error[:80]}"
        self.detail_stats.configure(text=stats)
        self.segment_bar.set_job(job)

    def _update_status(self) -> None:
        jobs = self.manager.jobs
        active = sum(
            1
            for j in jobs
            if j.status in (DownloadStatus.DOWNLOADING, DownloadStatus.CONNECTING)
        )
        complete = sum(1 for j in jobs if j.status == DownloadStatus.COMPLETE)
        total_speed = sum(
            j.speed_bps
            for j in jobs
            if j.status == DownloadStatus.DOWNLOADING
        )
        self.speed_badge.configure(text=format_speed(total_speed) if total_speed else "0 B/s")
        self.status_var.set(
            f"Total: {len(jobs)}   ·   Active: {active}   ·   Complete: {complete}   ·   "
            f"Queue limit: {self.manager.settings.get('max_simultaneous', 3)}"
        )
        port = self.manager.settings.get("browser_port", 7373)
        if self._browser and self._browser.running:
            browser_txt = f"Browser API: 127.0.0.1:{port}"
            self.browser_badge.configure(text=f"Browser: :{port}", fg="#7dff9a")
        else:
            err = (self._browser.last_error if self._browser else "") or "disabled"
            browser_txt = f"Browser API: off ({err})"
            self.browser_badge.configure(text="Browser: off", fg="#ffb84d")
        self.status_right.configure(
            text=f"{browser_txt}   ·   Connections: {self.manager.settings.get('connections', 8)}"
        )

    def _sort_by(self, col: str) -> None:
        if self._sort_col == col:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_col = col
            self._sort_reverse = False
        col_index = COLUMNS.index(col)

        def key_fn(jid: str):
            vals = self.tree.item(jid, "values")
            return (vals[col_index] or "").lower() if vals else ""

        items = list(self.tree.get_children(""))
        items.sort(key=key_fn, reverse=self._sort_reverse)
        for i, iid in enumerate(items):
            self.tree.move(iid, "", i)

    def _context_menu(self, event: tk.Event) -> None:
        row = self.tree.identify_row(event.y)
        if row:
            if row not in self.tree.selection():
                self.tree.selection_set(row)
            self._ctx.tk_popup(event.x_root, event.y_root)

    # ── sidebar (category folders) right-click ───────────────────────────

    def _sidebar_context(self, event: tk.Event) -> None:
        idx = self.cat_list.nearest(event.y)
        if idx < 0 or idx >= len(self._sidebar_items):
            return
        label, key = self._sidebar_items[idx]
        menu = tk.Menu(self, tearoff=0)
        is_cat = bool(key) and key.startswith(FILTER_CAT_PREFIX)
        cat = key[len(FILTER_CAT_PREFIX):] if is_cat else ""
        if is_cat:
            # Select + switch to the category so the menu targets what's shown.
            self.cat_list.selection_clear(0, tk.END)
            self.cat_list.selection_set(idx)
            self._on_category_select()
            menu.add_command(label="📂  Browse folder", command=lambda c=cat: self._sidebar_browse(c))
            menu.add_separator()
        menu.add_command(label="➕  Add category…", command=self._sidebar_add_category)
        if is_cat:
            builtin = cat in self.manager.BUILTIN_CATEGORIES
            menu.add_command(
                label="🗑  Delete category",
                state="disabled" if builtin else "normal",
                command=lambda c=cat: self._sidebar_delete_category(c),
            )
        menu.tk_popup(event.x_root, event.y_root)

    def _sidebar_browse(self, cat: str) -> None:
        folder = (self.manager.settings.get("category_paths") or {}).get(cat)
        if not folder:
            folder = self.manager.settings.get("default_save_path") or str(Path.home() / "Downloads")
        p = Path(folder)
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        self._open_path(p)

    def _sidebar_add_category(self) -> None:
        name = messagebox.askstring("Add category", "Category name:", parent=self)
        if not name or not name.strip():
            return
        name = name.strip()
        folder = filedialog.askdirectory(
            title=f"Folder for “{name}” (Cancel = use a default folder)",
            initialdir=self.manager.settings.get("default_save_path") or None,
            parent=self,
            mustexist=False,
        ) or None
        created = self.manager.add_category(name, folder)
        if created:
            self._rebuild_sidebar()

    def _sidebar_delete_category(self, cat: str) -> None:
        if not messagebox.askyesno(
            "Delete category",
            f"Remove the “{cat}” category from the sidebar?\n\n"
            "Your downloaded files are NOT deleted — only the category entry.",
            parent=self,
        ):
            return
        if self.manager.remove_category(cat):
            if self._filter == FILTER_CAT_PREFIX + cat:
                self._filter = FILTER_ALL
            self._rebuild_sidebar()
            self._refresh_tree()
        else:
            messagebox.showinfo(
                "Delete category", "Built-in categories can't be removed.", parent=self
            )

    # ── file-table right-click file operations ───────────────────────────

    def _rename_selected(self) -> None:
        ids = self._selected_ids()
        if not ids:
            return
        job = self.manager.get_job(ids[0])
        if not job:
            return
        new = messagebox.askstring(
            "Rename", "New file name:", parent=self, initialvalue=job.filename
        )
        if not new or new.strip() == job.filename:
            return
        ok, err = self.manager.rename_job(job.id, new)
        if not ok:
            messagebox.showerror("Rename", err, parent=self)
        self._refresh_all()

    def _move_selected(self) -> None:
        ids = self._selected_ids()
        if not ids:
            return
        job = self.manager.get_job(ids[0])
        if not job:
            return
        dest = filedialog.askdirectory(
            title="Move to folder",
            initialdir=str(Path(job.save_path).parent),
            parent=self,
            mustexist=False,
        )
        if not dest:
            return
        ok, err = self.manager.move_job(job.id, dest)
        if not ok:
            messagebox.showerror("Move", err, parent=self)
        self._refresh_all()

    def _redownload_selected(self) -> None:
        ids = self._selected_ids()
        if not ids:
            return
        names = [j.filename for j in (self.manager.get_job(i) for i in ids) if j]
        if not names:
            return
        preview = ", ".join(names[:5]) + (" …" if len(names) > 5 else "")
        if not messagebox.askyesno(
            "Re-download",
            f"Download again from the start (discarding current progress)?\n\n{preview}",
            parent=self,
        ):
            return
        for jid in ids:
            self.manager.redownload_job(jid)
            self._open_progress(jid)
        self._refresh_all()

    # ── actions ─────────────────────────────────────────────────────────

    def _add_url(self, initial: str = "") -> None:
        def on_submit(job: DownloadJob) -> None:
            start = getattr(job, "_start_immediately", True)
            self.manager.add_job(job, start=start)
            if start:
                self._open_progress(job.id)
            self._filter = FILTER_ALL
            self.cat_list.selection_clear(0, tk.END)
            self.cat_list.selection_set(0)
            self.list_title.configure(text="📥  All downloads".strip())
            self._refresh_all()

        AddDownloadDialog(self, self.manager.settings, on_submit, initial_url=initial)

    def _paste_url(self) -> None:
        try:
            clip = self.clipboard_get().strip()
        except tk.TclError:
            clip = ""
        if clip and urlparse(clip).scheme in ("http", "https"):
            self._add_url(initial=clip)
        else:
            self._add_url()

    def _add_video(self, initial_url: str = "") -> None:
        def probe(url: str) -> dict:
            return self.manager.probe_video(url)

        def on_submit(url: str, folder: str, sel: dict, media_type: str, title: str, category: str = "") -> None:
            mt = media_type if media_type in ("page", "hls", "dash") else "page"
            job = self.manager.add_video_job(url, mt, sel, title=title, folder=folder, start=True, category=category or None)
            self._open_progress(job.id)
            self._remember_save_dir(folder)
            self._filter = FILTER_ALL
            self.cat_list.selection_clear(0, tk.END)
            self.cat_list.selection_set(0)
            self._refresh_all()

        if not initial_url:
            try:
                clip = self.clipboard_get().strip()
                if urlparse(clip).scheme in ("http", "https"):
                    initial_url = clip
            except tk.TclError:
                pass
        AddVideoDialog(self, self.manager.settings, on_submit, probe, initial_url=initial_url,
                       add_category=self.manager.add_category)

    def _show_capture_dialog(self, spec: dict) -> None:
        # One dialog per captured file (IDM-style), shown one at a time.
        self._capture_queue.append(spec)
        self._pump_capture_queue()

    def _pump_capture_queue(self) -> None:
        if self._capture_active or not self._capture_queue:
            return
        spec = self._capture_queue.pop(0)
        self._capture_active = True

        # Bring the app forward so the dialog is visible over the browser.
        try:
            self.deiconify()
            self.lift()
            self.attributes("-topmost", True)
            self.after(300, lambda: self.attributes("-topmost", False))
        except tk.TclError:
            pass

        def on_result(final: dict, start: bool, always: bool) -> None:
            if always != bool(self.manager.settings.get("confirm_browser_captures", True)):
                self.manager.settings["confirm_browser_captures"] = always
                self.manager.save_settings()
            res = self.manager.add_capture_confirmed(
                url=final["url"], filename=final["filename"], folder=final["folder"],
                category=final["category"], connections=final["connections"],
                media_type=final["media_type"], media_meta=final["media_meta"],
                cookie=final["cookie"], referrer=final["referrer"],
                extra_headers=final["extra_headers"], start=start, source="browser",
                overwrite=bool(final.get("overwrite")),
            )
            if start and res.get("id"):
                self._open_progress(res["id"])
            self._remember_save_dir(final["folder"])
            self._filter = FILTER_ALL
            self.cat_list.selection_clear(0, tk.END)
            self.cat_list.selection_set(0)
            self.list_title.configure(text="📥  All downloads".strip())
            self._refresh_all()

        def on_closed() -> None:
            self._capture_active = False
            # Show the next queued capture, if any.
            self.after(100, self._pump_capture_queue)

        CaptureDialog(
            self, self.manager.settings, spec, on_result,
            add_category=self.manager.add_category, on_closed=on_closed,
        )

    def _remember_save_dir(self, folder: str) -> None:
        folder = (folder or "").strip()
        if folder and folder != self.manager.settings.get("last_save_dir"):
            self.manager.settings["last_save_dir"] = folder
            self.manager.save_settings()

    def _choose_quality(self) -> None:
        ids = self._selected_ids()
        if not ids:
            return
        job = self.manager.get_job(ids[0])
        if not job:
            return
        # Prefer the original page URL for stream/page jobs.
        url = job.media_meta.get("page_url") or job.url

        def probe(u: str) -> dict:
            mt = job.media_type if job.media_type in ("page", "hls", "dash") else ""
            return self.manager.probe_video(u, mt, cookie=job.cookie, referrer=job.referrer)

        def on_submit(u: str, folder: str, sel: dict, media_type: str, title: str, category: str = "") -> None:
            self.manager.set_job_quality(job.id, sel, title=title)
            self._refresh_all()

        AddVideoDialog(
            self, self.manager.settings, on_submit, probe,
            initial_url=url, submit_label="Re-download",
            add_category=self.manager.add_category,
        )

    def _resume_selected(self) -> None:
        ids = self._selected_ids()
        if not ids:
            messagebox.showinfo("Resume", "Select one or more downloads first.", parent=self)
            return
        for jid in ids:
            self.manager.retry_job(jid)
            self._open_progress(jid)

    def _pause_selected(self) -> None:
        for jid in self._selected_ids():
            self.manager.pause_job(jid)

    def _cancel_selected(self) -> None:
        for jid in self._selected_ids():
            self.manager.cancel_job(jid)

    def _delete_selected(self) -> None:
        ids = self._selected_ids()
        if not ids:
            return
        if self.manager.settings.get("confirm_delete", True) and not messagebox.askyesno(
            "Delete", f"Remove {len(ids)} item(s) from the list?", parent=self
        ):
            return
        for jid in ids:
            self.manager.delete_job(jid, delete_files=False)

    def _delete_with_files(self) -> None:
        ids = self._selected_ids()
        if not ids:
            return
        if not messagebox.askyesno(
            "Delete files",
            f"Remove {len(ids)} item(s) and delete partial/completed files from disk?",
            parent=self,
        ):
            return
        for jid in ids:
            self.manager.delete_job(jid, delete_files=True)

    def _open_file(self) -> None:
        ids = self._selected_ids()
        if not ids:
            return
        job = self.manager.get_job(ids[0])
        if not job:
            return
        path = Path(job.save_path)
        if not path.exists():
            messagebox.showinfo(
                "Open file",
                "File not found yet — still downloading or was removed.",
                parent=self,
            )
            return
        self._open_path(path)

    def _open_folder(self) -> None:
        ids = self._selected_ids()
        if not ids:
            # Open default downloads folder
            folder = Path(self.manager.settings.get("default_save_path") or Path.home() / "Downloads")
            folder.mkdir(parents=True, exist_ok=True)
            self._open_path(folder)
            return
        job = self.manager.get_job(ids[0])
        if not job:
            return
        folder = Path(job.save_path).parent
        folder.mkdir(parents=True, exist_ok=True)
        self._open_path(folder)

    def _open_path(self, path: Path) -> None:
        try:
            if sys.platform == "win32":
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.run(["open", str(path)], check=False)
            else:
                subprocess.run(["xdg-open", str(path)], check=False)
        except OSError as exc:
            messagebox.showerror("Open", str(exc), parent=self)

    def _open_settings(self) -> None:
        def on_save(settings: dict) -> None:
            old_port = self.manager.settings.get("browser_port")
            old_on = self.manager.settings.get("browser_integration", True)
            self.manager.settings.update(settings)
            self.manager.save_settings()
            # Restart browser API if settings changed
            new_port = settings.get("browser_port")
            new_on = settings.get("browser_integration", True)
            if old_port != new_port or old_on != new_on or settings.get("browser_token") is not None:
                self._restart_browser_server()
            self._update_status()

        SettingsDialog(self, self.manager.settings, on_save)

    def _extension_dir(self) -> Path:
        from magic_downloader.paths import extension_dir

        return extension_dir()

    def _open_extension_help(self) -> None:
        ext = self._extension_dir()
        port = int(self.manager.settings.get("browser_port") or 7373)
        running = bool(self._browser and self._browser.running)
        msg = (
            "Why you see NO download button on websites yet\n"
            "────────────────────────────────────────────\n"
            "The website does not show Magic Downloader by itself.\n"
            "You must install the browser extension once.\n\n"
            f"App browser API: {'ON' if running else 'OFF'}  (port {port})\n\n"
            "Install steps:\n"
            "1. Keep THIS app running\n"
            "2. Open chrome://extensions  (or edge://extensions)\n"
            "3. Turn ON Developer mode\n"
            "4. Load unpacked → select folder:\n\n"
            f"   {ext}\n\n"
            "Then, on any website:\n"
            "  • A blue “⬇ Download” button appears on video players\n"
            "  • The toolbar icon shows a badge with # of videos found\n"
            "  • Click it → pick the stream/quality → sent here\n"
            "  • Right-click any link → Download with Magic Downloader\n\n"
            "Tip: install ffmpeg (on PATH) so streamed video+audio merge\n"
            "into a single clean .mp4.\n\n"
            "Open the extension folder now?"
        )
        if messagebox.askyesno("Install browser extension", msg, parent=self):
            ext.mkdir(parents=True, exist_ok=True)
            self._open_path(ext)
            # Also open the plain-English guide
            from magic_downloader.paths import install_txt_path

            guide = install_txt_path()
            if guide.exists():
                try:
                    os.startfile(guide)  # type: ignore[attr-defined]
                except OSError:
                    pass

    def _show_toast(self, text: str) -> None:
        self.toast_var.set(f"  🌐  {text}")
        try:
            self.toast_bar.pack_forget()
        except tk.TclError:
            pass
        try:
            self.toast_bar.pack(fill=tk.X, side=tk.BOTTOM, before=self._status_frame)
        except Exception:
            self.toast_bar.pack(fill=tk.X, side=tk.BOTTOM)
        if self._toast_after:
            try:
                self.after_cancel(self._toast_after)
            except Exception:
                pass
        self._toast_after = self.after(4500, self._hide_toast)

    def _hide_toast(self) -> None:
        try:
            self.toast_bar.pack_forget()
        except tk.TclError:
            pass
        self.toast_var.set("")

    def _start_browser_server(self) -> None:
        if not self.manager.settings.get("browser_integration", True):
            return
        port = int(self.manager.settings.get("browser_port") or 7373)
        token = str(self.manager.settings.get("browser_token") or "")

        def on_add(data: dict) -> dict:
            # IDM-style: pop the "Download File Info" dialog unless disabled.
            if self.manager.settings.get("confirm_browser_captures", True):
                spec = self.manager.suggest_capture(data)
                try:
                    self.after(0, lambda: self._show_capture_dialog(spec))
                except tk.TclError:
                    return self.manager.add_from_browser(data)
                return {"prompted": True, "filename": spec["filename"], "media_type": spec["media_type"]}

            result = self.manager.add_from_browser(data)
            name = result.get("filename") or "file"
            jid = result.get("id")
            try:
                self.after(0, lambda: self._show_toast(f"Captured from browser: {name}"))
                if jid and self.manager.settings.get("browser_auto_start", True):
                    self.after(0, lambda: self._open_progress(jid))
                self.after(0, self._refresh_all)
            except tk.TclError:
                pass
            return result

        def on_status() -> dict:
            snap = self.manager.status_snapshot()
            snap["port"] = port
            return snap

        def on_probe(data: dict) -> dict:
            url = str(data.get("url") or "")
            mtype = str(data.get("media_type") or "").lower()
            ua = str(self.manager.settings.get("user_agent") or "")
            cookie = str(data.get("cookie") or "")
            referrer = str(data.get("referrer") or data.get("page_url") or "")
            if mtype == "page":
                from magic_downloader.media.ytdlp_engine import probe_formats

                return probe_formats(url=url, cookie=cookie, user_agent=ua, referrer=referrer)
            from magic_downloader.media.probe import probe_media

            return probe_media(
                url=url,
                media_type=mtype or None,
                cookie=cookie,
                referrer=referrer,
                user_agent=ua,
            )

        self._browser = BrowserAPIServer(
            port=port, on_add=on_add, on_status=on_status, token=token, on_probe=on_probe
        )
        try:
            self._browser.start()
        except OSError as exc:
            self._browser.last_error = str(exc)
            # Don't crash the app if the port is busy
            try:
                self.after(
                    200,
                    lambda: messagebox.showwarning(
                        "Browser API",
                        f"Could not start browser integration on port {port}:\n{exc}\n\n"
                        "Change the port in Options, or close the other app using it.",
                        parent=self,
                    ),
                )
            except tk.TclError:
                pass

    def _restart_browser_server(self) -> None:
        if self._browser:
            self._browser.stop()
            self._browser = None
        self._start_browser_server()

    def _about(self) -> None:
        from magic_downloader import __version__

        messagebox.showinfo(
            "About Magic Downloader",
            f"Magic Downloader {__version__}\n\n"
            "A fast, multi-connection download manager:\n"
            "• Segmented multi-part downloads\n"
            "• Pause / resume with partial files\n"
            "• Queue, categories, progress map, speed limit\n"
            "• Video grabber: streaming (HLS/DASH) + many websites\n"
            "• All qualities & formats · merged to MP4 with ffmpeg\n"
            "• Browser extension: download button + video panel\n\n"
            "Install ffmpeg from Options → Video for clean merged MP4.\n\n"
            "Please only download content you have the right to.",
            parent=self,
        )

    # ── system tray (IDM-style: close hides, only Exit quits) ────────────

    def _set_window_icon(self) -> None:
        """Set the title-bar / taskbar icon (and for child dialogs)."""
        try:
            from magic_downloader.paths import RESOURCE_ROOT, extension_dir

            for ico in (
                RESOURCE_ROOT / "browser_extension" / "icons" / "app.ico",
                extension_dir() / "icons" / "app.ico",
            ):
                if ico.exists():
                    self.iconbitmap(default=str(ico))
                    return
        except Exception:  # noqa: BLE001 — cosmetic only
            pass

    def _tray_image(self):
        from PIL import Image

        from magic_downloader.paths import RESOURCE_ROOT, extension_dir

        for p in (
            extension_dir() / "icons" / "icon128.png",
            RESOURCE_ROOT / "browser_extension" / "icons" / "icon128.png",
        ):
            try:
                if p.exists():
                    return Image.open(p)
            except Exception:
                pass
        return Image.new("RGB", (64, 64), "#2b579a")

    def _setup_tray(self) -> None:
        """Create the system-tray icon. Degrades gracefully if unavailable."""
        try:
            import pystray

            menu = pystray.Menu(
                pystray.MenuItem("Show Magic Downloader", self._tray_show, default=True),
                pystray.MenuItem("Resume all", self._tray_resume_all),
                pystray.MenuItem("Pause all", self._tray_pause_all),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Exit", self._tray_exit),
            )
            self._tray = pystray.Icon(
                "magic_downloader", self._tray_image(), "Magic Downloader", menu
            )
            self._tray_thread = threading.Thread(target=self._tray.run, daemon=True)
            self._tray_thread.start()
        except Exception as exc:  # noqa: BLE001 — no tray → close will just exit
            self._tray = None

    # Single-instance control (called from the control-socket thread).
    def _request_quit(self) -> None:
        try:
            self.after(0, self._quit)
        except tk.TclError:
            pass

    def _request_show(self) -> None:
        try:
            self.after(0, self._restore_from_tray)
        except tk.TclError:
            pass

    # Tray callbacks run on the tray thread → marshal to the Tk main thread.
    def _tray_show(self, *_a) -> None:
        try:
            self.after(0, self._restore_from_tray)
        except tk.TclError:
            pass

    def _tray_exit(self, *_a) -> None:
        try:
            self.after(0, self._quit)
        except tk.TclError:
            pass

    def _tray_resume_all(self, *_a) -> None:
        self.after(0, lambda: [self.manager.retry_job(j.id) for j in list(self.manager.jobs)
                               if j.status in (DownloadStatus.PAUSED, DownloadStatus.QUEUED)])

    def _tray_pause_all(self, *_a) -> None:
        self.after(0, lambda: [self.manager.pause_job(j.id) for j in list(self.manager.jobs)])

    def _restore_from_tray(self) -> None:
        try:
            self.deiconify()
            self.state("normal")
            self.lift()
            self.focus_force()
        except tk.TclError:
            pass

    def _hide_to_tray(self) -> None:
        if self._tray is None:
            return
        try:
            self.withdraw()
        except tk.TclError:
            pass
        # (No tray balloon: it plays a Windows notification sound and pystray
        # has no silent option. The tray icon itself signals the app is alive.)

    def _on_minimize(self, event: tk.Event) -> None:
        # Hide to tray on the minimize button too, if the user opted in.
        if event.widget is not self:
            return
        if self._quitting or self._tray is None:
            return
        if not self.manager.settings.get("minimize_to_tray", False):
            return
        try:
            if self.state() == "iconic":
                self.after(10, self._hide_to_tray)
        except tk.TclError:
            pass

    def _on_close(self) -> None:
        # The window's X button: hide to tray (IDM-style) unless disabled.
        if self._tray is not None and self.manager.settings.get("close_to_tray", True):
            self._hide_to_tray()
        else:
            self._quit()

    def _quit(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        try:
            if self._single_instance is not None:
                self._single_instance.close()
        except Exception:
            pass
        try:
            if self._tray is not None:
                self._tray.stop()
        except Exception:
            pass
        if self._browser:
            self._browser.stop()
        self.manager.shutdown()
        try:
            self.destroy()
        except tk.TclError:
            pass


def run_app() -> None:
    # When packaged as an exe, copy bundled resources to a stable writable dir.
    from magic_downloader.paths import sync_bundled_resources
    from magic_downloader.single_instance import SingleInstance

    sync_bundled_resources()

    # Single instance — "last one takes place": a new launch tells any running
    # instance to quit and takes over.
    si = SingleInstance()
    si.acquire(takeover=True)

    app = MagicDownloaderApp()
    app._single_instance = si
    si.start_listener(on_quit=app._request_quit, on_show=app._request_show)
    app.mainloop()
