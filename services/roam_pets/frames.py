"""过程化渲染帧：把物种参数 + 时间 t 变成一帧几何图形（纯函数，可测、可截图）。

设计要点：
- 不使用位图：Canvas 矢量形状不会与透明键色冲突，也不需要贴图资源。
- 所有颜色来自物种 palette，且约定避开窗口透明键色 TRANSPARENT_KEY。
- 帧完全由 (物种, t, mood) 决定：待机呼吸、眨眼、尾巴摆动、心情低垂全部确定性可测。
"""
from __future__ import annotations

import math

from .models import Species

TRANSPARENT_KEY = "#010203"
SILHOUETTE_COLOR = "#223b3b"

TEMPER_SPEED = {"活泼": 1.3, "温顺": 1.0, "高傲": 0.85, "胆小": 1.15}


def _oval(cx: float, cy: float, rx: float, ry: float, fill: str, outline: str = "", width: int = 2, tags: str = "") -> dict:
    return {"kind": "oval", "x1": cx - rx, "y1": cy - ry, "x2": cx + rx, "y2": cy + ry,
            "fill": fill, "outline": outline or fill, "width": width, "tags": tags}


def _line(x1: float, y1: float, x2: float, y2: float, fill: str, width: int = 3) -> dict:
    return {"kind": "line", "x1": x1, "y1": y1, "x2": x2, "y2": y2, "fill": fill, "width": width, "tags": ""}


def _polygon(points: list[float], fill: str, outline: str = "", width: int = 2) -> dict:
    return {"kind": "polygon", "points": points, "fill": fill, "outline": outline or fill, "width": width, "tags": ""}


def build_frame(species: Species, t: float, mood: str = "平静") -> list[dict]:
    colors = species.colors
    shape = species.shape
    speed = TEMPER_SPEED.get(species.temper, 1.0) * (0.6 if mood in ("饿了", "累了") else 1.0)
    droop = 5 if mood in ("饿了", "累了") else 0
    bob = math.sin(t * math.tau / (2.6 / speed)) * 5 * speed
    hop = 0.0
    if (t % 6.0) < 0.34 and mood == "开心":
        hop = -12 * math.sin(math.pi * ((t % 6.0) / 0.34))
    cx = 110.0
    body_cy = 152.0 + bob + hop + droop
    ratio = float(shape.get("body_ratio", 0.8))
    rx, ry = 44 * ratio, 38 * ratio
    wag = math.sin(t * 4.2) * 6

    shapes: list[dict] = []

    # 尾巴/翅膀（最底层）
    tail = str(shape.get("tail", "none"))
    if tail == "flame":
        fx = cx + rx + 8 + wag * 0.4
        shapes.append(_polygon([cx + rx - 6, body_cy + 4, fx, body_cy - 6, fx + 6, body_cy + 6], colors["accent"]))
        shapes.append(_oval(fx + wag * 0.3, body_cy - 12 + math.sin(t * 6) * 2, 7, 9, colors["accent"]))
    elif tail == "bolt":
        shapes.append(_polygon([cx + rx - 4, body_cy - 2, cx + rx + 18 + wag, body_cy - 10,
                                cx + rx + 10 + wag, body_cy + 2, cx + rx + 22 + wag, body_cy + 4], colors["accent"]))
    elif tail == "long":
        shapes.append(_line(cx + rx - 4, body_cy + 10, cx + rx + 26 + wag, body_cy - 14, colors["body"], 7))
        shapes.append(_oval(cx + rx + 26 + wag, body_cy - 15, 5, 5, colors["accent"]))
    elif tail == "puff":
        shapes.append(_oval(cx + rx + 2, body_cy + 12, 8, 8, colors["belly"]))
    elif tail == "stub":
        shapes.append(_oval(cx + rx, body_cy + 10, 7, 6, colors["body"]))
    if shape.get("wings"):
        flap = math.sin(t * 5) * 6
        for side in (-1, 1):
            shapes.append(_polygon([cx + side * rx * 0.5, body_cy - 8,
                                    cx + side * (rx + 22), body_cy - 24 + flap,
                                    cx + side * rx * 0.8, body_cy + 6], colors["accent"]))

    # 脚
    shapes.append(_oval(cx - rx * 0.45, body_cy + ry - 4, 10, 6, colors["extra"]))
    shapes.append(_oval(cx + rx * 0.45, body_cy + ry - 4, 10, 6, colors["extra"]))

    # 身体与肚皮
    shapes.append(_oval(cx, body_cy, rx, ry, colors["body"], outline=colors["extra"], width=2))
    shapes.append(_oval(cx, body_cy + ry * 0.35, rx * 0.62, ry * 0.5, colors["belly"], outline="", width=0))

    # 花纹
    pattern = str(shape.get("pattern", "none"))
    if pattern == "spots":
        for dx, dy, r in ((-0.45, -0.35, 5), (0.4, -0.5, 4), (0.15, 0.05, 4)):
            shapes.append(_oval(cx + dx * rx, body_cy + dy * ry, r, r, colors["accent"]))
    elif pattern == "stripes":
        for i, dy in enumerate((-0.45, -0.1, 0.25)):
            w = rx * (0.55 - i * 0.08)
            shapes.append(_line(cx - w, body_cy + dy * ry, cx + w, body_cy + (dy - 0.08) * ry, colors["accent"], 5))

    # 耳朵/头冠
    ear = str(shape.get("ear", "none"))
    head_y = body_cy - ry * 0.72
    if ear == "pointy":
        for side in (-1, 1):
            tip_x = cx + side * rx * 0.55 + (side * wag * 0.3)
            shapes.append(_polygon([cx + side * rx * 0.2, head_y + 4, tip_x, head_y - 22, cx + side * rx * 0.6, head_y - 2],
                                   colors["body"], colors["extra"], 2))
    elif ear == "long":
        for side in (-1, 1):
            tip_x = cx + side * rx * 0.5 + (side * wag * 0.25)
            shapes.append(_polygon([cx + side * rx * 0.25, head_y + 6, tip_x + side * 6, head_y - 34,
                                    cx + side * rx * 0.65, head_y - 4], colors["body"], colors["extra"], 2))
            shapes.append(_polygon([cx + side * rx * 0.35, head_y + 2, tip_x + side * 5, head_y - 26,
                                    cx + side * rx * 0.55, head_y - 2], colors["belly"], "", 0))
    elif ear == "crest":
        shapes.append(_polygon([cx - 6, head_y + 2, cx + math.sin(t * 3) * 4, head_y - 24, cx + 10, head_y - 2],
                               colors["accent"], colors["extra"], 2))

    # 眼睛：眨眼 / 低落时眯眼
    eye_y = body_cy - ry * 0.28
    blink = (t % 3.7) < 0.14
    sad = mood in ("饿了", "累了")
    for side in (-1, 1):
        ex = cx + side * rx * 0.34
        if blink or sad:
            tilt = 2.4 if sad else 0
            shapes.append(_line(ex - 5, eye_y - tilt, ex + 5, eye_y + tilt, colors["extra"], 3))
        else:
            shapes.append(_oval(ex, eye_y, 5.4, 6.4, colors["extra"]))
            shapes.append(_oval(ex + 1.8, eye_y - 2.2, 1.7, 2.0, "#ffffff"))

    # 腮红与嘴
    for side in (-1, 1):
        shapes.append(_oval(cx + side * rx * 0.55, eye_y + 9, 4.4, 3, colors["accent"]))
    mouth_y = eye_y + 15
    shapes.append(_line(cx - 4, mouth_y, cx + 4, mouth_y + (2 if mood == "开心" else 0), colors["extra"], 2))

    # 头顶元素微光（小字符号，不是光晕）
    shapes.append({"kind": "text", "x": cx, "y": head_y - 30, "text": species.element,
                   "fill": colors["accent"], "tags": ""})
    return shapes


def silhouette(species: Species) -> list[dict]:
    """图鉴未解锁剪影：全部形状换成统一的深色。"""
    shapes = build_frame(species, 0.0, "平静")
    for item in shapes:
        if item["kind"] == "text":
            item["text"] = "?"
            item["fill"] = SILHOUETTE_COLOR
            continue
        item["fill"] = SILHOUETTE_COLOR
        item["outline"] = SILHOUETTE_COLOR
    return shapes
