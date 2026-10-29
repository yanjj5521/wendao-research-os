from __future__ import annotations

from collections import deque
from pathlib import Path
from shutil import copyfile

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "packaging" / "generated"
ICO_PATH = OUTPUT_DIR / "research-system.ico"
PNG_PATH = OUTPUT_DIR / "research-system.png"
SOURCE_PATH = ROOT / "assets" / "app-icon-source.png"
WEB_PATH = ROOT / "static" / "app-icon.png"
HUB_WEB_PATH = ROOT / "hub_static" / "app-icon.png"
ANDROID_PATH = ROOT / "mobile" / "android" / "app" / "src" / "main" / "res" / "drawable-nodpi" / "ic_launcher.png"


def remove_corner_backdrop(image: Image.Image) -> Image.Image:
    """Make only corner-connected near-black pixels transparent."""
    image = image.convert("RGBA")
    pixels = image.load()
    width, height = image.size
    queue = deque([(0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)])
    visited: set[tuple[int, int]] = set()
    while queue:
        x, y = queue.popleft()
        if (x, y) in visited:
            continue
        visited.add((x, y))
        red, green, blue, alpha = pixels[x, y]
        if alpha == 0 or max(red, green, blue) <= 20:
            pixels[x, y] = (red, green, blue, 0)
            if x:
                queue.append((x - 1, y))
            if x + 1 < width:
                queue.append((x + 1, y))
            if y:
                queue.append((x, y - 1))
            if y + 1 < height:
                queue.append((x, y + 1))
    return image


def render(size: int, source: Image.Image) -> Image.Image:
    return source.resize((size, size), Image.Resampling.LANCZOS)


def main() -> None:
    if not SOURCE_PATH.exists():
        raise SystemExit(f"Missing icon source: {SOURCE_PATH}")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    source = remove_corner_backdrop(Image.open(SOURCE_PATH))
    base = render(512, source)
    base.save(PNG_PATH, optimize=True)
    WEB_PATH.parent.mkdir(parents=True, exist_ok=True)
    base.save(WEB_PATH, optimize=True)
    HUB_WEB_PATH.parent.mkdir(parents=True, exist_ok=True)
    copyfile(WEB_PATH, HUB_WEB_PATH)
    ANDROID_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Copy the fully encoded web bitmap. Re-encoding the same Pillow image
    # repeatedly can produce a truncated PNG with some Pillow builds.
    copyfile(WEB_PATH, ANDROID_PATH)
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    base.save(ICO_PATH, format="ICO", sizes=[(size, size) for size in sizes])
    print("Generated Windows, web, and Android icons from assets/app-icon-source.png")


if __name__ == "__main__":
    main()
