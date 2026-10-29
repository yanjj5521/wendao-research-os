"""Unified command entry for 问道科研.

The shipped Windows build is a single executable.  Every operational task that
used to live in a separate ``.cmd`` file is a subcommand here, so users only
ever need one file on disk::

    WendaoResearchV3.exe                # 个人端桌面窗口（默认）
    WendaoResearchV3.exe --mode team    # 同行会中心（桌面窗口）
    WendaoResearchV3.exe install        # 安装到 %LOCALAPPDATA% 并创建快捷方式
    WendaoResearchV3.exe shortcut       # 只重建桌面/开始菜单快捷方式
    WendaoResearchV3.exe diagnose       # 自检并输出诊断信息
    WendaoResearchV3.exe version        # 版本与数据目录
    WendaoResearchV3.exe reset-env      # 仅源码模式：删除 .venv

Source checkouts use the same words through ``python run_local.py <command>``.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

COMMANDS = ("install", "shortcut", "diagnose", "version", "reset-env", "help")


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _installer_script() -> Path | None:
    """Locate install_windows.ps1 inside the bundle, beside the exe, or in source."""
    candidates: list[Path] = []
    if _is_frozen():
        bundle = getattr(sys, "_MEIPASS", "")
        if bundle:
            candidates.append(Path(bundle) / "packaging" / "windows" / "install_windows.ps1")
        candidates.append(
            Path(sys.executable).resolve().parent
            / "packaging"
            / "windows"
            / "install_windows.ps1"
        )
    else:
        candidates.append(_repo_root() / "packaging" / "windows" / "install_windows.ps1")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _built_executable() -> Path | None:
    """Find a freshly built exe so source checkouts can install what they built."""
    from core.version import APP_VERSION

    root = _repo_root()
    candidates = [
        root / "out" / f"WendaoResearch-v{APP_VERSION}-Windows-x64.exe",
        root / "dist" / "WendaoResearchV3.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run_installer(extra: list[str]) -> int:
    if os.name != "nt":
        print("安装与快捷方式命令仅支持 Windows。", file=sys.stderr)
        return 2
    script = _installer_script()
    if script is None:
        print(
            "找不到安装脚本 packaging/windows/install_windows.ps1。\n"
            "安装版 EXE 应内置该脚本；源码模式请在仓库根目录运行。",
            file=sys.stderr,
        )
        return 1
    command = [
        "powershell",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        *extra,
    ]
    return subprocess.call(command)


def _command_install(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="install", description="安装程序并创建快捷方式")
    parser.add_argument("--source", default="", help="指定要安装的 EXE 路径")
    parser.add_argument("--no-launch", action="store_true", help="安装完成后不自动启动")
    args = parser.parse_args(argv)

    if args.source:
        source = Path(args.source).expanduser().resolve()
    elif _is_frozen():
        source = Path(sys.executable).resolve()
    else:
        source = _built_executable()
    if source is None or not source.is_file():
        print(
            "找不到可安装的 EXE。请先运行 python tools/build.py，"
            "或用 --source 指定已有 EXE。",
            file=sys.stderr,
        )
        return 1
    print(f"安装源：{source}")
    print(f"SHA-256：{_sha256(source)}")
    extra = ["-SourceExe", str(source)]
    if args.no_launch:
        extra.append("-NoLaunch")
    return _run_installer(extra)


def _command_shortcut(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="shortcut", description="重建桌面与开始菜单快捷方式")
    parser.parse_args(argv)
    return _run_installer(["-ShortcutsOnly"])


def _command_diagnose(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="diagnose", description="运行内置自检")
    parser.add_argument("--keep", action="store_true", help="保留自检生成的临时数据目录")
    args = parser.parse_args(argv)

    # ``core.runtime_paths`` freezes DATA_ROOT at import time, and ``core.version``
    # imports it.  The temporary directory must therefore be in the environment
    # BEFORE the first core import, otherwise a diagnose run would touch real data.
    data_dir = tempfile.mkdtemp(prefix="research-os-diagnose-")
    os.environ["RESEARCH_OS_DATA_DIR"] = data_dir

    from core.version import APP_VERSION

    print(f"问道科研 {APP_VERSION} 诊断")
    print(f"程序：{Path(sys.executable).resolve() if _is_frozen() else _repo_root()}")
    print(f"临时数据目录：{data_dir}")
    from core.packaged_self_check import run_packaged_self_check
    from web.app import app as web_app

    run_packaged_self_check(web_app)
    print("诊断完成：全部检查通过。")
    if not args.keep:
        shutil.rmtree(data_dir, ignore_errors=True)
    return 0


def _command_version(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="version", description="显示版本与数据目录")
    parser.parse_args(argv)
    from core.runtime_paths import DATA_ROOT
    from core.version import APP_VERSION

    print(f"问道科研 {APP_VERSION}")
    print(f"运行方式：{'打包 EXE' if _is_frozen() else '源码'}")
    print(f"程序位置：{Path(sys.executable).resolve() if _is_frozen() else _repo_root()}")
    print(f"数据目录：{DATA_ROOT}")
    return 0


def _command_reset_env(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="reset-env", description="仅源码模式：删除 .venv")
    parser.add_argument("--yes", action="store_true", help="确认删除，不再询问")
    args = parser.parse_args(argv)
    if _is_frozen():
        print("安装版不依赖 .venv，无需重置环境。", file=sys.stderr)
        return 1
    target = _repo_root() / ".venv"
    if not target.is_dir():
        print("当前没有 .venv，无需重置。")
        return 0
    print("将删除：", target)
    print("数据库、论文、笔记和附件不会被删除。")
    if not args.yes:
        print("如确认，请运行：python run_local.py reset-env --yes")
        return 1
    shutil.rmtree(target)
    print("已删除 .venv。下次启动会重新创建。")
    return 0


def _command_help(argv: list[str]) -> int:
    print(__doc__)
    return 0


_HANDLERS = {
    "install": _command_install,
    "shortcut": _command_shortcut,
    "diagnose": _command_diagnose,
    "version": _command_version,
    "reset-env": _command_reset_env,
    "help": _command_help,
}


def dispatch_if_command(argv: list[str]) -> int | None:
    """Run a subcommand when the first argument is one; else return None.

    Returning ``None`` leaves ``run_local.py`` on its original code path, so the
    desktop application keeps every existing flag (``--serve``, ``--self-check``,
    ``--mode team`` ...) unchanged.
    """
    if not argv:
        return None
    command = argv[0].strip().lower()
    handler = _HANDLERS.get(command)
    if handler is None:
        return None
    return handler(argv[1:])
