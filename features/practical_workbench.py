"""Practical, local-first tools for preparing research text and figures."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from core.database import connect, now_iso
from services.ai_provider import AIProviderError, generate_structured, provider_status
from web.runtime import UPLOAD_DIR, secure_filename


TEXT_MODES = {
    "zh_academic": ("中文学术润色", "压缩口语和重复，保留证据强度与单位。"),
    "en_academic": ("英文论文润色", "改善语法与学术表达，不把结果夸大。"),
    "figure_caption": ("图注与图中说明", "按对象—变量—对照—统计—结论边界重写。"),
    "report_script": ("文献汇报讲稿", "把一段材料改成能围绕关键图讲清的口述稿。"),
}


def _reading_result(source: str) -> dict[str, str]:
    """Create an English-reading exercise.  Translation is deliberately last."""
    try:
        result, provider = generate_structured(
            system_prompt=(
                "你是材料科学英语精读教练。目标是让学生自己读懂顶级期刊论文，"
                "而非把英文逐句翻译成中文。绝不杜撰论文内容。"
            ),
            user_prompt=(
                "对下列英文论文片段设计一次 15 分钟精读训练。先帮助读句法和证据关系，"
                "最后才允许写中文理解。输出 JSON：structure（句子骨架与逻辑连接词），"
                "phrases（3–8 个可迁移学术搭配，含原文短语和简短中文功能说明），"
                "questions（3 个必须回到原文回答的英文问题），summary_task（一项 80–120 词英文复述任务）。\n\n"
                f"原文：\n{source}"
            ),
            schema={
                "type": "object", "additionalProperties": False,
                "properties": {
                    "structure": {"type": "string"}, "phrases": {"type": "string"},
                    "questions": {"type": "string"}, "summary_task": {"type": "string"},
                },
                "required": ["structure", "phrases", "questions", "summary_task"],
            },
            schema_name="english_reading_drill",
        )
        return {key: _clean(str(result.get(key, "")), 5000) for key in ("structure", "phrases", "questions", "summary_task")} | {"provider": provider}
    except AIProviderError:
        return {
            "structure": "离线训练：先圈出每句谓语动词与连接词（however, therefore, whereas, suggesting 等），再用斜线划分从句；不要先查整句翻译。",
            "phrases": "离线模式不自动猜测术语。请从原文挑 3 个反复出现或能表达因果、比较、限制的词组，抄下完整搭配与所在句。",
            "questions": "1. What question does this passage answer?\n2. Which words describe evidence rather than interpretation?\n3. What limitation or condition is stated?",
            "summary_task": "不用翻译原文。写 80–120 词英文：研究对象、比较关系、观察到的结果和作者允许下的结论各一句。",
            "provider": "离线规则",
        }


def _clean(value: str, limit: int) -> str:
    return (value or "").strip()[:limit]


def _offline_result(mode: str, source: str) -> dict[str, str]:
    label = TEXT_MODES[mode][0]
    prompt = f"""你是严谨的材料科学论文编辑。请对下面的{label}进行处理。

硬性规则：
1. 只依据原文，不补造数据、文献、机制或因果；
2. 保留全部数值、单位、样品名、图号和不确定性；
3. 将结果与解释分开，删掉空泛形容词；
4. 输出：润色稿；逐条修改说明；仍需作者核对的事实。

原文：
{source}"""
    return {
        "polished": source,
        "changes": "当前为离线规则模式：未自动改写原文，避免把未经核对的科学表述写进你的文献或汇报。",
        "checks": "核对：数值/单位是否原样保留；比较对象是否明确；结果与机制解释是否分开；图号和引用是否对应。",
        "prompt": prompt,
        "provider": "离线规则",
    }


def _polish(mode: str, source: str) -> dict[str, str]:
    if mode not in TEXT_MODES:
        mode = "zh_academic"
    try:
        result, provider = generate_structured(
            system_prompt=(
                "你是审慎的科研写作编辑。你绝不编造数据、引文、实验或机制；"
                "不把相关性写成因果，也不删除不确定性。"
            ),
            user_prompt=(
                f"任务：{TEXT_MODES[mode][0]}。{TEXT_MODES[mode][1]}\n\n"
                "请仅根据原文输出 JSON 字段：polished（完整可编辑文本）、"
                "changes（不超过 6 条的修改说明）、checks（仍需作者核对的事实或空字符串）。\n\n"
                f"原文：\n{source}"
            ),
            schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "polished": {"type": "string"},
                    "changes": {"type": "string"},
                    "checks": {"type": "string"},
                },
                "required": ["polished", "changes", "checks"],
            },
            schema_name="research_text_polish",
        )
        polished = _clean(str(result.get("polished", "")), 16000) or source
        return {
            "polished": polished,
            "changes": _clean(str(result.get("changes", "")), 4000),
            "checks": _clean(str(result.get("checks", "")), 4000),
            "prompt": "",
            "provider": provider,
        }
    except AIProviderError:
        return _offline_result(mode, source)


def _trim_border(image, padding: int):
    """Trim only a uniform outer border; never recolour or alter plotted pixels."""
    from PIL import Image, ImageChops

    rgba = image.convert("RGBA")
    background = rgba.getpixel((0, 0))
    diff = ImageChops.difference(rgba, Image.new("RGBA", rgba.size, background))
    bbox = diff.getbbox()
    if not bbox:
        return rgba
    left, top, right, bottom = bbox
    left = max(0, left - padding)
    top = max(0, top - padding)
    right = min(rgba.width, right + padding)
    bottom = min(rgba.height, bottom + padding)
    return rgba.crop((left, top, right, bottom))


def _save_image_entry(
    *, image, original_name: str, operation: str, target_width: int, output_format: str
) -> int:
    from PIL import Image

    suffix = {"PNG": ".png", "JPEG": ".jpg", "TIFF": ".tiff"}[output_format]
    stem = secure_filename(Path(original_name).stem) or "figure"
    stored_name = f"workbench_{uuid4().hex}_{stem}{suffix}"
    output_path = UPLOAD_DIR / stored_name
    save_image = image
    if output_format == "JPEG":
        save_image = image.convert("RGB")
    save_image.save(output_path, format=output_format, dpi=(300, 300))
    timestamp = now_iso()
    operation_label = "裁去统一留白" if operation == "trim" else "统一导出"
    with connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO entries(
                title,kind,domain,tags,summary,content,file_path,original_name,mime_type,file_size,
                source,extract_status,content_format,created_at,updated_at
            ) VALUES (?,'image','图像与图谱','工作台,图片处理,可编辑',?,?,?,?,?,?,?,'ready','plain',?,?)
            """,
            (
                f"{stem} · {operation_label}",
                f"{operation_label} · {image.width} × {image.height} px · {output_format}",
                "处理过程未改动图中数据或颜色；发布/汇报前请人工核对坐标轴、图例和比例尺。",
                stored_name,
                f"{stem}{suffix}",
                Image.MIME.get(output_format, "application/octet-stream"),
                output_path.stat().st_size,
                "实用工作台 · 图片处理",
                timestamp,
                timestamp,
            ),
        )
        entry_id = int(cursor.lastrowid)
        conn.execute(
            "INSERT INTO activities(action,entry_id,xp,detail,created_at) VALUES (?,?,?,?,?)",
            ("image_workbench", entry_id, 0, f"处理图像：{stem}", timestamp),
        )
        conn.commit()
    return entry_id


def register_practical_workbench_routes(
    app,
    templates,
    context: Callable[..., dict[str, Any]],
    flash: Callable[[Request, str, str], None],
):
    router = APIRouter()

    def page_context(request: Request, **values: Any) -> dict[str, Any]:
        return context(
            request,
            "workbench",
            text_modes=TEXT_MODES,
            provider=provider_status(),
            **values,
        )

    @router.get("/workbench", response_class=HTMLResponse, name="workbench_page")
    def workbench_page(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="workbench.html",
            context=page_context(request, text_mode="zh_academic", source_text="", result=None),
        )

    @router.post("/workbench/polish", response_class=HTMLResponse, name="workbench_polish")
    def workbench_polish(
        request: Request,
        text_mode: str = Form("zh_academic"),
        source_text: str = Form(""),
    ):
        source = _clean(source_text, 16000)
        if len(source) < 8:
            flash(request, "请先粘贴至少一句需要处理的文字。", "error")
            return templates.TemplateResponse(
                request=request,
                name="workbench.html",
                context=page_context(request, text_mode=text_mode, source_text=source, result=None),
            )
        result = _polish(text_mode, source)
        return templates.TemplateResponse(
            request=request,
            name="workbench.html",
            context=page_context(request, text_mode=text_mode, source_text=source, result=result),
        )

    @router.post("/workbench/reading", response_class=HTMLResponse, name="workbench_reading")
    def workbench_reading(request: Request, reading_text: str = Form("")):
        source = _clean(reading_text, 12000)
        if len(source) < 20:
            flash(request, "请先粘贴至少一句英文论文原文。", "error")
            return templates.TemplateResponse(request=request, name="workbench.html", context=page_context(request, text_mode="zh_academic", source_text="", result=None, reading_text=source, reading_result=None))
        return templates.TemplateResponse(
            request=request, name="workbench.html",
            context=page_context(request, text_mode="zh_academic", source_text="", result=None, reading_text=source, reading_result=_reading_result(source)),
        )

    @router.post("/workbench/reading/save", name="workbench_reading_save")
    def workbench_reading_save(
        request: Request, title: str = Form("英文精读"), reading_text: str = Form(""),
        structure: str = Form(""), phrases: str = Form(""), questions: str = Form(""), summary_task: str = Form(""),
    ):
        timestamp = now_iso()
        source = _clean(reading_text, 12000)
        if len(source) < 20:
            flash(request, "没有可归档的英文原文。", "error")
            return RedirectResponse(request.url_for("workbench_page"), status_code=303)
        body = f"英文原文\n{source}\n\n句法与逻辑\n{_clean(structure, 5000)}\n\n可迁移搭配\n{_clean(phrases, 5000)}\n\n回原文回答\n{_clean(questions, 5000)}\n\n英文复述任务\n{_clean(summary_task, 3000)}"
        with connect() as conn:
            cursor = conn.execute(
                """INSERT INTO entries(title,kind,domain,tags,summary,content,source,extract_status,content_format,created_at,updated_at)
                   VALUES (?,'note','科研英语','工作台,英文精读,可修改',?,?,?,'ready','plain',?,?)""",
                (_clean(title, 180) or "英文精读", _clean(structure, 280), body, "实用工作台 · 英文精读", timestamp, timestamp),
            )
            entry_id = int(cursor.lastrowid)
            conn.commit()
        flash(request, "英文精读训练已归档；下次可以在这张知识卡上补自己的英文复述。", "success")
        return RedirectResponse(request.url_for("entry_view", entry_id=entry_id), status_code=303)

    @router.post("/workbench/polish/save", name="workbench_polish_save")
    def workbench_polish_save(
        request: Request,
        title: str = Form("润色记录"),
        text_mode: str = Form("zh_academic"),
        source_text: str = Form(""),
        polished_text: str = Form(""),
        changes: str = Form(""),
        checks: str = Form(""),
    ):
        polished = _clean(polished_text, 16000)
        if len(polished) < 3:
            flash(request, "没有可归档的润色文本。", "error")
            return RedirectResponse(request.url_for("workbench_page"), status_code=303)
        timestamp = now_iso()
        heading = _clean(title, 180) or "润色记录"
        body = (
            f"原文\n{_clean(source_text, 16000)}\n\n润色稿\n{polished}\n\n"
            f"修改说明\n{_clean(changes, 4000)}\n\n仍需核对\n{_clean(checks, 4000)}"
        )
        with connect() as conn:
            cursor = conn.execute(
                """INSERT INTO entries(title,kind,domain,tags,summary,content,source,extract_status,content_format,created_at,updated_at)
                   VALUES (?,'note','科研写作','工作台,文献润色,可修改',?,?,?,'ready','plain',?,?)""",
                (heading, polished[:280], body, f"实用工作台 · {TEXT_MODES.get(text_mode, TEXT_MODES['zh_academic'])[0]}", timestamp, timestamp),
            )
            entry_id = int(cursor.lastrowid)
            conn.commit()
        flash(request, "润色记录已作为可编辑知识卡归档。", "success")
        return RedirectResponse(request.url_for("entry_view", entry_id=entry_id), status_code=303)

    @router.post("/workbench/image", name="workbench_image")
    def workbench_image(
        request: Request,
        image_file: UploadFile = File(...),
        operation: str = Form("trim"),
        target_width: int = Form(0),
        padding: int = Form(20),
        output_format: str = Form("PNG"),
    ):
        if not image_file.filename:
            flash(request, "请选择一张图片。", "error")
            return RedirectResponse(request.url_for("workbench_page") + "#figures", status_code=303)
        if Path(image_file.filename).suffix.lower() not in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp"}:
            flash(request, "请上传 PNG、JPG、TIFF、WebP 或 BMP 图片。", "error")
            return RedirectResponse(request.url_for("workbench_page") + "#figures", status_code=303)
        try:
            from PIL import Image, ImageOps

            image_file.file.seek(0, 2)
            size = image_file.file.tell()
            image_file.file.seek(0)
            if size > 30 * 1024 * 1024:
                raise ValueError("图片不能超过 30 MB")
            with Image.open(image_file.file) as raw:
                image = ImageOps.exif_transpose(raw).copy()
            if operation == "trim":
                image = _trim_border(image, max(0, min(int(padding), 240)))
            width = max(0, min(int(target_width), 6000))
            if width and image.width != width:
                height = max(1, round(image.height * width / image.width))
                image = image.resize((width, height), Image.Resampling.LANCZOS)
            fmt = output_format if output_format in {"PNG", "JPEG", "TIFF"} else "PNG"
            entry_id = _save_image_entry(
                image=image,
                original_name=image_file.filename,
                operation=operation if operation == "trim" else "export",
                target_width=width,
                output_format=fmt,
            )
        except (OSError, ValueError) as exc:
            flash(request, f"图片处理失败：{exc}", "error")
            return RedirectResponse(request.url_for("workbench_page") + "#figures", status_code=303)
        flash(request, "图片已处理并自动归档；可继续编辑说明或下载使用。", "success")
        return RedirectResponse(request.url_for("entry_view", entry_id=entry_id), status_code=303)

    @router.post("/workbench/figure-board", name="workbench_figure_board")
    def workbench_figure_board(
        request: Request, board_files: list[UploadFile] = File(...), columns: int = Form(2), panel_width: int = Form(1200), gap: int = Form(36),
    ):
        selected = [item for item in board_files if item.filename]
        if not 2 <= len(selected) <= 8:
            flash(request, "请一次选择 2–8 张图片来组合版式。", "error")
            return RedirectResponse(request.url_for("workbench_page") + "#figures", status_code=303)
        try:
            from PIL import Image, ImageDraw, ImageOps

            prepared = []
            for item in selected:
                if Path(item.filename or "").suffix.lower() not in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp"}:
                    raise ValueError("拼图只支持常见图片文件")
                item.file.seek(0, 2)
                if item.file.tell() > 30 * 1024 * 1024:
                    raise ValueError("单张图片不能超过 30 MB")
                item.file.seek(0)
                with Image.open(item.file) as raw:
                    prepared.append(ImageOps.exif_transpose(raw).convert("RGBA").copy())
            cols = max(1, min(int(columns), 4, len(prepared)))
            width = max(400, min(int(panel_width), 2400))
            spacing = max(8, min(int(gap), 160))
            label_h = 52
            cell_h = max(round(image.height * width / image.width) for image in prepared)
            rows = (len(prepared) + cols - 1) // cols
            board = Image.new("RGBA", (cols * width + (cols - 1) * spacing, rows * (cell_h + label_h) + (rows - 1) * spacing), "white")
            draw = ImageDraw.Draw(board)
            for index, image in enumerate(prepared):
                row, col = divmod(index, cols)
                x, y = col * (width + spacing), row * (cell_h + label_h + spacing)
                fitted = ImageOps.contain(image, (width, cell_h), Image.Resampling.LANCZOS)
                board.alpha_composite(fitted, (x + (width - fitted.width) // 2, y + label_h + (cell_h - fitted.height) // 2))
                draw.text((x + 4, y + 3), chr(65 + index), fill=(33, 33, 33, 255), stroke_width=0, font_size=36)
            entry_id = _save_image_entry(image=board, original_name="multi_panel_figure.png", operation="export", target_width=board.width, output_format="PNG")
        except (OSError, ValueError) as exc:
            flash(request, f"多面板排版失败：{exc}", "error")
            return RedirectResponse(request.url_for("workbench_page") + "#figures", status_code=303)
        flash(request, "多面板图已排好并归档；请在提交前核对 A/B 标签、比例尺和各图的大小关系。", "success")
        return RedirectResponse(request.url_for("entry_view", entry_id=entry_id), status_code=303)

    app.include_router(router)
