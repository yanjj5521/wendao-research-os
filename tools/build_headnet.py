"""Build the bundled userspace Headscale/tsnet helper.

The helper source is tracked; the generated executable is not. CI uses
actions/setup-go. Local release builds can reuse ``.build-tools/go*`` or a Go
binary on PATH. On Windows, if Go is absent, the pinned official toolchain is
downloaded and SHA-256 verified before use.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import time
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "native" / "headnet"
OUTPUT = SOURCE_DIR / ("headnet-runtime.exe" if sys.platform == "win32" else "headnet-runtime")
GO_VERSION = "1.27.3"
GO_WINDOWS_SHA256 = "f26081f438319757f8a1b46d7d0b2c5ec7583d4038e485e0aa725cfde7885fff"
GO_WINDOWS_URL = f"https://go.dev/dl/go{GO_VERSION}.windows-amd64.zip"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _find_go() -> Path | None:
    explicit = os.environ.get("GO_BINARY", "").strip()
    if explicit and Path(explicit).is_file():
        return Path(explicit).resolve()
    local = ROOT / ".build-tools" / f"go{GO_VERSION}" / "bin" / (
        "go.exe" if sys.platform == "win32" else "go"
    )
    if local.is_file():
        return local
    found = shutil.which("go")
    return Path(found).resolve() if found else None


def _download_go() -> Path:
    if sys.platform != "win32":
        raise RuntimeError(f"Go {GO_VERSION} is required on PATH to build headnet.")
    tools_root = ROOT / ".build-tools"
    target_dir = tools_root / f"go{GO_VERSION}"
    go_exe = target_dir / "bin" / "go.exe"
    if go_exe.is_file():
        return go_exe
    tools_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="go-toolchain-", dir=tools_root) as temporary:
        temp_root = Path(temporary)
        archive = temp_root / "go.zip"
        print(f"Downloading Go {GO_VERSION}...")
        with urllib.request.urlopen(GO_WINDOWS_URL, timeout=600) as response, archive.open("wb") as out:
            shutil.copyfileobj(response, out)
        digest = _sha256(archive)
        if digest != GO_WINDOWS_SHA256:
            raise RuntimeError(f"Go checksum mismatch: {digest}")
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(temp_root)
        shutil.move(str(temp_root / "go"), str(target_dir))
    return go_exe


def build() -> Path:
    if not (SOURCE_DIR / "main.go").is_file():
        raise FileNotFoundError(SOURCE_DIR / "main.go")
    go = _find_go() or _download_go()
    env = os.environ.copy()
    env.update(
        {
            "GOTOOLCHAIN": "local",
            "CGO_ENABLED": "0",
            "GOFLAGS": "-mod=readonly",
        }
    )
    subprocess.run([str(go), "mod", "download"], cwd=SOURCE_DIR, env=env, check=True)
    temporary = OUTPUT.with_suffix(".building")
    try:
        subprocess.run(
            [
                str(go), "build", "-trimpath", "-buildvcs=false",
                "-ldflags=-s -w", "-o", str(temporary), ".",
            ],
            cwd=SOURCE_DIR,
            env=env,
            check=True,
        )
        if not temporary.is_file() or temporary.stat().st_size < 1_000_000:
            raise RuntimeError("headnet helper build produced no usable executable")
        built = OUTPUT
        last_error: OSError | None = None
        for _ in range(8):
            try:
                if OUTPUT.exists():
                    OUTPUT.unlink()
                temporary.replace(OUTPUT)
                last_error = None
                break
            except PermissionError as exc:
                last_error = exc
                time.sleep(0.5)
        if last_error is not None:
            # Windows can keep a previously executed helper image locked for a
            # while (AV/indexer). Keep the fixed name authoritative on clean CI,
            # but fall back to a uniquely named fresh build locally.
            built = SOURCE_DIR / (f"headnet-runtime-{int(time.time())}" + OUTPUT.suffix)
            if built.exists():
                built.unlink()
            temporary.replace(built)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Built headnet helper: {built} ({built.stat().st_size} bytes)")
    return built


if __name__ == "__main__":
    build()
