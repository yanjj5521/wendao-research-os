from __future__ import annotations

import re
import statistics
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any


SECTION_PATTERNS = {
    "abstract": [r"\babstract\b", r"摘要"],
    "introduction": [r"\bintroduction\b", r"引言", r"绪论"],
    "methods": [r"\bmaterials? and methods?\b", r"\bmethods?\b", r"实验方法", r"材料与方法"],
    "results": [r"\bresults?(?: and discussion)?\b", r"结果与讨论", r"结果"],
    "conclusion": [r"\bconclusions?\b", r"结论"],
}

MAX_BUNDLE_BYTES = 2 * 1024 * 1024 * 1024
MAX_BUNDLE_FILES = 5000

SCIENCE_KEYWORDS = [
    "supercapacitor", "cement", "expanded graphite", "electrochemical", "capacitance",
    "electrode", "electrolyte", "conductivity", "impedance", "CV", "GCD", "EIS",
    "超级电容器", "水泥", "膨胀石墨", "电化学", "电容", "电极", "电解液", "导电", "阻抗",
    "分子动力学", "机器学习", "LAMMPS", "C-S-H", "孔结构", "离子输运", "电子输运",
]


def _clean_lines(text: str) -> list[str]:
    lines = []
    for raw in text.replace("\r", "\n").split("\n"):
        line = re.sub(r"\s+", " ", raw).strip()
        if line and len(line) > 2:
            lines.append(line)
    return lines


def _sentences(text: str) -> list[str]:
    chunks = re.split(r"(?<=[。！？.!?])\s+|\n+", text)
    return [re.sub(r"\s+", " ", x).strip() for x in chunks if 25 <= len(x.strip()) <= 500]


def _extract_section(lines: list[str], names: list[str], max_chars: int = 1800) -> str:
    start = None
    for i, line in enumerate(lines):
        lower = line.lower()
        if any(re.search(pattern, lower, re.I) for pattern in names):
            start = i + 1
            break
    if start is None:
        return ""
    selected: list[str] = []
    total = 0
    for line in lines[start:]:
        if total > 80 and any(
            re.search(pat, line.lower(), re.I)
            for patterns in SECTION_PATTERNS.values()
            for pat in patterns
        ):
            break
        selected.append(line)
        total += len(line)
        if total >= max_chars:
            break
    return "\n".join(selected)[:max_chars]


def offline_paper_summary(title: str, text: str) -> dict[str, Any]:
    """Generate a deterministic local research card without external AI."""
    text = text or ""
    lines = _clean_lines(text)
    abstract = _extract_section(lines, SECTION_PATTERNS["abstract"], 2200)
    conclusion = _extract_section(lines, SECTION_PATTERNS["conclusion"], 1800)
    source_for_scoring = abstract or "\n".join(lines[:80])
    sentences = _sentences(source_for_scoring)

    keyword_counts: dict[str, int] = {}
    lower_text = text.lower()
    for keyword in SCIENCE_KEYWORDS:
        count = lower_text.count(keyword.lower())
        if count:
            keyword_counts[keyword] = count
    top_keywords = [k for k, _ in sorted(keyword_counts.items(), key=lambda item: (-item[1], item[0]))[:12]]

    def score(sentence: str) -> float:
        lowered = sentence.lower()
        keyword_score = sum(2.5 for k in top_keywords if k.lower() in lowered)
        purpose_score = 4 if re.search(r"we (?:report|propose|develop|demonstrate|investigate)|本文|本研究|旨在|提出", sentence, re.I) else 0
        result_score = 3 if re.search(r"increase|decrease|improv|enhanc|result|show|achiev|提高|降低|结果|表明|实现", sentence, re.I) else 0
        length_penalty = abs(len(sentence) - 140) / 120
        return keyword_score + purpose_score + result_score - length_penalty

    ranked = sorted(sentences, key=score, reverse=True)
    key_points: list[str] = []
    for sentence in ranked:
        if not any(sentence[:45] in existing or existing[:45] in sentence for existing in key_points):
            key_points.append(sentence)
        if len(key_points) >= 5:
            break

    question = ""
    for sentence in sentences:
        if re.search(r"challenge|problem|however|remain|unclear|limited|瓶颈|问题|然而|尚不清楚|不足", sentence, re.I):
            question = sentence
            break
    if not question and key_points:
        question = key_points[0]

    method = ""
    methods_text = _extract_section(lines, SECTION_PATTERNS["methods"], 1200)
    method_sentences = _sentences(methods_text)
    if method_sentences:
        method = method_sentences[0]

    conclusion_sentences = _sentences(conclusion)
    takeaway = conclusion_sentences[0] if conclusion_sentences else (key_points[1] if len(key_points) > 1 else "")

    related = []
    for keyword in top_keywords:
        if keyword.lower() in {"cement", "水泥", "expanded graphite", "膨胀石墨", "supercapacitor", "超级电容器", "electrochemical", "电化学", "分子动力学", "机器学习", "lammps"}:
            related.append(keyword)
    relevance = "、".join(related[:6]) or "请结合你的研究主线手动补充关联。"

    card = {
        "title": title,
        "mode": "纯本地规则摘要",
        "research_question": question[:600],
        "method": method[:600],
        "key_points": key_points,
        "takeaway": takeaway[:600],
        "keywords": top_keywords,
        "relevance": relevance,
        "limitations_prompt": "检查作者是否区分电子输运与离子输运，是否报告器件级指标、重复性、力学性能及长期稳定性。",
        "next_actions": [
            "标出最关键的 Figure，并写一句它证明了什么。",
            "把作者的变量、响应指标和控制变量录入实验或数据集模块。",
            "写下一个可证伪的后续假设，而不是只记录‘可以进一步研究’。",
        ],
    }
    return card


def summary_to_markdown(card: dict[str, Any]) -> str:
    points = "\n".join(f"- {x}" for x in card.get("key_points", [])) or "- 暂未提取到稳定结果句。"
    keywords = "、".join(card.get("keywords", [])) or "待补充"
    actions = "\n".join(f"- [ ] {x}" for x in card.get("next_actions", []))
    return f"""# 本地论文研读卡：{card.get('title', '')}

> 生成方式：{card.get('mode', '纯本地规则摘要')}。本卡用于快速定位，不替代核对原文。

## 作者真正想解决的问题
{card.get('research_question') or '待手动补充'}

## 主要方法
{card.get('method') or '待手动补充'}

## 关键发现
{points}

## 一句话结论
{card.get('takeaway') or '待手动补充'}

## 高频关键词
{keywords}

## 与当前研究体系的关联
{card.get('relevance') or '待手动补充'}

## 阅读时重点质疑
{card.get('limitations_prompt') or ''}

## 下一步动作
{actions}
"""


def parse_lammps_log(path: Path) -> dict[str, Any]:
    """Parse the last thermo block from a LAMMPS log without heavy dependencies."""
    text = path.read_text(encoding="utf-8", errors="ignore")
    version_match = re.search(r"LAMMPS \(([^\)]+)\)", text)
    atom_matches = re.findall(r"\b(\d+) atoms\b", text)
    loop_matches = re.findall(r"Loop time of .*? for (\d+) steps", text)
    completion = bool(loop_matches) or "Total wall time" in text

    lines = text.splitlines()
    blocks: list[tuple[list[str], list[list[float]]]] = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if re.match(r"^Step(?:\s+\w+)+$", stripped):
            headers = stripped.split()
            rows: list[list[float]] = []
            i += 1
            while i < len(lines):
                line = lines[i].strip()
                if not line or line.startswith(("Loop time", "ERROR", "WARNING")):
                    break
                parts = line.split()
                if len(parts) != len(headers):
                    break
                try:
                    rows.append([float(x) for x in parts])
                except ValueError:
                    break
                i += 1
            if rows:
                blocks.append((headers, rows))
        i += 1

    last: dict[str, float] = {}
    columns: list[str] = []
    points: list[dict[str, float]] = []
    if blocks:
        headers, rows = blocks[-1]
        columns = headers
        last = dict(zip(headers, rows[-1]))
        sample_rows = rows[-200:]
        points = [dict(zip(headers, row)) for row in sample_rows]

    temp_values = [row.get("Temp") for row in points if row.get("Temp") is not None]
    summary = {
        "status": "PASS" if completion else ("ERROR" if "ERROR" in text else "INCOMPLETE"),
        "lammps_version": version_match.group(1) if version_match else "",
        "atoms": int(atom_matches[-1]) if atom_matches else None,
        "steps": int(loop_matches[-1]) if loop_matches else int(last.get("Step", 0) or 0),
        "last_step": int(last.get("Step", 0) or 0) if last else None,
        "last_temp": last.get("Temp"),
        "last_etotal": last.get("TotEng", last.get("E_total", last.get("Etot"))),
        "thermo_columns": columns,
        "thermo_points": points,
        "mean_temp": statistics.fmean(temp_values) if temp_values else None,
        "warnings": len(re.findall(r"\bWARNING\b", text)),
        "errors": len(re.findall(r"\bERROR\b", text)),
    }
    return summary


def unpack_lammps_bundle(
    zip_path: Path,
    destination: Path,
    *,
    max_bytes: int = MAX_BUNDLE_BYTES,
    max_files: int = MAX_BUNDLE_FILES,
) -> list[Path]:
    destination.mkdir(parents=True, exist_ok=True)
    destination = destination.resolve()
    extracted: list[Path] = []
    total_bytes = 0
    with zipfile.ZipFile(zip_path) as archive:
        members = archive.infolist()
        if len(members) > max_files:
            raise ValueError(f"压缩包文件数超过 {max_files} 个限制")
        for member in members:
            if member.is_dir():
                continue
            safe = PurePosixPath(member.filename.replace("\\", "/"))
            if (
                safe.is_absolute()
                or not safe.parts
                or ".." in safe.parts
                or (safe.parts and ":" in safe.parts[0])
            ):
                continue
            target = destination.joinpath(*safe.parts).resolve()
            if destination not in target.parents:
                continue
            if member.file_size < 0 or total_bytes + member.file_size > max_bytes:
                raise ValueError(f"压缩包解压后超过 {max_bytes / 1024 / 1024:.0f} MB 限制")
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as src, target.open("wb") as dst:
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    total_bytes += len(chunk)
                    if total_bytes > max_bytes:
                        raise ValueError(f"压缩包解压后超过 {max_bytes / 1024 / 1024:.0f} MB 限制")
                    dst.write(chunk)
            extracted.append(target)
    return extracted


def find_lammps_files(paths: list[Path]) -> dict[str, list[Path]]:
    result = {"logs": [], "inputs": [], "trajectories": [], "data": [], "other": []}
    for path in paths:
        name = path.name.lower()
        suffix = path.suffix.lower()
        if name.startswith("log") or name.endswith(".lammps.log"):
            result["logs"].append(path)
        elif name.startswith("in.") or suffix in {".in"}:
            result["inputs"].append(path)
        elif "dump" in name or suffix in {".lammpstrj", ".dcd", ".xtc"}:
            result["trajectories"].append(path)
        elif name.startswith("data") or suffix in {".data"}:
            result["data"].append(path)
        else:
            result["other"].append(path)
    return result
