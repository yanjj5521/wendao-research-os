"""PDF 辅助抽取：只抽标题、图表标题、结论，供手动填空。

刻意不做摘要、不做翻译、不判断论文好坏——那属于第二遍阅读，应该由人完成。
无 PDF 文本层（扫描件）时明确告知，不尝试 OCR。
"""

from __future__ import annotations

import io
import re
from typing import Any

from pypdf import PdfReader

MAX_BYTES = 25 * 1024 * 1024
MAX_PAGES = 40
CAPTION_PATTERN = re.compile(r"^(fig(?:ure)?\.?|table|scheme)\s*\d+", re.IGNORECASE)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def sketch(data: bytes) -> dict[str, Any]:
    if not data:
        raise ValueError("文件是空的。")
    if len(data) > MAX_BYTES:
        raise ValueError("PDF 超过 25 MB，请先拆分或压缩。")
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:  # pypdf 对损坏文件抛多种异常
        raise ValueError("无法解析这个 PDF，可能已损坏或加密。") from exc
    if reader.is_encrypted:
        raise ValueError("PDF 已加密，请先解除密码限制。")

    pages = reader.pages[:MAX_PAGES]
    first_text = ""
    captions: list[str] = []
    for index, page in enumerate(pages):
        try:
            text = page.extract_text() or ""
        except Exception:
            continue
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if index == 0 and lines and not first_text:
            first_text = lines[0]
        for line in lines:
            if CAPTION_PATTERN.match(line) and len(line) <= 240:
                if line not in captions:
                    captions.append(line)

    tail_text = ""
    for page in pages[-2:]:
        try:
            tail_text += "\n" + (page.extract_text() or "")
        except Exception:
            continue
    paragraphs = [_clean(block) for block in re.split(r"\n\s*\n", tail_text) if _clean(block)]
    conclusion = max(paragraphs[-3:], key=len) if paragraphs else ""

    if not first_text and not captions and not conclusion:
        raise ValueError("这份 PDF 没有可抽取的文本层（可能是扫描件），需要手动填写。")

    return {
        "title": _clean(first_text)[:200],
        "captions": captions[:12],
        "conclusion": conclusion[:900],
        "pages": len(reader.pages),
    }
