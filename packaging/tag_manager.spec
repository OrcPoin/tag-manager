# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

root = Path(SPECPATH).parent.parent

a = Analysis(
    [str(root / "tag_manager.pyw")],
    pathex=[str(root)],
    binaries=[],
    datas=[
        (str(root / "frontend" / "dist"), "frontend/dist"),
        (str(root / "stoplist.txt"), "."),
        (str(root / "presets.example.json"), "."),
    ],
    hiddenimports=["webview.platforms.edgechromium", "pystray._win32"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["streamlit"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="TagManager",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
)
