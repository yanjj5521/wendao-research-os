"""Build the two audience-specific release packages.

    python tools/package_release.py

Outputs (inside ``out/``):

* ``WendaoResearch-v<version>-User.zip``      普通用户：EXE + 使用说明 + 校验和
* ``WendaoResearch-v<version>-Admin-Dev.zip`` 管理员/开发者：Team-Update + 源码 + 指南
* ``SHA256SUMS.txt``                          两个包的校验和

The admin package embeds ``git archive HEAD`` rather than a copy of the working
tree, so databases, session secrets and runtime folders can never leak into a
release even if they exist locally.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_PARTS = (
    ".runtime",
    ".venv",
    ".git",
    ".workbuddy",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
)
FORBIDDEN_NAMES = (".db", ".sqlite", ".sqlite3", ".token", ".secret", ".key", ".pem")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_checksums(root: Path) -> Path:
    rows = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS.txt":
            rows.append(f"{sha256(path)}  {path.relative_to(root).as_posix()}")
    target = root / "SHA256SUMS.txt"
    target.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return target


def pack(folder: Path, target: Path) -> None:
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(folder.parent).as_posix())


def assert_clean(folder: Path) -> None:
    offenders: list[str] = []
    for path in folder.rglob("*"):
        if not path.is_file():
            continue
        parts = set(path.parts)
        if parts & set(FORBIDDEN_PARTS):
            offenders.append(str(path))
        elif path.name.lower().endswith(FORBIDDEN_NAMES):
            offenders.append(str(path))
        elif "credential" in path.name.lower() or "secret" in path.name.lower():
            offenders.append(str(path))
    if offenders:
        raise SystemExit(
            "发布包中出现禁止内容，已中止：\n  " + "\n  ".join(offenders[:20])
        )


def git_is_clean() -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return not result.stdout.strip()


def export_source(target: Path, ref: str) -> None:
    target.mkdir(parents=True, exist_ok=True)
    archive = target.parent / "source.zip"
    subprocess.run(
        ["git", "archive", "--format=zip", f"--output={archive}", ref],
        cwd=ROOT,
        check=True,
    )
    with zipfile.ZipFile(archive) as bundle:
        for name in bundle.namelist():
            if name.endswith("/"):
                continue
            destination = (target / name).resolve()
            if target.resolve() not in destination.parents:
                raise SystemExit(f"源码包路径异常：{name}")
        bundle.extractall(target)
    archive.unlink()


def build_user_package(version: str, out_dir: Path, work: Path) -> Path:
    folder = work / "WendaoResearchV3"
    folder.mkdir(parents=True)
    exe = out_dir / f"WendaoResearch-v{version}-Windows-x64.exe"
    if not exe.is_file():
        raise SystemExit(f"缺少构建产物：{exe}")
    shutil.copy2(exe, folder / "WendaoResearchV3.exe")
    shutil.copy2(ROOT / "packaging" / "windows" / "使用说明.txt", folder / "使用说明.txt")
    write_checksums(folder)
    assert_clean(folder)
    target = out_dir / f"WendaoResearch-v{version}-User.zip"
    pack(folder, target)
    return target


def build_admin_package(version: str, out_dir: Path, work: Path, ref: str) -> Path:
    folder = work / f"WendaoResearch-v{version}-Admin"
    app = folder / "app"
    app.mkdir(parents=True)
    update = out_dir / f"WendaoResearch-v{version}-Team-Update.zip"
    if not update.is_file():
        raise SystemExit(f"缺少更新包：{update}")
    shutil.copy2(update, app / "Team-Update.zip")
    (app / "VERSION").write_text(version + "\n", encoding="utf-8")

    export_source(folder / "source", ref)

    shutil.copy2(ROOT / "docs" / "RELEASE_STRATEGY.md", folder / "ADMIN_GUIDE.md")
    notes = ROOT / "docs" / "releases" / f"v{version}.md"
    if notes.is_file():
        shutil.copy2(notes, folder / "RELEASE_NOTES.md")
    (folder / "README.md").write_text(
        f"""问道科研 v{version} 管理开发包
================================

给谁：同行会管理员与开发者。普通用户请下载 WendaoResearch-v{version}-User.zip。

内容
----
app/Team-Update.zip   传到同行会“版本中心”，成员据此原地更新
app/VERSION           该更新包对应的版本号
source/               该标签的完整源码快照（可用 python tools/build.py 重建）
ADMIN_GUIDE.md        发布流程、更新链路、回滚方式
RELEASE_NOTES.md      本版本变更
SHA256SUMS.txt        包内文件校验和

三步发布
--------
1. 同行会 → 版本中心 → 上传 app/Team-Update.zip，版本号填 {version}
2. 通知成员在“同行会 → 更新与同步”检查更新
3. 至少确认一台成员机更新后可正常启动；异常按 ADMIN_GUIDE.md 回滚

重建
----
cd source
py -3 -m venv .venv
.\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt
.\\.venv\\Scripts\\python.exe tools\\build.py --assemble --output out
""",
        encoding="utf-8",
    )
    write_checksums(folder)
    assert_clean(folder)
    target = out_dir / f"WendaoResearch-v{version}-Admin-Dev.zip"
    pack(folder, target)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="生成普通用户包与管理开发包")
    parser.add_argument("--ref", default="HEAD", help="源码快照的 Git 引用，默认 HEAD")
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="允许工作区有未提交改动（源码包仍取 --ref，可能与 EXE 不一致）",
    )
    args = parser.parse_args()

    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    out_dir = ROOT / "out"
    if not args.allow_dirty and not git_is_clean():
        raise SystemExit(
            "工作区有未提交改动：源码快照会与 EXE 不一致。"
            "请先提交，或显式使用 --allow-dirty。"
        )

    work = ROOT / "build" / "release"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    user = build_user_package(version, out_dir, work / "user")
    admin = build_admin_package(version, out_dir, work / "admin", args.ref)

    sums = out_dir / "SHA256SUMS.txt"
    sums.write_text(
        "\n".join(f"{sha256(p)}  {p.name}" for p in (user, admin)) + "\n",
        encoding="utf-8",
    )
    print(f"普通用户包：{user}  ({user.stat().st_size / 1048576:.1f} MiB)")
    print(f"管理开发包：{admin}  ({admin.stat().st_size / 1048576:.1f} MiB)")
    print(f"校验和：    {sums}")
    shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
