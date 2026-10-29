"""从模板中提取界面核心文案，生成 docs/COPY_GUIDE.md 的文案速查部分。

用法：python tools/gen_copy_guide.py [--out docs/COPY_GUIDE.md]
只提取用户可见文本：页标题、标题层级、主按钮、表单标签、页面导语。
Jinja 表达式原样保留，便于与源码对照。
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"

H_TAG = re.compile(r"<(h[1-3])\b[^>]*>(.*?)</\1>", re.S)
BUTTON = re.compile(r"<button\b[^>]*>(.*?)</button>", re.S)
LEAD = re.compile(r"</h1>\s*<p>(.*?)</p>", re.S)
LABEL = re.compile(r"<label(?:\s[^>]*)?><span>(.*?)</span>", re.S)
TAG = re.compile(r"<[^>]+>")
BLOCK_TITLE = re.compile(r"{% block title %}(.*?){% endblock %}", re.S)


def clean(fragment: str) -> str:
    text = TAG.sub("", fragment)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def is_copyworthy(text: str) -> bool:
    if not text:
        return False
    if len(text) <= 1:
        return False
    lowered = text.lower()
    return not lowered.startswith(("http", "{{ url_for"))


def extract(path: Path) -> dict[str, list[str]]:
    source = path.read_text(encoding="utf-8")
    out: dict[str, list[str]] = {}

    title = BLOCK_TITLE.search(source)
    if title:
        out["页面标题"] = [clean(title.group(1))]

    heads: dict[str, list[str]] = {}
    for match in H_TAG.finditer(source):
        text = clean(match.group(2))
        if is_copyworthy(text):
            heads.setdefault(match.group(1), []).append(text)
    out.update({f"标题 {tag}": values for tag, values in heads.items()})

    leads = [clean(m.group(1)) for m in LEAD.finditer(source)]
    leads = [item for item in leads if is_copyworthy(item)]
    if leads:
        out["页面导语"] = leads

    labels = []
    for match in LABEL.finditer(source):
        text = clean(match.group(1))
        if is_copyworthy(text) and text not in labels:
            labels.append(text)
    if labels:
        out["表单标签"] = labels

    buttons = []
    for match in BUTTON.finditer(source):
        text = clean(match.group(1))
        if is_copyworthy(text) and text not in buttons:
            buttons.append(text)
    if buttons:
        out["按钮"] = buttons

    return out


def render(out_path: Path) -> None:
    lines = ["## 四、每页核心文案速查", ""]
    lines.append("> 由 `tools/gen_copy_guide.py` 自动提取；改动界面文案后重跑本工具即可刷新本节。")
    lines.append("")
    for path in sorted(TEMPLATES.glob("*.html")):
        sections = extract(path)
        if not sections:
            continue
        lines.append(f"### {path.name}")
        lines.append("")
        for section, values in sections.items():
            lines.append(f"- **{section}**：" + "；".join(values))
        lines.append("")
    current = out_path.read_text(encoding="utf-8")
    marker = "## 四、每页核心文案速查"
    if marker in current:
        current = current[: current.index(marker)].rstrip() + "\n"
    else:
        current = current.rstrip() + "\n"
    out_path.write_text(current + "\n".join(lines), encoding="utf-8")
    print(f"已写入 {out_path}（覆盖 {len(lines)} 行速查）")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "docs" / "COPY_GUIDE.md"))
    args = parser.parse_args()
    render(Path(args.out))


if __name__ == "__main__":
    main()
