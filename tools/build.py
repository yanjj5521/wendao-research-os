from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()


def run(*command: str) -> None:
    subprocess.run(command, cwd=ROOT, check=True)


def build_executable() -> Path:
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
    return executable


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
