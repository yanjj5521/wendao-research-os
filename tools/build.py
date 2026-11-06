from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()


def run(*command: str) -> None:
    subprocess.run(command, cwd=ROOT, check=True)


def build_executable() -> Path:
    run(sys.executable, "tools/build_headnet.py")
    run(sys.executable, "packaging/build_icons.py")
    run(
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        str(ROOT / "packaging" / "WendaoResearchV3.spec"),
    )
    executable = ROOT / "dist" / (
        "WendaoResearchV3.exe" if sys.platform == "win32" else "WendaoResearchV3"
    )
    if not executable.exists():
        raise FileNotFoundError(executable)
    verify_executable(executable)
    return executable


def verify_executable(executable: Path) -> None:
    """构建后自检：冷启动必须能 import 全部依赖并报出版本。

    防止"pydantic_core._pydantic_core 缺失"这类只会在用户机器上暴露的
    打包缺件问题流出发布包。
    """
    # A frozen one-file app starts a child process. If the child keeps an
    # inherited pipe open after a timeout, subprocess.run's cleanup can hang
    # indefinitely. Files and a bounded process-tree kill avoid that deadlock.
    with tempfile.TemporaryDirectory(prefix="wendao-build-check-", ignore_cleanup_errors=True) as temp:
        temp_root = Path(temp)
        stdout_path = temp_root / "stdout.txt"
        stderr_path = temp_root / "stderr.txt"
        environment = os.environ.copy()
        environment["RESEARCH_OS_DATA_DIR"] = str(temp_root / "data")
        environment["PYTHONUTF8"] = "1"
        environment["PYTHONIOENCODING"] = "utf-8"
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            process = subprocess.Popen(
                [str(executable), "version"],
                cwd=ROOT,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            try:
                returncode = process.wait(timeout=180)
            except subprocess.TimeoutExpired as exc:
                if os.name == "nt":
                    try:
                        subprocess.run(
                            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=10,
                            check=False,
                        )
                    except (OSError, subprocess.TimeoutExpired):
                        pass
                if process.poll() is None:
                    process.kill()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
                raise SystemExit(f"构建自检失败：{executable.name} 启动超时。") from exc
        output = stdout_path.read_text(encoding="utf-8", errors="replace")
        output += stderr_path.read_text(encoding="utf-8", errors="replace")
    if returncode != 0 or "ModuleNotFoundError" in output or VERSION not in output:
        tail = chr(10).join(output.splitlines()[-12:])
        message = chr(10).join(
            [
                "构建自检失败：打包后的 EXE 无法正常启动（依赖可能缺失）。",
                f"退出码：{returncode}",
                tail,
                "提示：检查 spec 是否 collect 了 pydantic_core 等编译依赖。",
            ]
        )
        raise SystemExit(message)
    print(f"构建自检通过：{executable.name} -> {VERSION}")


def assemble_windows(executable: Path, output_dir: Path) -> list[Path]:
    if sys.platform != "win32":
        raise RuntimeError("Windows release assembly must run on a Windows host.")
    output_dir.mkdir(parents=True, exist_ok=True)
    direct = output_dir / f"WendaoResearch-v{VERSION}-Windows-x64.exe"
    shutil.copy2(executable, direct)

    portable_parent = ROOT / "build" / "portable"
    portable_root = portable_parent / "WendaoResearchV3"
    if portable_parent.exists():
        shutil.rmtree(portable_parent)
    portable_root.mkdir(parents=True)
    shutil.copy2(executable, portable_root / "WendaoResearchV3.exe")
    # The administrator starts the optional centre from the in-app 同行会 page.
    # Do not ship a second visible CMD launcher in the portable/update bundle.
    for name in ("portable.flag", "使用说明.txt"):
        shutil.copy2(ROOT / "packaging" / "windows" / name, portable_root / name)
    shutil.copy2(ROOT / "THIRD_PARTY_NOTICES.md", portable_root / "THIRD_PARTY_NOTICES.md")

    archive = output_dir / f"WendaoResearch-v{VERSION}-Portable.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for file in sorted(portable_root.rglob("*")):
            if file.is_file():
                bundle.write(file, file.relative_to(portable_parent))
    # The exact same portable bundle is the only package an administrator
    # uploads in the team centre.  It contains no user_data and is therefore
    # safe for a member-side update to unpack beside an existing installation.
    update_archive = output_dir / f"WendaoResearch-v{VERSION}-Team-Update.zip"
    shutil.copy2(archive, update_archive)
    return [direct, archive, update_archive]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--assemble", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "out")
    args = parser.parse_args()
    executable = build_executable()
    print(executable)
    if args.assemble:
        for artifact in assemble_windows(executable, args.output):
            print(artifact)


if __name__ == "__main__":
    main()
