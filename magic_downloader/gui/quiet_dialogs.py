"""Silent, themed drop-in replacements for tkinter.messagebox / simpledialog.

Tk's native message boxes on Windows call the Win32 ``MessageBox`` API, which
plays a system sound (Critical Stop / Exclamation / Asterisk) tied to the icon.
These custom ``Toplevel`` dialogs render the same information with the app theme
and **never make a sound**.

The public functions mirror the parts of ``tkinter.messagebox`` and
``tkinter.simpledialog`` this app uses, with the same call signatures and
return values, so modules can simply do::

    from magic_downloader.gui import quiet_dialogs as messagebox
    from magic_downloader.gui import quiet_dialogs as simpledialog
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Optional

from . import theme as T

_ICONS = {"info": "ℹ", "warning": "⚠", "error": "⛔", "question": "❓"}
_ICON_FG = {
    "info": T.ACCENT,
    "warning": T.ORANGE,
    "error": T.RED,
    "question": T.ACCENT,
}


def _resolve_parent(parent):
    if parent is not None:
        return parent
    getter = getattr(tk, "_get_default_root", None)
    if getter is not None:
        try:
            return getter()
        except Exception:
            pass
    return getattr(tk, "_default_root", None)


def _center(win, parent) -> None:
    win.update_idletasks()
    try:
        if parent is not None and parent.winfo_exists():
            px, py = parent.winfo_rootx(), parent.winfo_rooty()
            pw, ph = parent.winfo_width(), parent.winfo_height()
            x = px + max(0, (pw - win.winfo_width()) // 2)
            y = py + max(0, (ph - win.winfo_height()) // 2)
        else:
            x = (win.winfo_screenwidth() - win.winfo_width()) // 2
            y = (win.winfo_screenheight() - win.winfo_height()) // 2
        win.geometry(f"+{max(0, x)}+{max(0, y)}")
    except Exception:
        pass


def _run_modal(win, parent, on_close_value, result_holder):
    """Make ``win`` modal, wait for it, and restore the parent's grab."""
    prev_grab = None
    try:
        prev_grab = parent.grab_current() if parent is not None else None
    except Exception:
        prev_grab = None

    def _close():
        result_holder["v"] = on_close_value
        win.destroy()

    win.protocol("WM_DELETE_WINDOW", _close)
    win.bind("<Escape>", lambda _e: _close())
    _center(win, parent)
    try:
        if parent is not None:
            win.transient(parent)
    except Exception:
        pass
    # Make it modal and force it to the FRONT — and keep it there until the user
    # answers. A plain Toplevel can otherwise open hidden behind its parent / the
    # browser and never take focus. Stay -topmost for the dialog's whole life
    # (the flag dies with the window) instead of dropping it after a moment.
    try:
        win.grab_set()
    except tk.TclError:
        try:
            win.wait_visibility()
            win.grab_set()
        except Exception:
            pass
    def _raise():
        if not win.winfo_exists():
            return
        try:
            win.deiconify()
            win.attributes("-topmost", True)
            win.lift()
            win.focus_force()
            first = win.focus_get()
            if first is None:
                win.focus_set()
        except Exception:
            pass
    _raise()
    win.after(60, _raise)   # re-assert after the window manager settles
    win.wait_window()
    # Restore the modal grab the parent dialog (if any) held before us, so
    # nested dialogs don't leave the underlying dialog non-modal.
    try:
        if prev_grab is not None and prev_grab.winfo_exists():
            prev_grab.grab_set()
    except Exception:
        pass


def _message(icon, title, message, parent, buttons):
    """buttons: list of (label, return_value, is_default). Returns chosen value."""
    parent = _resolve_parent(parent)
    win = tk.Toplevel(parent) if parent is not None else tk.Toplevel()
    win.title(title or "")
    win.configure(bg=T.BG)
    win.resizable(False, False)
    result = {"v": buttons[-1][1]}

    body = tk.Frame(win, bg=T.BG)
    body.pack(fill=tk.BOTH, expand=True, padx=20, pady=(18, 8))
    tk.Label(
        body, text=_ICONS.get(icon, ""), bg=T.BG, fg=_ICON_FG.get(icon, T.FG),
        font=("Segoe UI", 24),
    ).pack(side=tk.LEFT, anchor="n", padx=(0, 14))
    tk.Label(
        body, text=message or "", bg=T.BG, fg=T.FG, font=T.FONT_UI,
        justify=tk.LEFT, wraplength=400,
    ).pack(side=tk.LEFT, anchor="w")

    row = tk.Frame(win, bg=T.BG)
    row.pack(fill=tk.X, padx=16, pady=(4, 14))

    def _make(label, value):
        def _cb():
            result["v"] = value
            win.destroy()
        return ttk.Button(row, text=label, command=_cb)

    # Right-align the button group but keep the buttons in the SAME left-to-right
    # order as the `buttons` list (affirmative first) — matching the native
    # Windows dialog layout, e.g. [Yes] [No]. Packing side=RIGHT in list order
    # would reverse them to [No] [Yes], making users click the wrong button, so
    # we pack the list in reverse.
    default_btn = None
    for label, value, is_default in reversed(buttons):
        b = _make(label, value)
        b.pack(side=tk.RIGHT, padx=4)
        if is_default:
            default_btn = b
    if default_btn is not None:
        default_btn.focus_set()
        win.bind("<Return>", lambda _e: default_btn.invoke())

    _run_modal(win, parent, result["v"], result)
    return result["v"]


# --- messagebox API ---------------------------------------------------------

def showinfo(title=None, message=None, parent=None, **_kw):
    return _message("info", title, message, parent, [("OK", "ok", True)])


def showwarning(title=None, message=None, parent=None, **_kw):
    return _message("warning", title, message, parent, [("OK", "ok", True)])


def showerror(title=None, message=None, parent=None, **_kw):
    return _message("error", title, message, parent, [("OK", "ok", True)])


def askyesno(title=None, message=None, parent=None, **_kw) -> bool:
    return bool(_message(
        "question", title, message, parent,
        [("Yes", True, True), ("No", False, False)],
    ))


def askokcancel(title=None, message=None, parent=None, **_kw) -> bool:
    return bool(_message(
        "question", title, message, parent,
        [("OK", True, True), ("Cancel", False, False)],
    ))


def askquestion(title=None, message=None, parent=None, **_kw) -> str:
    return "yes" if askyesno(title, message, parent) else "no"


def ask(title=None, message=None, buttons=None, parent=None, icon="question", **_kw):
    """Custom multi-button prompt. ``buttons`` is a list of ``(label, value)``;
    the first is the default (focused, Enter). Returns the chosen value, or the
    last button's value on Escape / window-close. Silent (no system sound)."""
    pairs = buttons or [("OK", "ok")]
    spec = [(label, value, i == 0) for i, (label, value) in enumerate(pairs)]
    return _message(icon, title, message, parent, spec)


# --- simpledialog API -------------------------------------------------------

def askstring(title=None, prompt=None, parent=None, initialvalue=None,
              show=None, **_kw) -> Optional[str]:
    parent = _resolve_parent(parent)
    win = tk.Toplevel(parent) if parent is not None else tk.Toplevel()
    win.title(title or "")
    win.configure(bg=T.BG)
    win.resizable(False, False)
    result = {"v": None}

    body = tk.Frame(win, bg=T.BG)
    body.pack(fill=tk.BOTH, expand=True, padx=20, pady=(18, 8))
    tk.Label(
        body, text=prompt or "", bg=T.BG, fg=T.FG, font=T.FONT_UI,
        justify=tk.LEFT, wraplength=340, anchor="w",
    ).pack(fill=tk.X)
    var = tk.StringVar(value=initialvalue or "")
    entry = ttk.Entry(body, textvariable=var, width=40)
    if show:
        entry.configure(show=show)
    entry.pack(fill=tk.X, pady=(8, 0))
    entry.focus_set()
    entry.select_range(0, tk.END)

    row = tk.Frame(win, bg=T.BG)
    row.pack(fill=tk.X, padx=16, pady=(4, 14))

    def _ok():
        result["v"] = var.get()
        win.destroy()

    def _cancel():
        result["v"] = None
        win.destroy()

    ttk.Button(row, text="Cancel", command=_cancel).pack(side=tk.RIGHT, padx=4)
    ok_btn = ttk.Button(row, text="OK", command=_ok)
    ok_btn.pack(side=tk.RIGHT, padx=4)
    win.bind("<Return>", lambda _e: _ok())

    _run_modal(win, parent, None, result)
    return result["v"]
