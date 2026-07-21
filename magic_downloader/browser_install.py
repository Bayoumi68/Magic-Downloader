"""Register the browser extension with the user's installed browsers.

Chromium browsers support a per-user "external extension" registry key: point it
at the Chrome Web Store item and the browser fetches the extension itself on the
next launch. This only works because the extension is store-hosted — Chrome
stopped honouring locally-sideloaded extensions years ago.

Two things worth knowing, because they shape the UI copy:

* **No admin needed.** These live under HKEY_CURRENT_USER, so a per-user install
  can write them. (The machine-wide HKLM variant would need elevation.)
* **The browser still asks.** Externally-registered extensions arrive *disabled*;
  the browser shows a "new extension added" prompt and the user clicks Enable.
  No application can bypass that consent step — every download manager that
  claims to "install the extension for you" stops here too.

Firefox's registry equivalent wants a path to a locally signed ``.xpi`` rather
than a store id, so for Firefox we just open the AMO listing — one click to add,
and it auto-updates from there.
"""

from __future__ import annotations

import sys
import webbrowser
from dataclasses import dataclass

# Chrome Web Store item (verified against the live listing).
CHROME_EXT_ID = "pgiehelnpkakggoeglldnhmidapoeblb"
CHROME_UPDATE_URL = "https://clients2.google.com/service/update2/crx"
CHROME_STORE_URL = f"https://chromewebstore.google.com/detail/{CHROME_EXT_ID}"
FIREFOX_STORE_URL = "https://addons.mozilla.org/en-US/firefox/addon/magic-downloader/"

_APP_PATHS = r"Software\Microsoft\Windows\CurrentVersion\App Paths"


@dataclass(frozen=True)
class Browser:
    key: str            # internal id
    name: str           # shown to the user
    exe: str            # App Paths executable name, used to detect it
    ext_root: str | None  # HKCU external-extension parent key (None => not Chromium)
    store_url: str

    @property
    def chromium(self) -> bool:
        return self.ext_root is not None


BROWSERS: tuple[Browser, ...] = (
    Browser("chrome", "Google Chrome", "chrome.exe",
            r"Software\Google\Chrome\Extensions", CHROME_STORE_URL),
    Browser("edge", "Microsoft Edge", "msedge.exe",
            r"Software\Microsoft\Edge\Extensions", CHROME_STORE_URL),
    Browser("brave", "Brave", "brave.exe",
            r"Software\BraveSoftware\Brave-Browser\Extensions", CHROME_STORE_URL),
    Browser("firefox", "Mozilla Firefox", "firefox.exe", None, FIREFOX_STORE_URL),
)


def _winreg():
    """winreg, or None off-Windows (keeps the module importable everywhere)."""
    if sys.platform != "win32":
        return None
    try:
        import winreg
        return winreg
    except ImportError:  # pragma: no cover - Windows always has it
        return None


def is_installed(b: Browser) -> bool:
    """True if the browser is registered in Windows' App Paths (HKCU or HKLM)."""
    reg = _winreg()
    if reg is None:
        return False
    for hive in (reg.HKEY_CURRENT_USER, reg.HKEY_LOCAL_MACHINE):
        try:
            with reg.OpenKey(hive, rf"{_APP_PATHS}\{b.exe}"):
                return True
        except OSError:
            continue
    return False


def installed_browsers() -> list[Browser]:
    return [b for b in BROWSERS if is_installed(b)]


def _ext_key(b: Browser) -> str:
    return rf"{b.ext_root}\{CHROME_EXT_ID}"


def is_registered(b: Browser) -> bool:
    """True if we've already staged the extension for this Chromium browser."""
    reg = _winreg()
    if reg is None or not b.chromium:
        return False
    try:
        with reg.OpenKey(reg.HKEY_CURRENT_USER, _ext_key(b)) as k:
            url, _ = reg.QueryValueEx(k, "update_url")
            return bool(url)
    except OSError:
        return False


def register(b: Browser) -> bool:
    """Stage the store extension for *b*. Returns True on success.

    Writes HKCU\\<browser>\\Extensions\\<id>\\update_url — the browser then pulls
    the extension from the Web Store itself and prompts the user to enable it.
    """
    reg = _winreg()
    if reg is None or not b.chromium:
        return False
    try:
        with reg.CreateKey(reg.HKEY_CURRENT_USER, _ext_key(b)) as k:
            reg.SetValueEx(k, "update_url", 0, reg.REG_SZ, CHROME_UPDATE_URL)
        return True
    except OSError:
        return False


def unregister(b: Browser) -> bool:
    """Remove our staging key (leaving stray registry keys behind is rude).

    True if the key is gone afterwards — including when it was never there.
    """
    reg = _winreg()
    if reg is None or not b.chromium:
        return False
    try:
        reg.DeleteKey(reg.HKEY_CURRENT_USER, _ext_key(b))
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False


def open_store(b: Browser) -> None:
    webbrowser.open(b.store_url)
