"""Reusable UI widgets (progress / segment bars)."""

from __future__ import annotations

import tkinter as tk

from magic_downloader.gui import theme as T
from magic_downloader.models import DownloadJob, DownloadStatus, SegmentState


class ProgressBar(tk.Canvas):
    """Horizontal progress bar with percent text."""

    def __init__(self, master: tk.Misc, height: int = 18, **kwargs) -> None:
        super().__init__(
            master,
            height=height,
            bg=T.BG_LIST,
            highlightthickness=1,
            highlightbackground=T.BORDER,
            **kwargs,
        )
        self._value = 0.0
        self.bind("<Configure>", lambda e: self.redraw())

    def set_progress(self, percent: float, active: bool = False) -> None:
        self._value = max(0.0, min(100.0, percent))
        self._active = active
        self.redraw()

    def redraw(self) -> None:
        self.delete("all")
        w = max(1, self.winfo_width())
        h = max(1, self.winfo_height())
        fill = int(w * self._value / 100.0)
        color = T.GREEN if getattr(self, "_active", False) else T.GREEN_SEG_DONE
        if self._value >= 100:
            color = T.GREEN_SEG_DONE
        self.create_rectangle(0, 0, w, h, fill="#e9edf2", outline="")
        if fill > 0:
            self.create_rectangle(0, 0, fill, h, fill=color, outline="")
        label = f"{self._value:.1f}%"
        self.create_text(w // 2, h // 2, text=label, fill=T.FG, font=T.FONT_SMALL)


class SegmentBar(tk.Canvas):
    """IDM-style multi-connection segment map (green blocks)."""

    def __init__(self, master: tk.Misc, height: int = 28, **kwargs) -> None:
        super().__init__(
            master,
            height=height,
            bg="#1a1a1a",
            highlightthickness=1,
            highlightbackground=T.BORDER,
            **kwargs,
        )
        self._job: DownloadJob | None = None
        self.bind("<Configure>", lambda e: self.redraw())

    def set_job(self, job: DownloadJob | None) -> None:
        self._job = job
        self.redraw()

    def redraw(self) -> None:
        self.delete("all")
        w = max(1, self.winfo_width())
        h = max(1, self.winfo_height())
        self.create_rectangle(0, 0, w, h, fill="#222222", outline="")

        job = self._job
        if not job or job.total_size <= 0:
            # Single stream progress fallback
            if job and job.downloaded > 0 and job.total_size > 0:
                fill = int(w * job.downloaded / job.total_size)
                self.create_rectangle(0, 2, fill, h - 2, fill=T.GREEN_SEG, outline="")
            elif job and job.status == DownloadStatus.COMPLETE:
                self.create_rectangle(0, 2, w, h - 2, fill=T.GREEN_SEG_DONE, outline="")
            else:
                self.create_text(
                    w // 2, h // 2, text="No segment data yet", fill="#888", font=T.FONT_SMALL
                )
            return

        segments: list[SegmentState] = job.segments or []
        if not segments:
            # Approximate single bar
            pct = job.downloaded / job.total_size
            fill = max(1, int(w * pct))
            color = T.GREEN_SEG if job.status == DownloadStatus.DOWNLOADING else T.GREEN_SEG_DONE
            self.create_rectangle(0, 2, fill, h - 2, fill=color, outline="")
            return

        total = job.total_size
        for seg in segments:
            x0 = int(w * seg.start / total)
            x1 = int(w * (seg.end + 1) / total)
            # background for full segment range
            self.create_rectangle(x0, 2, max(x0 + 1, x1), h - 2, fill="#333333", outline="")
            if seg.downloaded > 0:
                done_end = seg.start + seg.downloaded
                xd = int(w * done_end / total)
                color = T.GREEN_SEG
                if seg.remaining <= 0:
                    color = T.GREEN_SEG_DONE
                elif job.status == DownloadStatus.PAUSED:
                    color = T.ORANGE
                self.create_rectangle(x0, 2, max(x0 + 1, xd), h - 2, fill=color, outline="")

        # divider lines between segments
        for seg in segments[1:]:
            x = int(w * seg.start / total)
            self.create_line(x, 0, x, h, fill="#111111")


class ToolbarButton(tk.Frame):
    """Large labeled toolbar button with icon glyph."""

    def __init__(
        self,
        master: tk.Misc,
        icon: str,
        text: str,
        command,
        bg: str = T.BG_TOOLBAR,
        **kwargs,
    ) -> None:
        super().__init__(master, bg=bg, **kwargs)
        self._command = command
        self._bg = bg
        self._hover = T.ACCENT_HOVER
        self._enabled = True

        self.icon_lbl = tk.Label(
            self, text=icon, font=("Segoe UI Emoji", 16), bg=bg, fg=T.FG_ON_DARK, cursor="hand2"
        )
        self.text_lbl = tk.Label(
            self, text=text, font=T.FONT_TOOLBAR, bg=bg, fg=T.FG_ON_DARK, cursor="hand2"
        )
        self.icon_lbl.pack(padx=10, pady=(6, 0))
        self.text_lbl.pack(padx=10, pady=(0, 6))

        for w in (self, self.icon_lbl, self.text_lbl):
            w.bind("<Button-1>", self._click)
            w.bind("<Enter>", self._enter)
            w.bind("<Leave>", self._leave)

    def set_enabled(self, enabled: bool) -> None:
        """Dim/undim the button. A disabled button ignores clicks and hover so
        it never looks active when its action doesn't apply."""
        enabled = bool(enabled)
        if enabled == self._enabled:
            return
        self._enabled = enabled
        fg = T.FG_ON_DARK if enabled else T.FG_ON_DARK_DISABLED
        cursor = "hand2" if enabled else "arrow"
        for w in (self.icon_lbl, self.text_lbl):
            w.configure(fg=fg, cursor=cursor)
        # Reset background in case the pointer was hovering when it disabled.
        for w in (self, self.icon_lbl, self.text_lbl):
            w.configure(bg=self._bg)

    def _click(self, _event=None) -> None:
        if self._enabled and self._command:
            self._command()

    def _enter(self, _event=None) -> None:
        if not self._enabled:
            return
        for w in (self, self.icon_lbl, self.text_lbl):
            w.configure(bg=self._hover)

    def _leave(self, _event=None) -> None:
        for w in (self, self.icon_lbl, self.text_lbl):
            w.configure(bg=self._bg)
