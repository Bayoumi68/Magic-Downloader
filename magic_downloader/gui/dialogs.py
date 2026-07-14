"""Add download and settings dialogs — polished IDM-style."""

from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from magic_downloader.gui import quiet_dialogs as messagebox
from magic_downloader.gui import quiet_dialogs as simpledialog
from typing import Callable
from urllib.parse import urlparse

from magic_downloader.config import category_for_filename, resolve_save_path
from magic_downloader.engine import suggest_filename
from magic_downloader.gui import theme as T
from magic_downloader.media import ffmpeg as ffmpeg_mod
from magic_downloader.gui.widgets import ProgressBar, SegmentBar
from magic_downloader.media.detect import MediaKind, classify_url
from magic_downloader.models import (
    DownloadJob,
    DownloadStatus,
    format_bytes,
    format_eta,
    format_speed,
)


def _center(win: tk.Toplevel) -> None:
    win.update_idletasks()
    w, h = win.winfo_width(), win.winfo_height()
    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    win.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")


def _dedupe_name(folder: Path, name: str) -> str:
    """Return a non-colliding file name in *folder* by appending (1), (2), …"""
    p = folder / name
    if not p.exists() and not Path(str(p) + ".part").exists():
        return name
    stem, suf = Path(name).stem, Path(name).suffix
    i = 1
    while True:
        cand = f"{stem} ({i}){suf}"
        cp = folder / cand
        if not cp.exists() and not Path(str(cp) + ".part").exists():
            return cand
        i += 1


def resolve_name_conflict(parent: tk.Misc, folder: Path, name: str) -> tuple[str, str]:
    """IDM-style filename collision. If *name* already exists in *folder*, ask
    the user what to do instead of silently versioning.

    Returns ``(action, final_name)`` where action is one of:
      • ``"ok"``        — no collision, use *name* as-is
      • ``"overwrite"`` — replace the existing file (name unchanged)
      • ``"rename"``    — keep both; *final_name* is a versioned name
      • ``"cancel"``    — user backed out; caller should abort
    """
    p = folder / name
    if not p.exists() and not Path(str(p) + ".part").exists():
        return ("ok", name)
    choice = messagebox.ask(
        "File already exists",
        f"A file named:\n\n    {name}\n\n"
        f"already exists in:\n{folder}\n\n"
        "•  Add version — keep both (saves as “name (1).ext”)\n"
        "•  Overwrite — replace the existing file\n",
        [("Add version", "rename"), ("Overwrite", "overwrite"), ("Cancel", "cancel")],
        parent=parent,
    )
    if choice == "overwrite":
        return ("overwrite", name)
    if choice == "rename":
        return ("rename", _dedupe_name(folder, name))
    return ("cancel", name)


def _confirm_create_folder(parent: tk.Misc, folder: str) -> bool:
    """IDM-style: if the target folder doesn't exist, offer to create it.

    Returns True if the folder exists (or was created), False to abort.
    """
    if not folder:
        messagebox.showerror("Save folder", "Choose a folder to save to.", parent=parent)
        return False
    p = Path(folder)
    if p.exists():
        return True
    if not messagebox.askyesno(
        "Create folder?",
        f"This folder does not exist:\n\n{folder}\n\nCreate it now?",
        parent=parent,
    ):
        return False
    try:
        p.mkdir(parents=True, exist_ok=True)
        return True
    except OSError as exc:
        messagebox.showerror("Create folder", f"Could not create folder:\n{exc}", parent=parent)
        return False


class AddDownloadDialog(tk.Toplevel):
    def __init__(
        self,
        master: tk.Misc,
        settings: dict,
        on_submit: Callable[[DownloadJob], None],
        initial_url: str = "",
    ) -> None:
        super().__init__(master)
        self.title("Add new download — Magic Downloader")
        self.settings = settings
        self.on_submit = on_submit
        self.resizable(True, False)
        self.transient(master)
        self.grab_set()
        self.configure(bg=T.BG)
        self.geometry("620x380")
        self.minsize(560, 360)

        header = tk.Frame(self, bg=T.BG_TOOLBAR, height=48)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        tk.Label(
            header,
            text="  ⬇  New download",
            bg=T.BG_TOOLBAR,
            fg=T.FG_ON_DARK,
            font=T.FONT_TITLE,
            anchor="w",
        ).pack(fill=tk.BOTH, expand=True, padx=12)

        frm = tk.Frame(self, bg=T.BG, padx=16, pady=14)
        frm.pack(fill=tk.BOTH, expand=True)

        def row_label(r: int, text: str) -> None:
            tk.Label(frm, text=text, bg=T.BG, fg=T.FG, font=T.FONT_UI, anchor="w").grid(
                row=r, column=0, sticky="w", pady=6, padx=(0, 10)
            )

        row_label(0, "Address (URL):")
        self.url_var = tk.StringVar(value=initial_url)
        url_entry = ttk.Entry(frm, textvariable=self.url_var, width=64, font=T.FONT_UI)
        url_entry.grid(row=0, column=1, columnspan=2, sticky="ew", pady=6)
        url_entry.focus_set()

        row_label(1, "File name:")
        self.name_var = tk.StringVar(value=suggest_filename(initial_url) if initial_url else "")
        ttk.Entry(frm, textvariable=self.name_var, width=48, font=T.FONT_UI).grid(
            row=1, column=1, columnspan=2, sticky="ew", pady=6
        )

        row_label(2, "Category:")
        cats = list(settings.get("category_paths", {}).keys()) or [
            "General",
            "Compressed",
            "Documents",
            "Music",
            "Video",
        ]
        self.cat_var = tk.StringVar(value="General")
        ttk.Combobox(
            frm, textvariable=self.cat_var, values=cats, state="readonly", width=22, font=T.FONT_UI
        ).grid(row=2, column=1, sticky="w", pady=6)

        row_label(3, "Save as:")
        self.path_var = tk.StringVar(value=settings.get("default_save_path", ""))
        ttk.Entry(frm, textvariable=self.path_var, width=48, font=T.FONT_UI).grid(
            row=3, column=1, sticky="ew", pady=6
        )
        ttk.Button(frm, text="Browse…", command=self._browse).grid(row=3, column=2, padx=(8, 0), pady=6)

        row_label(4, "Connections:")
        conn_fr = tk.Frame(frm, bg=T.BG)
        conn_fr.grid(row=4, column=1, sticky="w", pady=6)
        self.conn_var = tk.IntVar(value=int(settings.get("connections") or 8))
        ttk.Spinbox(conn_fr, from_=1, to=32, textvariable=self.conn_var, width=6).pack(side=tk.LEFT)
        tk.Label(
            conn_fr,
            text="  (multi-part acceleration)",
            bg=T.BG,
            fg=T.FG_MUTED,
            font=T.FONT_SMALL,
        ).pack(side=tk.LEFT)

        self.start_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm, text="Start downloading immediately", variable=self.start_var).grid(
            row=5, column=1, sticky="w", pady=8
        )

        btns = tk.Frame(frm, bg=T.BG)
        btns.grid(row=6, column=0, columnspan=3, sticky="e", pady=(16, 0))
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side=tk.RIGHT, padx=4)
        ttk.Button(btns, text="  Start download  ", command=self._submit).pack(side=tk.RIGHT, padx=4)

        frm.columnconfigure(1, weight=1)
        self.url_var.trace_add("write", self._on_url_change)
        self.bind("<Return>", lambda e: self._submit())
        self.bind("<Escape>", lambda e: self.destroy())
        _center(self)

    def _on_url_change(self, *_args: object) -> None:
        url = self.url_var.get().strip()
        if not self.name_var.get().strip() or self.name_var.get() in ("download", ""):
            self.name_var.set(suggest_filename(url))
        name = self.name_var.get().strip() or "download"
        cat = category_for_filename(name)
        self.cat_var.set(cat)
        folder = self.settings.get("category_paths", {}).get(cat) or self.settings.get(
            "default_save_path", ""
        )
        if folder:
            self.path_var.set(folder)

    def _browse(self) -> None:
        d = filedialog.askdirectory(initialdir=self.path_var.get() or None, parent=self, mustexist=False)
        if d:
            self.path_var.set(d)

    def _submit(self) -> None:
        url = self.url_var.get().strip()
        if not url or urlparse(url).scheme not in ("http", "https"):
            messagebox.showerror("Invalid URL", "Please enter a valid http(s) URL.", parent=self)
            return
        media_kind = classify_url(url)
        media_type = media_kind.value
        name = self.name_var.get().strip() or suggest_filename(url)
        if media_type in ("hls", "dash"):
            # A streamed manifest becomes a single .mp4 after merging.
            stem = Path(name).stem or "video"
            if stem.lower().endswith((".m3u8", ".mpd", ".m3u")):
                stem = Path(stem).stem
            name = f"{stem}.mp4"
        for ch in '<>:"/\\|?*':
            name = name.replace(ch, "_")
        folder = Path(
            self.path_var.get().strip() or str(resolve_save_path(self.settings, name).parent)
        )
        folder.mkdir(parents=True, exist_ok=True)
        # IDM-style: if the name already exists, ask (overwrite / add version)
        # instead of silently appending "(1)".
        action, name = resolve_name_conflict(self, folder, name)
        if action == "cancel":
            return  # keep the dialog open so the user can change the name
        save_path = str(folder / name)
        if action == "overwrite":
            # Drop any stale partial so the download starts fresh; the finished
            # file replaces the existing one when it completes.
            Path(save_path + ".part").unlink(missing_ok=True)

        job = DownloadJob(
            url=url,
            save_path=save_path,
            filename=name,
            connections=max(1, min(32, int(self.conn_var.get()))),
            category="Video" if media_type in ("hls", "dash") else self.cat_var.get(),
            source="manual",
            media_type=media_type,
        )
        start = self.start_var.get()
        self.destroy()
        job._start_immediately = start  # type: ignore[attr-defined]
        self.on_submit(job)


class AddVideoDialog(tk.Toplevel):
    """Fetch a video's available qualities/formats and download the chosen one.

    ``probe_fn(url)`` runs the network probe (call returns the manager's
    probe_video result) — invoked on a worker thread.
    ``on_submit(url, folder, sel, media_type, title)`` starts the download.
    """

    def __init__(
        self,
        master: tk.Misc,
        settings: dict,
        on_submit: Callable[..., None],
        probe_fn: Callable[[str], dict],
        initial_url: str = "",
        submit_label: str = "Download",
        add_category: Callable[[str, str | None], str] | None = None,
    ) -> None:
        super().__init__(master)
        self.title("Download video — choose quality")
        self.settings = settings
        self.on_submit = on_submit
        self.probe_fn = probe_fn
        self.add_category = add_category
        self._rows: dict[str, dict] = {}
        self._media_type = "page"
        self._title = ""
        self._probing = False
        self.resizable(True, True)
        self.transient(master)
        self.grab_set()
        self.configure(bg=T.BG)
        self.geometry("680x480")
        self.minsize(600, 420)

        header = tk.Frame(self, bg=T.BG_TOOLBAR, height=46)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        tk.Label(
            header, text="  🎬  Download video", bg=T.BG_TOOLBAR,
            fg=T.FG_ON_DARK, font=T.FONT_TITLE, anchor="w",
        ).pack(fill=tk.BOTH, expand=True, padx=12)

        top = tk.Frame(self, bg=T.BG, padx=14, pady=10)
        top.pack(fill=tk.X)
        tk.Label(top, text="Video / page URL:", bg=T.BG, fg=T.FG, font=T.FONT_UI).grid(row=0, column=0, sticky="w")
        self.url_var = tk.StringVar(value=initial_url)
        ent = ttk.Entry(top, textvariable=self.url_var, font=T.FONT_UI)
        ent.grid(row=0, column=1, sticky="ew", padx=8)
        self.fetch_btn = ttk.Button(top, text="Fetch formats", command=self._fetch)
        self.fetch_btn.grid(row=0, column=2)
        top.columnconfigure(1, weight=1)
        ent.focus_set()
        ent.bind("<Return>", lambda e: self._fetch())

        self.status = tk.Label(self, text="Enter a URL and click “Fetch formats”.", bg=T.BG, fg=T.FG_MUTED, font=T.FONT_SMALL, anchor="w")
        self.status.pack(fill=tk.X, padx=16)

        mid = tk.Frame(self, bg=T.BG, padx=14, pady=6)
        mid.pack(fill=tk.BOTH, expand=True)
        cols = ("quality", "format", "size", "note")
        self.tree = ttk.Treeview(mid, columns=cols, show="headings", selectmode="browse", height=9)
        for key, label, w in (("quality", "Quality", 150), ("format", "Format", 90), ("size", "Size", 100), ("note", "Note", 220)):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=w, anchor="w")
        ysb = ttk.Scrollbar(mid, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=ysb.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ysb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<Double-1>", lambda e: self._submit())

        bottom = tk.Frame(self, bg=T.BG, padx=14, pady=10)
        bottom.pack(fill=tk.X)
        bottom.columnconfigure(1, weight=1)

        tk.Label(bottom, text="Category:", bg=T.BG, fg=T.FG, font=T.FONT_UI).grid(row=0, column=0, sticky="w", pady=(0, 6))
        cat_row = tk.Frame(bottom, bg=T.BG)
        cat_row.grid(row=0, column=1, columnspan=2, sticky="ew", padx=8, pady=(0, 6))
        self.cats = list((settings.get("category_paths") or {}).keys()) or ["Video", "Music", "General"]
        self.cat_var = tk.StringVar(value="Video" if "Video" in self.cats else self.cats[0])
        self.cat_cb = ttk.Combobox(cat_row, textvariable=self.cat_var, values=self.cats, state="readonly", width=18, font=T.FONT_UI)
        self.cat_cb.pack(side=tk.LEFT)
        self.cat_cb.bind("<<ComboboxSelected>>", lambda e: self._on_category())
        if self.add_category:
            ttk.Button(cat_row, text="＋ New…", command=self._new_category).pack(side=tk.LEFT, padx=(8, 0))

        tk.Label(bottom, text="Save to:", bg=T.BG, fg=T.FG, font=T.FONT_UI).grid(row=1, column=0, sticky="w")
        self.folder_var = tk.StringVar(
            value=self.settings.get("last_save_dir")
            or (self.settings.get("category_paths", {}) or {}).get("Video")
            or self.settings.get("default_save_path", "")
        )
        ttk.Entry(bottom, textvariable=self.folder_var, font=T.FONT_UI).grid(row=1, column=1, sticky="ew", padx=8)
        ttk.Button(bottom, text="Browse…", command=self._browse).grid(row=1, column=2)

        btns = tk.Frame(self, bg=T.BG, padx=14, pady=8)
        btns.pack(fill=tk.X, pady=(0, 6))
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side=tk.RIGHT, padx=4)
        self.dl_btn = ttk.Button(btns, text=f"  {submit_label}  ", command=self._submit)
        self.dl_btn.pack(side=tk.RIGHT, padx=4)

        _center(self)
        if initial_url:
            self.after(150, self._fetch)

    def _browse(self) -> None:
        d = filedialog.askdirectory(initialdir=self.folder_var.get() or None, parent=self, mustexist=False)
        if d:
            self.folder_var.set(d)

    def _on_category(self) -> None:
        folder = (self.settings.get("category_paths") or {}).get(self.cat_var.get())
        if folder:
            self.folder_var.set(folder)

    def _new_category(self) -> None:
        if not self.add_category:
            return
        name = simpledialog.askstring("New category", "Category name:", parent=self)
        if not name or not name.strip():
            return
        name = name.strip()
        folder = filedialog.askdirectory(
            title=f"Folder for “{name}” (Cancel = use default)",
            initialdir=self.settings.get("default_save_path") or None,
            parent=self,
            mustexist=False,
        ) or None
        created = self.add_category(name, folder)
        if not created:
            return
        self.cats = list((self.settings.get("category_paths") or {}).keys())
        self.cat_cb["values"] = self.cats
        self.cat_var.set(created)
        self._on_category()

    def _fetch(self) -> None:
        url = self.url_var.get().strip()
        if not url or urlparse(url).scheme not in ("http", "https"):
            messagebox.showerror("Invalid URL", "Enter a valid http(s) URL.", parent=self)
            return
        if self._probing:
            return
        self._probing = True
        self.fetch_btn.configure(state="disabled")
        self.status.configure(text="Fetching available formats… (a few seconds)", fg=T.FG_MUTED)
        self.tree.delete(*self.tree.get_children())
        self._rows.clear()

        def worker() -> None:
            try:
                res = self.probe_fn(url)
                self.after(0, lambda: self._populate(res))
            except Exception as exc:  # noqa: BLE001
                self.after(0, lambda e=exc: self._probe_failed(str(e)))

        threading.Thread(target=worker, daemon=True).start()

    def _probe_failed(self, error: str) -> None:
        self._probing = False
        self.fetch_btn.configure(state="normal")
        self.status.configure(text=f"Couldn't read formats: {error[:120]}", fg=T.RED)

    def _populate(self, res: dict) -> None:
        self._probing = False
        self.fetch_btn.configure(state="normal")
        self._media_type = str(res.get("kind") or "page")
        self._title = str(res.get("title") or "")
        formats = res.get("formats") or []

        # Always offer a "best" auto option first.
        self._add_row("⭐ Best available", "auto", "", "Recommended", {})
        for fmt in formats:
            raw_size = _human_size(fmt.get("filesize") or 0)
            size = ("~" + raw_size) if (raw_size and fmt.get("approx")) else raw_size
            note = "needs ffmpeg (merge)" if fmt.get("needs_ffmpeg") else ("audio only" if fmt.get("audio_only") else "")
            sel = {}
            if fmt.get("audio_only"):
                sel = {"audio_only": True}
            elif fmt.get("format_id"):
                sel = {"format_id": fmt["format_id"]}
            elif fmt.get("height"):
                sel = {"height": fmt["height"]}
            label = fmt.get("label") or (f"{fmt.get('height')}p" if fmt.get("height") else "format")
            self._add_row(label, (fmt.get("ext") or "").upper(), size, note, sel)

        if not formats:
            self.status.configure(
                text=f"{self._title or 'Video'} — no per-quality list; “Best available” will still download.",
                fg=T.ORANGE,
            )
        else:
            self.status.configure(text=f"{self._title or 'Video'} — {len(formats)} formats. Pick one and click Download.", fg="#1a7a32")
        first = self.tree.get_children()
        if first:
            self.tree.selection_set(first[0])

    def _add_row(self, quality: str, fmt: str, size: str, note: str, sel: dict) -> None:
        iid = self.tree.insert("", "end", values=(quality, fmt, size, note))
        self._rows[iid] = sel

    def _submit(self) -> None:
        url = self.url_var.get().strip()
        if not url or urlparse(url).scheme not in ("http", "https"):
            messagebox.showerror("Invalid URL", "Enter a valid http(s) URL.", parent=self)
            return
        folder = self.folder_var.get().strip()
        if not _confirm_create_folder(self, folder):
            return
        sel_id = self.tree.selection()
        sel = self._rows.get(sel_id[0], {}) if sel_id else {}
        category = self.cat_var.get()
        self.destroy()
        self.on_submit(url, folder, sel, self._media_type, self._title, category)


def _human_size(n: int) -> str:
    if not n:
        return ""
    v = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if v < 1024:
            return f"{v:.0f} {unit}" if unit == "B" else f"{v:.1f} {unit}"
        v /= 1024
    return f"{v:.1f} TB"


class CaptureDialog(tk.Toplevel):
    """IDM-style "Download File Info" dialog for browser-captured downloads.

    Prefilled from ``spec`` (manager.suggest_capture). Lets the user set the
    file name, category and save folder, then Start / queue (Later) / Cancel.
    ``on_result(final_spec, start, always_ask)`` is called on Start/Later.
    """

    def __init__(
        self,
        master: tk.Misc,
        settings: dict,
        spec: dict,
        on_result: Callable[[dict, bool, bool], None],
        add_category: Callable[[str, str | None], str] | None = None,
        on_closed: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(master)
        self.title("Download — Magic Downloader")
        self.settings = settings
        self.spec = dict(spec)
        self.on_result = on_result
        self.add_category = add_category
        self.on_closed = on_closed
        self._done = False
        self.transient(master)
        self.grab_set()
        self.configure(bg=T.BG)
        self.geometry("560x360")
        self.minsize(520, 340)

        header = tk.Frame(self, bg=T.BG_TOOLBAR, height=46)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        is_stream = spec.get("is_stream")
        htext = "  🎬  Download video" if is_stream else "  ⬇  Download file"
        tk.Label(header, text=htext, bg=T.BG_TOOLBAR, fg=T.FG_ON_DARK, font=T.FONT_TITLE, anchor="w").pack(
            fill=tk.BOTH, expand=True, padx=12
        )

        frm = tk.Frame(self, bg=T.BG, padx=16, pady=14)
        frm.pack(fill=tk.BOTH, expand=True)
        frm.columnconfigure(1, weight=1)

        def row(r: int, text: str) -> None:
            tk.Label(frm, text=text, bg=T.BG, fg=T.FG, font=T.FONT_UI, anchor="w").grid(
                row=r, column=0, sticky="w", pady=7, padx=(0, 10)
            )

        row(0, "File name:")
        self.name_var = tk.StringVar(value=spec.get("filename", ""))
        ttk.Entry(frm, textvariable=self.name_var, font=T.FONT_UI).grid(row=0, column=1, columnspan=2, sticky="ew", pady=7)

        row(1, "Category:")
        cat_row = tk.Frame(frm, bg=T.BG)
        cat_row.grid(row=1, column=1, columnspan=2, sticky="ew", pady=7)
        self.cats = list((settings.get("category_paths") or {}).keys()) or ["General", "Video", "Music", "Documents", "Compressed"]
        self.cat_var = tk.StringVar(value=spec.get("category") or "General")
        self.cat_cb = ttk.Combobox(cat_row, textvariable=self.cat_var, values=self.cats, state="readonly", width=20, font=T.FONT_UI)
        self.cat_cb.pack(side=tk.LEFT)
        self.cat_cb.bind("<<ComboboxSelected>>", lambda e: self._on_category())
        if self.add_category:
            ttk.Button(cat_row, text="＋ New category…", command=self._new_category).pack(side=tk.LEFT, padx=(8, 0))

        row(2, "Save to:")
        # Default to the folder the user last downloaded to (remembered), else
        # the category folder from the spec.
        self.folder_var = tk.StringVar(value=settings.get("last_save_dir") or spec.get("folder", ""))
        ttk.Entry(frm, textvariable=self.folder_var, font=T.FONT_UI).grid(row=2, column=1, sticky="ew", pady=7)
        ttk.Button(frm, text="Browse…", command=self._browse).grid(row=2, column=2, padx=(8, 0), pady=7)

        row(3, "Connections:")
        self.conn_var = tk.IntVar(value=int(spec.get("connections") or 8))
        cframe = tk.Frame(frm, bg=T.BG)
        cframe.grid(row=3, column=1, sticky="w", pady=7)
        ttk.Spinbox(cframe, from_=1, to=32, textvariable=self.conn_var, width=6).pack(side=tk.LEFT)
        info = spec.get("media_type", "http")
        bits = []
        if is_stream:
            q = spec.get("media_meta", {}).get("quality") or (f"{spec['media_meta']['height']}p" if spec.get("media_meta", {}).get("height") else "best")
            bits.append(f"{info.upper()} video · {q}")
        elif spec.get("size"):
            bits.append(_human_size(spec["size"]))
        if bits:
            tk.Label(cframe, text="   " + "  ·  ".join(bits), bg=T.BG, fg=T.FG_MUTED, font=T.FONT_SMALL).pack(side=tk.LEFT)

        tk.Label(frm, text="URL:", bg=T.BG, fg=T.FG_MUTED, font=T.FONT_SMALL, anchor="w").grid(row=4, column=0, sticky="w", pady=(10, 0))
        tk.Label(frm, text=(spec.get("url") or "")[:80] + ("…" if len(spec.get("url") or "") > 80 else ""),
                 bg=T.BG, fg=T.BLUE, font=T.FONT_SMALL, anchor="w").grid(row=4, column=1, columnspan=2, sticky="w", pady=(10, 0))

        self.always_var = tk.BooleanVar(value=bool(settings.get("confirm_browser_captures", True)))
        ttk.Checkbutton(frm, text="Always show this dialog for browser downloads", variable=self.always_var).grid(
            row=5, column=0, columnspan=3, sticky="w", pady=(14, 0)
        )

        btns = tk.Frame(self, bg=T.BG, padx=14, pady=10)
        btns.pack(fill=tk.X)
        ttk.Button(btns, text="Cancel", command=self._cancel).pack(side=tk.RIGHT, padx=4)
        ttk.Button(btns, text="Download Later", command=lambda: self._finish(False)).pack(side=tk.RIGHT, padx=4)
        ttk.Button(btns, text="  Start Download  ", command=lambda: self._finish(True)).pack(side=tk.RIGHT, padx=4)

        self.bind("<Return>", lambda e: self._finish(True))
        self.bind("<Escape>", lambda e: self._cancel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        _center(self)

    def _on_category(self) -> None:
        cat = self.cat_var.get()
        folder = (self.settings.get("category_paths") or {}).get(cat)
        if folder:
            self.folder_var.set(folder)

    def _new_category(self) -> None:
        if not self.add_category:
            return
        name = simpledialog.askstring("New category", "Category name:", parent=self)
        if not name or not name.strip():
            return
        name = name.strip()
        folder = filedialog.askdirectory(
            title=f"Folder for “{name}” (Cancel = use default)",
            initialdir=self.settings.get("default_save_path") or None,
            parent=self,
            mustexist=False,
        ) or None
        created = self.add_category(name, folder)
        if not created:
            return
        self.cats = list((self.settings.get("category_paths") or {}).keys())
        self.cat_cb["values"] = self.cats
        self.cat_var.set(created)
        self._on_category()

    def _browse(self) -> None:
        d = filedialog.askdirectory(initialdir=self.folder_var.get() or None, parent=self, mustexist=False)
        if d:
            self.folder_var.set(d)

    def _final(self) -> dict:
        s = dict(self.spec)
        s["filename"] = self.name_var.get().strip() or s.get("filename") or "download"
        s["category"] = self.cat_var.get()
        s["folder"] = self.folder_var.get().strip() or s.get("folder")
        s["connections"] = max(1, min(32, int(self.conn_var.get())))
        return s

    def _finish(self, start: bool) -> None:
        if self._done:
            return
        result = self._final()
        if not _confirm_create_folder(self, result["folder"]):
            return
        # IDM-style filename collision prompt (overwrite / add version / cancel).
        action, newname = resolve_name_conflict(
            self, Path(result["folder"]), result["filename"]
        )
        if action == "cancel":
            return  # keep the dialog open
        result["filename"] = newname
        result["overwrite"] = action == "overwrite"
        self._done = True
        always = bool(self.always_var.get())
        self.destroy()
        self.on_result(result, start, always)
        if self.on_closed:
            self.on_closed()

    def _cancel(self) -> None:
        if self._done:
            return
        self._done = True
        self.destroy()
        if self.on_closed:
            self.on_closed()


QUALITY_CHOICES = [
    ("Ask each time", "ask"),
    ("Best available", "best"),
    ("2160p (4K)", "2160"),
    ("1440p", "1440"),
    ("1080p", "1080"),
    ("720p", "720"),
    ("480p", "480"),
    ("360p", "360"),
    ("Audio only", "audio"),
]


class SettingsDialog(tk.Toplevel):
    """Tabbed, IDM-style Options dialog."""

    def __init__(self, master: tk.Misc, settings: dict, on_save: Callable[[dict], None]) -> None:
        super().__init__(master)
        self.title("Options — Magic Downloader")
        self.settings = dict(settings)
        self.on_save = on_save
        self.transient(master)
        self.grab_set()
        self.configure(bg=T.BG)
        self.geometry("660x560")
        self.minsize(620, 520)

        # Working copies of the editable category maps.
        self._cat_paths = dict(self.settings.get("category_paths") or {})
        self._cat_exts = {k: list(v) for k, v in (self.settings.get("category_extensions") or {}).items()}
        self._install_thread: threading.Thread | None = None

        header = tk.Frame(self, bg=T.BG_TOOLBAR, height=44)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        tk.Label(
            header, text="  ⚙  Options", bg=T.BG_TOOLBAR, fg=T.FG_ON_DARK,
            font=T.FONT_TITLE, anchor="w",
        ).pack(fill=tk.BOTH, expand=True, padx=12)

        nb = ttk.Notebook(self)
        nb.pack(fill=tk.BOTH, expand=True, padx=10, pady=(10, 0))
        self._build_general(nb)
        self._build_connections(nb)
        self._build_filetypes(nb)
        self._build_video(nb)
        self._build_browser(nb)

        btns = tk.Frame(self, bg=T.BG)
        btns.pack(fill=tk.X, padx=12, pady=10)
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side=tk.RIGHT, padx=4)
        ttk.Button(btns, text="Save", command=self._save).pack(side=tk.RIGHT, padx=4)
        _center(self)

    # ── helpers ─────────────────────────────────────────────────────────
    def _tab(self, nb: ttk.Notebook, title: str) -> tk.Frame:
        f = tk.Frame(nb, bg=T.BG, padx=16, pady=14)
        nb.add(f, text=f"  {title}  ")
        f.columnconfigure(1, weight=1)
        return f

    def _label(self, parent: tk.Frame, row: int, text: str) -> None:
        tk.Label(parent, text=text, bg=T.BG, fg=T.FG, font=T.FONT_UI, anchor="w").grid(
            row=row, column=0, sticky="w", pady=6, padx=(0, 10)
        )

    def _hint(self, parent: tk.Frame, row: int, text: str, col: int = 1, span: int = 2) -> None:
        tk.Label(
            parent, text=text, bg=T.BG, fg=T.FG_MUTED, font=T.FONT_SMALL,
            anchor="w", justify=tk.LEFT, wraplength=420,
        ).grid(row=row, column=col, columnspan=span, sticky="w", pady=(0, 6))

    # ── Tab: General ────────────────────────────────────────────────────
    def _build_general(self, nb: ttk.Notebook) -> None:
        f = self._tab(nb, "General")
        self._label(f, 0, "Default save folder:")
        self.path_var = tk.StringVar(value=self.settings.get("default_save_path", ""))
        ttk.Entry(f, textvariable=self.path_var).grid(row=0, column=1, sticky="ew", pady=6)
        ttk.Button(f, text="Browse…", command=lambda: self._browse_into(self.path_var)).grid(
            row=0, column=2, padx=(8, 0), pady=6
        )

        self.confirm_delete = tk.BooleanVar(value=bool(self.settings.get("confirm_delete", True)))
        ttk.Checkbutton(
            f, text="Ask for confirmation before deleting downloads", variable=self.confirm_delete
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=8)

        self.browser_auto = tk.BooleanVar(value=bool(self.settings.get("browser_auto_start", True)))
        ttk.Checkbutton(
            f, text="Start downloads immediately when added from the browser",
            variable=self.browser_auto,
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=4)

        self.show_progress = tk.BooleanVar(value=bool(self.settings.get("show_progress_dialog", True)))
        ttk.Checkbutton(
            f, text="Show a progress window for each download",
            variable=self.show_progress,
        ).grid(row=6, column=0, columnspan=3, sticky="w", pady=4)

        self.close_to_tray = tk.BooleanVar(value=bool(self.settings.get("close_to_tray", True)))
        ttk.Checkbutton(
            f, text="Keep running in the system tray when I close the window (only Exit quits)",
            variable=self.close_to_tray,
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=4)

        self.minimize_to_tray = tk.BooleanVar(value=bool(self.settings.get("minimize_to_tray", False)))
        ttk.Checkbutton(
            f, text="Also hide to tray when I minimize", variable=self.minimize_to_tray
        ).grid(row=4, column=0, columnspan=3, sticky="w", pady=4)

        from magic_downloader import startup as _startup
        self.startup_var = tk.BooleanVar(value=_startup.is_enabled())
        startup_cb = ttk.Checkbutton(
            f, text="Start Magic Downloader when Windows starts", variable=self.startup_var
        )
        startup_cb.grid(row=5, column=0, columnspan=3, sticky="w", pady=4)
        if not _startup.is_supported():
            startup_cb.configure(state="disabled")

    # ── Tab: Connections / speed ────────────────────────────────────────
    def _build_connections(self, nb: ttk.Notebook) -> None:
        f = self._tab(nb, "Connections")
        self.conn_var = tk.IntVar(value=int(self.settings.get("connections") or 8))
        self.max_var = tk.IntVar(value=int(self.settings.get("max_simultaneous") or 3))
        self.workers_var = tk.IntVar(value=int(self.settings.get("media_workers") or 8))
        self.speed_var = tk.IntVar(value=int(self.settings.get("max_speed_kbps") or 0))
        self.timeout_var = tk.IntVar(value=int(self.settings.get("timeout") or 60))
        self.retries_var = tk.IntVar(value=int(self.settings.get("retries") or 3))
        self.chunk_var = tk.IntVar(value=int(int(self.settings.get("chunk_size") or 262144) // 1024))

        self._label(f, 0, "Connections per download:")
        ttk.Spinbox(f, from_=1, to=32, textvariable=self.conn_var, width=8).grid(row=0, column=1, sticky="w", pady=6)
        self._label(f, 1, "Max simultaneous downloads:")
        ttk.Spinbox(f, from_=1, to=10, textvariable=self.max_var, width=8).grid(row=1, column=1, sticky="w", pady=6)
        self._label(f, 2, "Stream segment workers:")
        ttk.Spinbox(f, from_=1, to=32, textvariable=self.workers_var, width=8).grid(row=2, column=1, sticky="w", pady=6)

        self._label(f, 3, "Speed limit (KB/s):")
        ttk.Spinbox(f, from_=0, to=1000000, increment=64, textvariable=self.speed_var, width=10).grid(
            row=3, column=1, sticky="w", pady=6
        )
        self._hint(f, 4, "0 = unlimited. Applies to the whole app (all active downloads).")

        self._label(f, 5, "Request timeout (seconds):")
        ttk.Spinbox(f, from_=5, to=600, textvariable=self.timeout_var, width=8).grid(row=5, column=1, sticky="w", pady=6)
        self._label(f, 6, "Retries on error:")
        ttk.Spinbox(f, from_=0, to=15, textvariable=self.retries_var, width=8).grid(row=6, column=1, sticky="w", pady=6)
        self._label(f, 7, "Chunk size (KB):")
        ttk.Spinbox(f, from_=16, to=8192, increment=16, textvariable=self.chunk_var, width=8).grid(
            row=7, column=1, sticky="w", pady=6
        )

        self._label(f, 8, "User-Agent:")
        self.ua_var = tk.StringVar(value=str(self.settings.get("user_agent") or ""))
        ttk.Entry(f, textvariable=self.ua_var).grid(row=8, column=1, columnspan=2, sticky="ew", pady=6)

    # ── Tab: File Types (categories) ────────────────────────────────────
    def _build_filetypes(self, nb: ttk.Notebook) -> None:
        f = self._tab(nb, "File Types")
        tk.Label(
            f, text="Downloads are filed into these categories by extension.",
            bg=T.BG, fg=T.FG_MUTED, font=T.FONT_SMALL, anchor="w",
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))

        left = tk.Frame(f, bg=T.BG)
        left.grid(row=1, column=0, sticky="ns")
        self.cat_listbox = tk.Listbox(
            left, height=10, width=18, exportselection=False,
            bg=T.BG_LIST, fg=T.FG, font=T.FONT_UI, highlightthickness=1, highlightbackground=T.BORDER,
            selectbackground=T.SELECT, selectforeground=T.SELECT_FG, activestyle="none",
        )
        self.cat_listbox.pack(fill=tk.Y, expand=True)
        self.cat_listbox.bind("<<ListboxSelect>>", lambda e: self._on_cat_select())
        catbtns = tk.Frame(f, bg=T.BG)
        catbtns.grid(row=2, column=0, sticky="w", pady=6)
        ttk.Button(catbtns, text="Add", width=7, command=self._add_category).pack(side=tk.LEFT)
        ttk.Button(catbtns, text="Remove", width=8, command=self._remove_category).pack(side=tk.LEFT, padx=4)

        right = tk.Frame(f, bg=T.BG)
        right.grid(row=1, column=1, columnspan=2, sticky="nsew", padx=(14, 0))
        right.columnconfigure(0, weight=1)
        f.rowconfigure(1, weight=1)
        f.columnconfigure(1, weight=1)

        tk.Label(right, text="Extensions (space or comma separated):", bg=T.BG, fg=T.FG, font=T.FONT_UI, anchor="w").pack(fill=tk.X)
        self.ext_var = tk.StringVar()
        self.ext_entry = tk.Text(right, height=5, width=40, font=T.FONT_UI, wrap="word")
        self.ext_entry.pack(fill=tk.BOTH, expand=True, pady=(2, 8))

        tk.Label(right, text="Save to folder:", bg=T.BG, fg=T.FG, font=T.FONT_UI, anchor="w").pack(fill=tk.X)
        folder_row = tk.Frame(right, bg=T.BG)
        folder_row.pack(fill=tk.X, pady=(2, 8))
        self.cat_folder_var = tk.StringVar()
        ttk.Entry(folder_row, textvariable=self.cat_folder_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(folder_row, text="Browse…", command=lambda: self._browse_into(self.cat_folder_var)).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(right, text="Apply to selected category", command=self._apply_category).pack(anchor="e")

        self._reload_categories(select_first=True)

    def _all_categories(self) -> list[str]:
        names = ["General"] + [c for c in self._cat_paths if c != "General"]
        for c in self._cat_exts:
            if c not in names:
                names.append(c)
        return names

    def _reload_categories(self, select_first: bool = False) -> None:
        self.cat_listbox.delete(0, tk.END)
        for name in self._all_categories():
            self.cat_listbox.insert(tk.END, name)
        if select_first and self.cat_listbox.size():
            self.cat_listbox.selection_set(0)
            self._on_cat_select()

    def _selected_category(self) -> str | None:
        sel = self.cat_listbox.curselection()
        if not sel:
            return None
        return self.cat_listbox.get(sel[0])

    def _on_cat_select(self) -> None:
        cat = self._selected_category()
        if not cat:
            return
        exts = self._cat_exts.get(cat, [])
        self.ext_entry.delete("1.0", tk.END)
        self.ext_entry.insert("1.0", " ".join(exts))
        self.cat_folder_var.set(self._cat_paths.get(cat, ""))

    def _apply_category(self) -> None:
        cat = self._selected_category()
        if not cat:
            return
        raw = self.ext_entry.get("1.0", tk.END).replace(",", " ").split()
        exts = []
        for tok in raw:
            tok = tok.strip().lower()
            if not tok:
                continue
            if not tok.startswith("."):
                tok = "." + tok
            exts.append(tok)
        if cat != "General":
            self._cat_exts[cat] = exts
        folder = self.cat_folder_var.get().strip()
        if folder:
            self._cat_paths[cat] = folder
        messagebox.showinfo("File Types", f"Updated “{cat}”.", parent=self)

    def _add_category(self) -> None:
        name = simpledialog.askstring("Add category", "Category name:", parent=self)
        if not name:
            return
        name = name.strip()
        if not name or name in self._all_categories():
            return
        base = Path(self.settings.get("default_save_path") or ".")
        self._cat_paths[name] = str(base / name)
        self._cat_exts[name] = []
        self._reload_categories()
        idx = self._all_categories().index(name)
        self.cat_listbox.selection_clear(0, tk.END)
        self.cat_listbox.selection_set(idx)
        self._on_cat_select()

    def _remove_category(self) -> None:
        cat = self._selected_category()
        if not cat or cat in ("General", "Video", "Music", "Documents", "Compressed"):
            messagebox.showinfo("File Types", "Built-in categories can't be removed.", parent=self)
            return
        self._cat_paths.pop(cat, None)
        self._cat_exts.pop(cat, None)
        self._reload_categories(select_first=True)

    # ── Tab: Video & ffmpeg ─────────────────────────────────────────────
    def _build_video(self, nb: ttk.Notebook) -> None:
        f = self._tab(nb, "Video / ffmpeg")

        self._label(f, 0, "Default video quality:")
        self.quality_var = tk.StringVar()
        cur_q = str(self.settings.get("default_video_quality") or "best")
        combo = ttk.Combobox(f, textvariable=self.quality_var, state="readonly", width=20,
                             values=[label for label, _ in QUALITY_CHOICES])
        combo.grid(row=0, column=1, sticky="w", pady=6)
        self.quality_var.set(next((lbl for lbl, val in QUALITY_CHOICES if val == cur_q), "Best available"))
        self._hint(f, 1, "Used for one-click video downloads. Higher than 720p usually needs ffmpeg.")

        sep = tk.Frame(f, bg=T.BORDER, height=1)
        sep.grid(row=2, column=0, columnspan=3, sticky="ew", pady=12)
        tk.Label(f, text="ffmpeg (merges video + audio into MP4)", bg=T.BG, fg=T.ACCENT, font=T.FONT_UI_BOLD, anchor="w").grid(
            row=3, column=0, columnspan=3, sticky="w"
        )

        self.ffmpeg_status = tk.Label(f, text="", bg=T.BG, fg=T.FG_MUTED, font=T.FONT_SMALL, anchor="w", justify=tk.LEFT, wraplength=440)
        self.ffmpeg_status.grid(row=4, column=0, columnspan=3, sticky="w", pady=6)

        self._label(f, 5, "ffmpeg path:")
        self.ffmpeg_var = tk.StringVar(value=str(self.settings.get("ffmpeg_path") or ""))
        ttk.Entry(f, textvariable=self.ffmpeg_var).grid(row=5, column=1, sticky="ew", pady=6)
        ttk.Button(f, text="Browse…", command=self._browse_ffmpeg).grid(row=5, column=2, padx=(8, 0), pady=6)

        self.install_btn = ttk.Button(f, text="⬇ Install ffmpeg automatically", command=self._install_ffmpeg)
        self.install_btn.grid(row=6, column=0, columnspan=2, sticky="w", pady=8)
        self.install_status = tk.Label(f, text="", bg=T.BG, fg=T.FG_MUTED, font=T.FONT_SMALL, anchor="w")
        self.install_status.grid(row=7, column=0, columnspan=3, sticky="w")
        self._refresh_ffmpeg_status()

    def _refresh_ffmpeg_status(self) -> None:
        ffmpeg_mod.reset_cache()
        hint = self.ffmpeg_var.get().strip() or None
        found = ffmpeg_mod.find_ffmpeg(extra_hint=hint)
        if found:
            self.ffmpeg_status.configure(text=f"✅ Found: {found}", fg="#1a7a32")
        else:
            self.ffmpeg_status.configure(
                text="⚠ Not found. Streaming video will save as .ts or "
                "separate files until ffmpeg is installed.",
                fg=T.RED,
            )

    def _browse_ffmpeg(self) -> None:
        path = filedialog.askopenfilename(
            title="Select ffmpeg executable",
            filetypes=[("ffmpeg", "ffmpeg.exe ffmpeg"), ("All files", "*.*")],
        )
        if path:
            self.ffmpeg_var.set(path)
            self._refresh_ffmpeg_status()

    def _install_ffmpeg(self) -> None:
        if self._install_thread and self._install_thread.is_alive():
            return
        self.install_btn.configure(state="disabled")
        self.install_status.configure(text="Starting download…", fg=T.FG_MUTED)

        def worker() -> None:
            from magic_downloader.media import ffmpeg_installer

            def prog(done: int, total: int, phase: str) -> None:
                if total:
                    pct = int(done * 100 / total)
                    msg = f"{phase} {pct}%  ({done // (1024*1024)} MB)"
                else:
                    msg = phase
                self.after(0, lambda: self.install_status.configure(text=msg))

            try:
                path = ffmpeg_installer.install_ffmpeg(progress=prog)
                self.after(0, lambda: self._install_done(path, None))
            except Exception as exc:  # noqa: BLE001
                self.after(0, lambda e=exc: self._install_done(None, str(e)))

        self._install_thread = threading.Thread(target=worker, daemon=True)
        self._install_thread.start()

    def _install_done(self, path: str | None, error: str | None) -> None:
        self.install_btn.configure(state="normal")
        if path:
            self.install_status.configure(text=f"✅ Installed: {path}", fg="#1a7a32")
            self._refresh_ffmpeg_status()
        else:
            self.install_status.configure(text=f"Failed: {error}", fg=T.RED)
            messagebox.showerror(
                "Install ffmpeg",
                f"Could not download ffmpeg automatically:\n{error}\n\n"
                "You can install it manually and set its path above, or add it to PATH.",
                parent=self,
            )

    # ── Tab: Browser ────────────────────────────────────────────────────
    def _build_browser(self, nb: ttk.Notebook) -> None:
        f = self._tab(nb, "Browser")
        self.browser_on = tk.BooleanVar(value=bool(self.settings.get("browser_integration", True)))
        ttk.Checkbutton(
            f, text="Enable local browser API (required for the extension)", variable=self.browser_on
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=6)

        self.confirm_captures = tk.BooleanVar(value=bool(self.settings.get("confirm_browser_captures", True)))
        ttk.Checkbutton(
            f, text="Show the download dialog (name/category/folder) for browser downloads",
            variable=self.confirm_captures,
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=4)

        self._label(f, 2, "API port:")
        self.port_var = tk.IntVar(value=int(self.settings.get("browser_port") or 7373))
        ttk.Spinbox(f, from_=1024, to=65535, textvariable=self.port_var, width=10).grid(row=2, column=1, sticky="w", pady=6)

        self._label(f, 3, "API token (optional):")
        self.token_var = tk.StringVar(value=str(self.settings.get("browser_token") or ""))
        ttk.Entry(f, textvariable=self.token_var).grid(row=3, column=1, columnspan=2, sticky="ew", pady=6)

        self._hint(
            f, 4,
            "Load the unpacked extension from the browser_extension/ folder in "
            "Chrome/Edge/Firefox, then match this port in the extension popup.",
            col=0, span=3,
        )

    # ── save ────────────────────────────────────────────────────────────
    def _browse_into(self, var: tk.StringVar) -> None:
        d = filedialog.askdirectory(initialdir=var.get() or None, parent=self, mustexist=False)
        if d:
            var.set(d)

    def _save(self) -> None:
        self.settings["default_save_path"] = self.path_var.get().strip()
        self.settings["confirm_delete"] = bool(self.confirm_delete.get())
        self.settings["browser_auto_start"] = bool(self.browser_auto.get())
        self.settings["close_to_tray"] = bool(self.close_to_tray.get())
        self.settings["minimize_to_tray"] = bool(self.minimize_to_tray.get())
        self.settings["show_progress_dialog"] = bool(self.show_progress.get())
        try:
            from magic_downloader import startup as _startup

            _startup.set_enabled(bool(self.startup_var.get()))
        except Exception:
            pass

        self.settings["connections"] = max(1, min(32, int(self.conn_var.get())))
        self.settings["max_simultaneous"] = max(1, min(10, int(self.max_var.get())))
        self.settings["media_workers"] = max(1, min(32, int(self.workers_var.get())))
        self.settings["max_speed_kbps"] = max(0, int(self.speed_var.get()))
        self.settings["timeout"] = max(5, min(600, int(self.timeout_var.get())))
        self.settings["retries"] = max(0, min(15, int(self.retries_var.get())))
        self.settings["chunk_size"] = max(16, min(8192, int(self.chunk_var.get()))) * 1024
        if self.ua_var.get().strip():
            self.settings["user_agent"] = self.ua_var.get().strip()

        # File types (fold in any unsaved edits to the selected category first).
        self._apply_current_category_silent()
        self.settings["category_paths"] = dict(self._cat_paths)
        self.settings["category_extensions"] = {k: list(v) for k, v in self._cat_exts.items()}

        self.settings["default_video_quality"] = next(
            (val for lbl, val in QUALITY_CHOICES if lbl == self.quality_var.get()), "best"
        )
        self.settings["ffmpeg_path"] = self.ffmpeg_var.get().strip()

        self.settings["browser_integration"] = bool(self.browser_on.get())
        self.settings["confirm_browser_captures"] = bool(self.confirm_captures.get())
        self.settings["browser_port"] = max(1024, min(65535, int(self.port_var.get())))
        self.settings["browser_token"] = self.token_var.get().strip()

        self.on_save(self.settings)
        self.destroy()

    def _apply_current_category_silent(self) -> None:
        cat = self._selected_category()
        if not cat:
            return
        raw = self.ext_entry.get("1.0", tk.END).replace(",", " ").split()
        exts = []
        for tok in raw:
            tok = tok.strip().lower()
            if tok:
                exts.append(tok if tok.startswith(".") else "." + tok)
        if cat != "General":
            self._cat_exts[cat] = exts
        folder = self.cat_folder_var.get().strip()
        if folder:
            self._cat_paths[cat] = folder


class DownloadProgressDialog(tk.Toplevel):
    """IDM-style per-download progress window (modeless — several can be open).

    Reads live state from the manager; the app drives ``update_view`` each tick.
    ``open_path(Path)`` opens a file/folder.
    """

    def __init__(self, master: tk.Misc, manager, job_id: str, open_path: Callable[[Path], None]) -> None:
        super().__init__(master)
        self.manager = manager
        self.job_id = job_id
        self.open_path = open_path
        self._closed = False
        self.title("Downloading — Magic Downloader")
        self.configure(bg=T.BG)
        self.geometry("580x350")
        self.minsize(540, 330)
        self.transient(master)

        job = self.manager.get_job(job_id)
        header = tk.Frame(self, bg=T.BG_TOOLBAR, height=44)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        self.title_lbl = tk.Label(
            header, text=f"  ⬇  {job.filename if job else 'download'}", bg=T.BG_TOOLBAR,
            fg=T.FG_ON_DARK, font=T.FONT_TITLE, anchor="w",
        )
        self.title_lbl.pack(fill=tk.BOTH, expand=True, padx=12)

        body = tk.Frame(self, bg=T.BG, padx=16, pady=12)
        body.pack(fill=tk.BOTH, expand=True)
        self.url_lbl = tk.Label(body, text="", bg=T.BG, fg=T.BLUE, font=T.FONT_SMALL, anchor="w")
        self.url_lbl.pack(fill=tk.X)
        self.path_lbl = tk.Label(body, text="", bg=T.BG, fg=T.FG_MUTED, font=T.FONT_SMALL, anchor="w")
        self.path_lbl.pack(fill=tk.X, pady=(0, 8))

        self.bar = ProgressBar(body, height=22)
        self.bar.pack(fill=tk.X)

        stats = tk.Frame(body, bg=T.BG)
        stats.pack(fill=tk.X, pady=8)
        self._stat: dict[str, tk.Label] = {}
        for col, (key, title) in enumerate(
            [("status", "Status"), ("size", "Downloaded"), ("speed", "Transfer rate"),
             ("eta", "Time left"), ("parts", "Connections")]
        ):
            f = tk.Frame(stats, bg=T.BG)
            f.grid(row=0, column=col, sticky="w", padx=(0, 16))
            tk.Label(f, text=title, bg=T.BG, fg=T.FG_MUTED, font=("Segoe UI", 8)).pack(anchor="w")
            v = tk.Label(f, text="—", bg=T.BG, fg=T.FG, font=T.FONT_UI_BOLD)
            v.pack(anchor="w")
            self._stat[key] = v

        tk.Label(body, text="Download progress (connections):", bg=T.BG, fg=T.FG_MUTED,
                 font=T.FONT_SMALL, anchor="w").pack(fill=tk.X)
        self.segbar = SegmentBar(body, height=34)
        self.segbar.pack(fill=tk.X, pady=(2, 8))

        self.close_when_done = tk.BooleanVar(
            value=bool(self.manager.settings.get("progress_close_on_complete", False))
        )
        ttk.Checkbutton(
            body, text="Close this window when the download completes",
            variable=self.close_when_done, command=self._remember_close_pref,
        ).pack(anchor="w")

        btns = tk.Frame(self, bg=T.BG, padx=14, pady=10)
        btns.pack(fill=tk.X)
        self.pause_btn = ttk.Button(btns, text="Pause", command=self._toggle)
        self.pause_btn.pack(side=tk.LEFT)
        self.cancel_btn = ttk.Button(btns, text="Cancel", command=self._cancel)
        self.cancel_btn.pack(side=tk.LEFT, padx=6)
        self.open_btn = ttk.Button(btns, text="Open", command=self._open_file)  # shown when complete
        ttk.Button(btns, text="Hide", command=self._hide).pack(side=tk.RIGHT, padx=4)
        self.folder_btn = ttk.Button(btns, text="Open folder", command=self._open_folder)
        self.folder_btn.pack(side=tk.RIGHT, padx=4)

        self.protocol("WM_DELETE_WINDOW", self._hide)
        _center(self)
        self.update_view()

    def _remember_close_pref(self) -> None:
        self.manager.settings["progress_close_on_complete"] = bool(self.close_when_done.get())
        try:
            self.manager.save_settings()
        except Exception:
            pass

    def update_view(self) -> None:
        if self._closed:
            return
        job = self.manager.get_job(self.job_id)
        if job is None:
            self._closed = True
            self.destroy()
            return

        active = job.status == DownloadStatus.DOWNLOADING
        processing = job.status == DownloadStatus.PROCESSING
        icon = "✅" if job.status == DownloadStatus.COMPLETE else ("⚠" if job.status == DownloadStatus.FAILED else "⬇")
        self.title_lbl.configure(text=f"  {icon}  {job.filename}")
        self.url_lbl.configure(text=(job.url or "")[:95])
        self.path_lbl.configure(text=f"Save to: {job.save_path}")
        self.bar.set_progress(job.progress, active=active or processing)

        status = job.status.value
        if processing:
            status = "Merging…"
        elif job.status == DownloadStatus.FAILED and job.error:
            status = f"Failed: {job.error[:40]}"
        self._stat["status"].configure(text=status)

        if job.is_stream and job.media_meta.get("seg_total"):
            self._stat["size"].configure(
                text=f"{job.media_meta.get('seg_done', 0)} / {job.media_meta['seg_total']} parts"
            )
        else:
            size = format_bytes(job.downloaded) + (f" / {format_bytes(job.total_size)}" if job.total_size else "")
            self._stat["size"].configure(text=size or "—")
        self._stat["speed"].configure(text=format_speed(job.speed_bps) if active else "—")
        self._stat["eta"].configure(text=format_eta(job.eta_seconds) if active else "—")
        self._stat["parts"].configure(
            text=job.media_type.upper() if job.is_stream else (str(job.connections) if job.supports_ranges else "1")
        )
        self.segbar.set_job(job)

        busy = job.status in (
            DownloadStatus.DOWNLOADING, DownloadStatus.CONNECTING, DownloadStatus.QUEUED, DownloadStatus.PROCESSING,
        )
        if busy:
            self.pause_btn.configure(text="Pause", state="normal")
            self.cancel_btn.configure(state="normal")
            self.open_btn.pack_forget()
        elif job.status == DownloadStatus.PAUSED:
            self.pause_btn.configure(text="Resume", state="normal")
            self.cancel_btn.configure(state="normal")
            self.open_btn.pack_forget()
        elif job.status == DownloadStatus.COMPLETE:
            self.pause_btn.configure(state="disabled")
            self.cancel_btn.configure(state="disabled")
            self.open_btn.pack(side=tk.LEFT, padx=6)
            if self.close_when_done.get():
                self.after(1200, self._hide)
        else:  # FAILED / CANCELLED
            self.pause_btn.configure(text="Retry", state="normal")
            self.cancel_btn.configure(state="disabled")
            self.open_btn.pack_forget()

    def _toggle(self) -> None:
        job = self.manager.get_job(self.job_id)
        if not job:
            return
        if job.status in (DownloadStatus.DOWNLOADING, DownloadStatus.CONNECTING):
            self.manager.pause_job(self.job_id)
        else:
            self.manager.retry_job(self.job_id)
        self.update_view()

    def _cancel(self) -> None:
        self.manager.cancel_job(self.job_id)
        self.update_view()

    def _open_file(self) -> None:
        job = self.manager.get_job(self.job_id)
        if job:
            self.open_path(Path(job.save_path))

    def _open_folder(self) -> None:
        job = self.manager.get_job(self.job_id)
        if job:
            self.open_path(Path(job.save_path).parent)

    def _hide(self) -> None:
        # Just close the window — does NOT cancel the download.
        self._closed = True
        try:
            self.destroy()
        except tk.TclError:
            pass
