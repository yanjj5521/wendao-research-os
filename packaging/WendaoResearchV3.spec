# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


root = Path(SPECPATH).resolve().parent
icon = root / "packaging" / "generated" / "research-system.ico"

hiddenimports = []
for package in ("core", "web", "features", "services", "launchers"):
    hiddenimports.extend(collect_submodules(package))
hiddenimports.extend(collect_submodules("webview"))

a = Analysis(
    [str(root / "run_local.py")],
    pathex=[str(root)],
    binaries=[],
    datas=[
        (str(root / "templates"), "templates"),
        (str(root / "static"), "static"),
        (str(root / "hub_templates"), "hub_templates"),
        (str(root / "hub_static"), "hub_static"),
        (str(root / "VERSION"), "."),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="WendaoResearchV3",
    icon=str(icon),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
