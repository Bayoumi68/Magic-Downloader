"""Run Magic Downloader when Windows starts (per-user, no admin needed).

Uses the HKCU 'Run' registry key. On non-Windows this is a graceful no-op.
"""

from __future__ import annotations

import sys
from pathlib import Path

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = "MagicDownloader"


def _startup_command() -> str:
    """The command Windows should run at login."""
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable).resolve()}"'
    # Running from source: launch the GUI with the same interpreter.
    main_py = Path(__file__).resolve().parent.parent / "main.py"
    return f'"{Path(sys.executable).resolve()}" "{main_py}"'


def is_supported() -> bool:
    return sys.platform == "win32"


def is_enabled() -> bool:
    if not is_supported():
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, _VALUE_NAME)
            return bool(value)
    except FileNotFoundError:
        return False
    except OSError:
        return False


def set_enabled(enable: bool) -> bool:
    """Add/remove the startup entry. Returns the resulting state."""
    if not is_supported():
        return False
    try:
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            if enable:
                winreg.SetValueEx(key, _VALUE_NAME, 0, winreg.REG_SZ, _startup_command())
            else:
                try:
                    winreg.DeleteValue(key, _VALUE_NAME)
                except FileNotFoundError:
                    pass
        return enable
    except OSError:
        return is_enabled()
