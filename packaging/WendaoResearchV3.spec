# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_submodules


root = Path(SPECPATH).resolve().parent
icon = root / "packaging" / "generated" / "research-system.ico"

from PyInstaller.utils.hooks import collect_all


headnet_dir = root / "native" / "headnet"
headnet_suffix = ".exe" if sys.platform == "win32" else ""
headnet_candidates = sorted(
    headnet_dir.glob(f"headnet-runtime*{headnet_suffix}"),
    key=lambda path: path.stat().st_mtime,
    reverse=True,
)
if not headnet_candidates:
    raise SystemExit("native/headnet helper is missing; run tools/build_headnet.py first")
headnet_helper = headnet_candidates[0]

hiddenimports = []
for package in ("core", "web", "features", "services", "launchers", "hub"):
    hiddenimports.extend(collect_submodules(package))
hiddenimports.extend(collect_submodules("webview"))

# pydantic v2 的核心是编译扩展（pydantic_core._pydantic_core）。PyInstaller 的
# 自动 hook 在部分版本/环境下会漏收，导致启动即
# "ModuleNotFoundError: No module named 'pydantic_core._pydantic_core'"。
# 这里显式 collect_all，把纯模块、二进制与元数据全部强制打包。
pydantic_datas, pydantic_binaries, pydantic_hidden = collect_all("pydantic")
pydcore_datas, pydcore_binaries, pydcore_hidden = collect_all("pydantic_core")
hiddenimports.extend(pydantic_hidden)
hiddenimports.extend(pydcore_hidden)

a = Analysis(
    [str(root / "run_local.py")],
    pathex=[str(root)],
    binaries=list(pydantic_binaries) + list(pydcore_binaries),
    datas=[
        *pydantic_datas,
        *pydcore_datas,
        (str(root / "templates"), "templates"),
        (str(root / "static"), "static"),
        (str(root / "hub_templates"), "hub_templates"),
        (str(root / "hub_static"), "hub_static"),
        (str(headnet_helper), "headnet"),
        (str(root / "VERSION"), "."),
        (str(root / "packaging" / "windows" / "install_windows.ps1"), "packaging/windows"),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "unittest", "pydoc_data", "tkinter", "fitz", "pymupdf", "openpyxl"],
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
