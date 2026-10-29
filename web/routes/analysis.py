from __future__ import annotations

from fastapi import APIRouter

from web.runtime import (
    Any,
    HTTPException,
    Request,
    UPLOAD_DIR,
    connect,
    extract_file,
    flash,
    get_setting,
    json,
    now_iso,
    offline_paper_summary,
    parse_json,
    redirect,
    summary_to_markdown,
    urllib,
)

app = APIRouter()

def _number(value: str | None) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ollama_card(title: str, content: str) -> dict[str, Any]:
    endpoint = get_setting("ai_endpoint", "http://127.0.0.1:11434/api/generate")
    model = get_setting("ai_model", "qwen2.5:7b")
    prompt = f"""你是一名严谨的科研助理。根据论文文本生成中文结构化研读卡，只输出JSON，字段为 research_question、method、key_points(数组)、takeaway、keywords(数组)、relevance、limitations_prompt、next_actions(数组)。不得杜撰，信息不足写‘原文未明确说明’。论文题目：{title}\n论文文本：{content[:45000]}"""
    body = json.dumps({"model": model, "prompt": prompt, "stream": False, "format": "json"}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(endpoint, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=180) as response:
        payload = json.loads(response.read().decode("utf-8"))
    raw = payload.get("response", "{}")
    card = json.loads(raw)
    card["title"] = title
    card["mode"] = f"本地 Ollama · {model}"
    return card


@app.post("/entry/{entry_id}/analyze", name="entry_analyze")
def entry_analyze(request: Request, entry_id: int):
    with connect() as conn:
        row = conn.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404)
        if not row["content"]:
            flash(request, "该条目没有可分析文本。扫描版 PDF 需要先 OCR。", "error")
            return redirect("entry_view", request, entry_id=str(entry_id))
        mode = get_setting("ai_mode", "offline")
        try:
            card = _ollama_card(row["title"], row["content"]) if mode == "ollama" else offline_paper_summary(row["title"], row["content"])
        except Exception as exc:
            card = offline_paper_summary(row["title"], row["content"])
            card["fallback_reason"] = str(exc)
            flash(request, "本地模型不可用，已自动改用离线规则摘要。", "error")
        conn.execute(
            "UPDATE entries SET analysis_json=?, updated_at=? WHERE id=?",
            (json.dumps(card, ensure_ascii=False), now_iso(), entry_id),
        )
        conn.execute(
            "INSERT INTO activities(action, entry_id, xp, detail, created_at) VALUES (?, ?, ?, ?, ?)",
            ("analyze", entry_id, 12, f"生成研读卡：{row['title']}", now_iso()),
        )
        conn.commit()
    flash(request, "论文研读卡已生成。")
    return redirect("entry_view", request, entry_id=str(entry_id))

@app.post("/entry/{entry_id}/analysis-to-note", name="analysis_to_note")
def analysis_to_note(request: Request, entry_id: int):
    with connect() as conn:
        row = conn.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404)
        card = parse_json(row["analysis_json"], {})
        if not card:
            flash(request, "请先生成论文研读卡。", "error")
            return redirect("entry_view", request, entry_id=str(entry_id))
        ts = now_iso()
        cur = conn.execute(
            "INSERT INTO entries(title,kind,domain,tags,summary,content,source,created_at,updated_at,extract_status,indexed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (f"研读卡｜{row['title']}", "note", row["domain"], row["tags"], "由论文条目自动生成，可继续人工修订。", summary_to_markdown(card), f"关联条目 #{entry_id}", ts, ts, "generated", ts),
        )
        note_id = int(cur.lastrowid)
        conn.execute("INSERT INTO activities(action,entry_id,xp,detail,created_at) VALUES (?,?,?,?,?)", ("analysis_note", note_id, 10, f"沉淀研读卡：{row['title']}", ts))
        conn.commit()
    flash(request, "研读卡已转为可编辑笔记。")
    return redirect("entry_view", request, entry_id=str(note_id))


@app.post("/entry/{entry_id}/reindex", name="entry_reindex")
def entry_reindex(request: Request, entry_id: int):
    with connect() as conn:
        row = conn.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
        if not row or not row["file_path"]:
            raise HTTPException(status_code=404)
        path = UPLOAD_DIR / row["file_path"]
        if not path.exists():
            flash(request, "原文件不存在，无法重新建立索引。", "error")
            return redirect("entry_view", request, entry_id=str(entry_id))
        extracted = extract_file(path)
        status = "ok" if extracted.get("content") else ("error" if extracted.get("error") else "no_text")
        conn.execute(
            "UPDATE entries SET content=?, mime_type=?, dataset_rows=?, dataset_columns=?, dataset_schema=?, dataset_preview=?, extract_status=?, indexed_at=?, updated_at=? WHERE id=?",
            (extracted.get("content", ""), extracted.get("mime_type", row["mime_type"]), extracted.get("rows"), extracted.get("columns"), json.dumps(extracted.get("schema", []), ensure_ascii=False), json.dumps(extracted.get("preview", []), ensure_ascii=False, default=str), status, now_iso(), now_iso(), entry_id),
        )
        conn.execute("INSERT INTO activities(action,entry_id,xp,detail,created_at) VALUES (?,?,?,?,?)", ("reindex", entry_id, 5, f"重建全文索引：{row['title']}", now_iso()))
        conn.commit()
    flash(request, "全文索引已重新建立。")
    return redirect("entry_view", request, entry_id=str(entry_id))
