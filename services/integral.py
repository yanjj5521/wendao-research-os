"""Numerical integration for curve files.

The packaged app must stay dependency-free, so parsing and integration are pure
Python.  Supported inputs are whitespace/comma separated text (two columns, or a
single column with an implicit index) and raw little/big-endian binary arrays.
"""
from __future__ import annotations

import math
import re
import struct
from dataclasses import dataclass, field

MAX_TEXT_BYTES = 8 * 1024 * 1024
MAX_BINARY_BYTES = 32 * 1024 * 1024
MAX_POINTS = 300_000

TEXT_SUFFIXES = {".txt", ".csv", ".tsv", ".dat", ".xy", ".asc"}
BINARY_SUFFIXES = {".bin"}

DTYPE_CHOICES = {
    "float32": ("f", 4),
    "float64": ("d", 8),
    "int16": ("h", 2),
    "int32": ("i", 4),
    "uint16": ("H", 2),
    "uint32": ("I", 4),
}

_NUMBER = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")


class IntegralError(ValueError):
    """Raised when a file cannot be interpreted as a curve."""


@dataclass
class Dataset:
    x: list[float]
    y: list[float]
    label: str
    kind: str
    notes: list[str] = field(default_factory=list)


@dataclass
class IntegralResult:
    method: str
    method_label: str
    area: float
    positive: float
    negative: float
    absolute: float
    x_min: float
    x_max: float
    count: int
    step: float | None
    mean_y: float
    centroid_x: float | None
    rms: float
    formula: str
    derivation: list[str]
    simpson_area: float | None
    simpson_note: str
    note: str


def _decode_text(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise IntegralError("无法识别文件编码，请另存为 UTF-8 后重试。")


def _numeric(token: str) -> bool:
    return bool(_NUMBER.match(token.strip()))


def parse_text(raw: bytes, label: str = "") -> Dataset:
    if len(raw) > MAX_TEXT_BYTES:
        raise IntegralError(
            f"文本文件超过 {MAX_TEXT_BYTES // (1024 * 1024)} MB，请先抽样或拆分。"
        )
    text = _decode_text(raw)
    lines = [line.strip() for line in text.splitlines()]
    sample = "\n".join(line for line in lines[:200] if line)
    delimiter = ""
    if sample:
        counts = {",": sample.count(","), ";": sample.count(";"), "\t": sample.count("\t")}
        delimiter = max(counts, key=counts.get) if max(counts.values()) > 0 else ""

    pairs: list[tuple[float, float]] = []
    singles: list[float] = []
    skipped = 0
    for line in lines:
        if not line or line.startswith(("#", "//", "%", ";")):
            continue
        parts = line.split(delimiter) if delimiter else line.split()
        numbers = [part.strip() for part in parts if _numeric(part)]
        if len(numbers) >= 2:
            pairs.append((float(numbers[0]), float(numbers[1])))
        elif len(numbers) == 1:
            singles.append(float(numbers[0]))
        else:
            skipped += 1

    notes: list[str] = []
    if pairs:
        x = [pair[0] for pair in pairs]
        y = [pair[1] for pair in pairs]
        if singles:
            notes.append(f"忽略了 {len(singles)} 行单列数值，按两列数据解析。")
    elif singles:
        x = [float(index) for index in range(len(singles))]
        y = singles
        notes.append("文件只有一列数值，已按行号作为 x（步长 1）处理。")
    else:
        raise IntegralError(
            "没有找到可用的数值行。文本需为两列（x y）或用逗号/制表符分隔。"
        )
    if skipped:
        notes.append(f"跳过了 {skipped} 行非数值内容（表头或说明）。")
    return _prepare(x, y, label or "文本数据", "文本", notes)


def parse_binary(
    raw: bytes,
    label: str = "",
    dtype: str = "float64",
    endian: str = "little",
    layout: str = "xy",
) -> Dataset:
    if len(raw) > MAX_BINARY_BYTES:
        raise IntegralError(
            f"二进制文件超过 {MAX_BINARY_BYTES // (1024 * 1024)} MB，请先抽样或拆分。"
        )
    if dtype not in DTYPE_CHOICES:
        raise IntegralError(f"不支持的数值类型：{dtype}")
    if endian not in {"little", "big"}:
        raise IntegralError("字节序只能是 little 或 big。")
    if layout not in {"xy", "y"}:
        raise IntegralError("二进制布局只能是 xy（成对）或 y（单列）。")
    code, size = DTYPE_CHOICES[dtype]
    if not raw or len(raw) % size:
        raise IntegralError(
            f"文件长度 {len(raw)} 字节不是 {dtype}（{size} 字节）的整数倍。"
        )
    prefix = "<" if endian == "little" else ">"
    values = [item[0] for item in struct.iter_unpack(prefix + code, raw)]
    if layout == "xy":
        if len(values) % 2:
            raise IntegralError("按 xy 成对读取时数值个数为奇数，请确认布局或类型。")
        x = [float(value) for value in values[0::2]]
        y = [float(value) for value in values[1::2]]
        notes = [f"按 {endian}-endian {dtype} 成对读取 {len(y)} 个点。"]
    else:
        y = [float(value) for value in values]
        x = [float(index) for index in range(len(y))]
        notes = [
            f"按 {endian}-endian {dtype} 单列读取 {len(y)} 个 y，x 取行号（步长 1）。"
        ]
    return _prepare(x, y, label or "二进制数据", "二进制", notes)


def _prepare(
    x: list[float], y: list[float], label: str, kind: str, notes: list[str]
) -> Dataset:
    pairs = [
        (float(a), float(b))
        for a, b in zip(x, y)
        if math.isfinite(float(a)) and math.isfinite(float(b))
    ]
    dropped = len(x) - len(pairs)
    if dropped:
        notes.append(f"移除了 {dropped} 个非有限值（NaN/Inf）。")
    if len(pairs) < 2:
        raise IntegralError("有效数据点少于 2 个，无法计算面积。")
    ordered = sorted(pairs, key=lambda pair: pair[0])
    if ordered != pairs:
        notes.append("原始 x 非升序，已按 x 重新排序。")
    cleaned: list[tuple[float, float]] = []
    duplicates = 0
    for point in ordered:
        if cleaned and abs(point[0] - cleaned[-1][0]) <= 1e-12 * max(1.0, abs(point[0])):
            cleaned[-1] = point
            duplicates += 1
        else:
            cleaned.append(point)
    if duplicates:
        notes.append(f"合并了 {duplicates} 个重复 x（保留后出现的值）。")
    if len(cleaned) > MAX_POINTS:
        raise IntegralError(f"数据点超过 {MAX_POINTS} 个，请先降采样。")
    if len(cleaned) < 2:
        raise IntegralError("去重后有效数据点少于 2 个，无法计算面积。")
    return Dataset(
        x=[point[0] for point in cleaned],
        y=[point[1] for point in cleaned],
        label=label,
        kind=kind,
        notes=notes,
    )


def _trapezoid(x: list[float], y: list[float]) -> float:
    total = 0.0
    for index in range(len(x) - 1):
        total += (x[index + 1] - x[index]) * (y[index] + y[index + 1]) / 2.0
    return total


def _uniform_step(x: list[float]) -> float | None:
    if len(x) < 2:
        return None
    step = (x[-1] - x[0]) / (len(x) - 1)
    if step <= 0:
        return None
    tolerance = 1e-9 * max(1.0, abs(step))
    for index in range(len(x) - 1):
        if abs((x[index + 1] - x[index]) - step) > tolerance:
            return None
    return step


def _simpson(x: list[float], y: list[float]) -> tuple[float | None, str]:
    step = _uniform_step(x)
    if step is None:
        return None, "x 间距不均匀，Simpson 公式不适用，已使用梯形法。"
    intervals = len(x) - 1
    if intervals < 2 or intervals % 2:
        return None, "Simpson 1/3 公式需要偶数个区间（奇数个点），已使用梯形法。"
    total = y[0] + y[-1]
    for index in range(1, intervals):
        total += (4.0 if index % 2 else 2.0) * y[index]
    return total * step / 3.0, ""


def _split_area(x: list[float], y: list[float]) -> tuple[float, float]:
    positive = negative = 0.0
    for index in range(len(x) - 1):
        x0, x1 = x[index], x[index + 1]
        y0, y1 = y[index], y[index + 1]
        dx = x1 - x0
        if y0 >= 0 and y1 >= 0:
            positive += dx * (y0 + y1) / 2.0
        elif y0 <= 0 and y1 <= 0:
            negative += dx * (y0 + y1) / 2.0
        else:
            cross = x0 + dx * (y0 / (y0 - y1))
            left = 0.5 * (cross - x0) * y0
            right = 0.5 * (x1 - cross) * y1
            positive += max(left, 0.0) + max(right, 0.0)
            negative += min(left, 0.0) + min(right, 0.0)
    return positive, negative


def integrate(dataset: Dataset, method: str = "auto") -> IntegralResult:
    x, y = dataset.x, dataset.y
    if method not in {"auto", "trapezoid", "simpson"}:
        raise IntegralError("未知的积分方法。")
    trapezoid = _trapezoid(x, y)
    simpson_area, simpson_note = _simpson(x, y)
    chosen = method
    if method == "auto":
        chosen = "simpson" if simpson_area is not None else "trapezoid"
    if chosen == "simpson" and simpson_area is None:
        raise IntegralError(simpson_note or "当前数据不满足 Simpson 公式条件。")

    positive, negative = _split_area(x, y)
    step = _uniform_step(x)
    span = x[-1] - x[0]

    if chosen == "simpson":
        intervals = len(x) - 1
        odd_sum = sum(y[1:-1:2])
        even_sum = sum(y[2:-1:2])
        formula = (
            "Simpson 1/3：A = (h/3) · [y₀ + 4(y₁+y₃+…) + 2(y₂+y₄+…) + yₙ]，"
            "h = (b − a)/n"
        )
        derivation = [
            f"1. 读入 {len(x)} 个点，x ∈ [{x[0]:.6g}, {x[-1]:.6g}]，共 {intervals} 个区间。",
            f"2. 检查等距：h = {step:.6g}；区间数为偶数，满足 Simpson 1/3 条件。",
            "3. 在每个双区间上用二次抛物线代替曲线，误差项为 −(b−a)h⁴f⁽⁴⁾(ξ)/180。",
            f"4. 奇数项和 Σy_odd = {odd_sum:.8g}，偶数项和 Σy_even = {even_sum:.8g}。",
            f"5. 代入：A = {step:.6g}/3 × ({y[0]:.8g} + 4×{odd_sum:.8g} + 2×{even_sum:.8g} + {y[-1]:.8g}) = {simpson_area:.8g}。",
            "6. 正负面积另按曲线与 y=0 的交点拆分，见下方分项。",
        ]
        area = simpson_area or 0.0
        label = "Simpson 1/3（抛物线）"
    else:
        formula = "复合梯形：A = Σᵢ (xᵢ₊₁ − xᵢ) · (yᵢ + yᵢ₊₁) / 2"
        derivation = [
            f"1. 读入 {len(x)} 个点，x ∈ [{x[0]:.6g}, {x[-1]:.6g}]，共 {len(x) - 1} 个区间。",
            "2. 每个区间用直线连接两端，面积取上下底平均乘以宽（梯形）。",
            f"3. 首段：({x[1]:.6g} − {x[0]:.6g}) × ({y[0]:.8g} + {y[1]:.8g})/2 = "
            f"{(x[1] - x[0]) * (y[0] + y[1]) / 2:.8g}。",
            f"4. 末段：({x[-1]:.6g} − {x[-2]:.6g}) × ({y[-2]:.8g} + {y[-1]:.8g})/2 = "
            f"{(x[-1] - x[-2]) * (y[-2] + y[-1]) / 2:.8g}。",
            f"5. 全部区间求和：A = {trapezoid:.8g}。",
            "6. 正负面积另按曲线与 y=0 的交点拆分，见下方分项。",
        ]
        area = trapezoid
        label = "复合梯形法"

    mean_y = area / span if span else sum(y) / len(y)

    def moment(values: list[float]) -> float:
        if chosen == "simpson":
            value, _ = _simpson(x, values)
            return value or 0.0
        return _trapezoid(x, values)

    centroid_x = (
        moment([a * b for a, b in zip(x, y)]) / area if abs(area) > 1e-12 else None
    )
    rms = (
        math.sqrt(max(moment([value * value for value in y]), 0.0) / span)
        if span
        else 0.0
    )

    if method == "auto" and simpson_area is not None:
        note = "自动选择 Simpson 1/3：数据等距且区间数为偶数，精度高于梯形法。"
    elif method == "auto":
        note = simpson_note or "自动退回梯形法。"
    else:
        note = "按指定方法计算。"
    if simpson_area is not None and chosen == "trapezoid":
        note += f" 同一数据用 Simpson 得 {simpson_area:.8g}，可用于估计离散误差。"

    return IntegralResult(
        method=chosen,
        method_label=label,
        area=area,
        positive=positive,
        negative=negative,
        absolute=positive - negative,
        x_min=x[0],
        x_max=x[-1],
        count=len(x),
        step=step,
        mean_y=mean_y,
        centroid_x=centroid_x,
        rms=rms,
        formula=formula,
        derivation=derivation,
        simpson_area=simpson_area,
        simpson_note=simpson_note,
        note=note,
    )


def render_svg(dataset: Dataset, width: int = 680, height: int = 250) -> str:
    """A dependency-free preview: curve, zero line and shaded signed area."""
    x, y = dataset.x, dataset.y
    if len(x) < 2:
        return ""
    stride = max(1, len(x) // 1500)
    points = list(zip(x, y))[::stride]
    if points[-1] != (x[-1], y[-1]):
        points.append((x[-1], y[-1]))
    left, right, top, bottom = 46, 16, 14, 30
    plot_width = max(width - left - right, 1)
    plot_height = max(height - top - bottom, 1)
    x_min, x_max = x[0], x[-1]
    y_min = min(0.0, min(y))
    y_max = max(0.0, max(y))
    if x_max == x_min:
        x_max = x_min + 1.0
    if y_max == y_min:
        y_max = y_min + 1.0

    def px(value: float) -> float:
        return left + (value - x_min) / (x_max - x_min) * plot_width

    def py(value: float) -> float:
        return top + (y_max - value) / (y_max - y_min) * plot_height

    zero = py(0.0)
    polyline = " ".join(f"{px(px_value):.1f},{py(py_value):.1f}" for px_value, py_value in points)
    area_path = (
        f"M {px(points[0][0]):.1f},{zero:.1f} "
        + " ".join(f"L {px(px_value):.1f},{py(py_value):.1f}" for px_value, py_value in points)
        + f" L {px(points[-1][0]):.1f},{zero:.1f} Z"
    )
    return f"""<svg class="integral-plot" viewBox="0 0 {width} {height}" role="img" aria-label="曲线与积分面积示意">
  <rect x="{left}" y="{top}" width="{plot_width}" height="{plot_height}" fill="#fffaf3" stroke="#e5d4c2"/>
  <line x1="{left}" y1="{zero:.1f}" x2="{left + plot_width}" y2="{zero:.1f}" stroke="#8b7668" stroke-dasharray="4 4"/>
  <path d="{area_path}" fill="rgba(183,103,72,0.18)" stroke="none"/>
  <polyline points="{polyline}" fill="none" stroke="#b76748" stroke-width="2"/>
  <text x="{left}" y="{height - 8}" font-size="10" fill="#8b7668">{x_min:.6g}</text>
  <text x="{left + plot_width}" y="{height - 8}" font-size="10" fill="#8b7668" text-anchor="end">{x_max:.6g}</text>
  <text x="4" y="{top + 10}" font-size="10" fill="#8b7668">{y_max:.4g}</text>
  <text x="4" y="{top + plot_height}" font-size="10" fill="#8b7668">{y_min:.4g}</text>
</svg>"""
