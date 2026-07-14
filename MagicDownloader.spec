# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build spec for Magic Downloader.

Build:   pyinstaller --noconfirm MagicDownloader.spec
Output:  dist/MagicDownloader/MagicDownloader.exe   (portable folder, no Python needed)
"""

import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

# Bundle the browser extension + the plain-text install guide as read-only data.
datas = [
    ("browser_extension", "browser_extension"),
    ("INSTALL_BROWSER.txt", "."),
    ("logo_toolbar.png", "."),   # cropped emblem (toolbar brand)
    ("logo_wordmark.png", "."),  # cropped "Magic Downloader" wordmark
]
binaries = []
hiddenimports = []

# yt-dlp loads its ~1800 site extractors dynamically — collect them all,
# otherwise site downloads fail in the frozen build.
_d, _b, _h = collect_all("yt_dlp")
datas += _d
binaries += _b
hiddenimports += _h

# cryptography (AES-128 HLS) has native bits; make sure submodules come along.
hiddenimports += collect_submodules("cryptography")

# pystray picks its OS backend dynamically — bundle them + Pillow.
hiddenimports += collect_submodules("pystray")
hiddenimports += ["PIL.Image", "PIL._tkinter_finder"]

icon_file = os.path.join("browser_extension", "icons", "app.ico")
icon = icon_file if os.path.exists(icon_file) else None

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "mypy"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MagicDownloader",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # GUI app — no console window
    disable_windowed_traceback=False,
    icon=icon,
    version="version_info.txt",   # embed FileVersion/ProductVersion 0.5.1.0
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="MagicDownloader",
)
