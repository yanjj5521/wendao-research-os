"""构建云端部署目录（WorkBuddy Sites 沙箱用）。

只复制运行所需内容，排除测试、工具、文档、桌面与打包脚本，
避免把 .venv / 缓存 / 发布脚本一起上传。

用法：python tools/prepare_cloud_deploy.py [--out deploy-cloud]
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

COPY_DIRS = (
    "core",
    "features",
    "services",
    "web",
    "hub",
    "templates",
    "static",
    "hub_templates",
    "hub_static",
    "launchers",
)
COPY_FILES = (
    "run_local.py",
    "run_hub.py",
    "hub_db.py",
    "desktop_shell.py",
    "desktop_pet.py",
    "desktop_shell.py",
    "desktop_pet.py",
    "content_library.py",
    "extractors.py",
    "research_tools.py",
    "runtime_paths.py",
    "workspace_profiles.py",
    "version.py",
    "requirements.txt",
    "VERSION",
    "README.md",
)

NOTE = """# 云端部署目录

本目录由 `tools/prepare_cloud_deploy.py` 生成，只包含云端运行所需内容。

启动命令（WorkBuddy Sites 会注入 PORT 环境变量）：

    RESEARCH_OS_ACCESS_CODE=<口令> PORT=<端口> python run_local.py --serve

- `RESEARCH_OS_ACCESS_CODE` 未设置时云端 `PORT` 模式会拒绝启动，避免 fail-open。
- 生产环境同时设置 `RESEARCH_OS_HTTPS_ONLY=1`，让门禁 Cookie 强制使用 Secure。
- 数据写入本目录的 `.runtime/`，与本地数据完全隔离。
"""


def clean(out: Path) -> None:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)


def copy_tree(source: Path, out: Path) -> int:
    count = 0
    for item in source.rglob("*"):
        if "__pycache__" in item.parts or item.suffix == ".pyc":
            continue
        target = out / item.relative_to(source)
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
            count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "deploy-cloud"))
    args = parser.parse_args()
    out = Path(args.out).resolve()
    clean(out)
    files = 0
    for name in COPY_DIRS:
        source = ROOT / name
        if source.is_dir():
            files += copy_tree(source, out / name)
    for name in COPY_FILES:
        source = ROOT / name
        if source.is_file():
            shutil.copy2(source, out / name)
            files += 1
    (out / "README_CLOUD.md").write_text(NOTE, encoding="utf-8")
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"部署目录: {out}")
    print(f"文件数: {files + 1} | 体积: {size / 1024 / 1024:.1f} MiB")


if __name__ == "__main__":
    main()
