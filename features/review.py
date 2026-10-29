from __future__ import annotations

import json
from datetime import date
from typing import Any, Callable

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from core.database import connect, now_iso, total_xp
from services.ai_provider import provider_status
from services.review_engine import (
    combine_sources,
    generate_questions,
    grade_answer,
    next_due_from_rating,
    pending_review_group,
)
from services.progression import (
    TRIBULATION_GATE_BY_KEY,
    first_pending_tribulation,
)


def _questions(value: str) -> list[dict[str, Any]]:
    try:
        items = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _pill_quantity(conn, key: str) -> int:
    row = conn.execute(
        "SELECT quantity FROM inventory_items WHERE item_key=? AND item_type='pill'",
        (key,),
    ).fetchone()
    return int(row["quantity"]) if row else 0


def _passed_gate_keys(conn) -> set[str]:
    return {
        str(row["gate_key"])
        for row in conn.execute(
            "SELECT DISTINCT gate_key FROM realm_tribulations WHERE status='passed'"
        )
    }


def register_review_routes(
    app,
    templates,
    context: Callable[..., dict[str, Any]],
    flash: Callable[[Request, str, str], None],
    current_realm: Callable[[int], dict[str, Any]],
):
    router = APIRouter()

    @router.get("/review", response_class=HTMLResponse, name="review_page")
    def review_page(request: Request):
        with connect() as conn:
            pending = pending_review_group(conn)
            history = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT s.*,COUNT(a.id) AS answer_count,
                           COALESCE(ROUND(AVG(a.score)),0) AS average_score
                    FROM review_sessions s
                    LEFT JOIN review_answers a ON a.session_id=s.id
                    WHERE s.mode='yesterday'
                    GROUP BY s.id
                    ORDER BY s.id DESC
                    LIMIT 12
                    """
                )
            ]
            source_count = conn.execute("SELECT COUNT(*) n FROM review_sources").fetchone()["n"]
            if int(source_count) >= 10:
                conn.execute(
                    """
                    UPDATE easter_eggs
                    SET unlocked=1,discovered_at=COALESCE(discovered_at,?)
                    WHERE egg_key='review_scribe'
                    """,
                    (now_iso(),),
                )
                conn.commit()
            due_count = conn.execute(
                "SELECT COUNT(*) n FROM review_answers WHERE next_due IS NOT NULL AND next_due<=?",
                (date.today().isoformat(),),
            ).fetchone()["n"]
        return templates.TemplateResponse(
            request=request,
            name="review.html",
            context=context(
                request,
                "review",
                pending=pending,
                history=history,
                source_count=source_count,
                due_count=due_count,
                ai_status=provider_status(),
                session=None,
                questions=[],
                answers={},
            ),
        )

    @router.get("/trials", response_class=HTMLResponse, name="trials_page")
    def trials_page(request: Request):
        with connect() as conn:
            xp = total_xp(conn)
            passed_gates = _passed_gate_keys(conn)
            pending_gate = first_pending_tribulation(xp, passed_gates)
            history = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT s.*,COUNT(a.id) AS answer_count,
                           COALESCE(ROUND(AVG(a.score)),0) AS average_score,
                           t.status AS tribulation_status,t.score AS tribulation_score
                    FROM review_sessions s
                    LEFT JOIN review_answers a ON a.session_id=s.id
                    LEFT JOIN realm_tribulations t ON t.session_id=s.id
                    WHERE s.mode IN (
                        'quick','mechanism','counterexample','beast','tribulation'
                    )
                    GROUP BY s.id
                    ORDER BY s.id DESC
                    LIMIT 16
                    """
                )
            ]
            source_count = conn.execute(
                "SELECT COUNT(*) n FROM review_sources"
            ).fetchone()["n"]
            tribulation_pills = _pill_quantity(conn, "tribulation_pill")
            completed_modes = int(
                conn.execute(
                    """
                    SELECT COUNT(DISTINCT mode) n FROM review_sessions
                    WHERE status='completed'
                      AND mode IN ('quick','mechanism','counterexample','beast')
                    """
                ).fetchone()["n"]
            )
            weak_point = conn.execute(
                """
                SELECT s.title,a.feedback,a.score
                FROM review_answers a
                JOIN review_sessions s ON s.id=a.session_id
                WHERE s.mode!='yesterday'
                ORDER BY a.score ASC,a.updated_at DESC LIMIT 1
                """
            ).fetchone()
        realm = current_realm(xp)
        return templates.TemplateResponse(
            request=request,
            name="trials.html",
            context=context(
                request,
                "trials",
                realm=realm,
                xp=xp,
                pending_gate=pending_gate,
                history=history,
                source_count=source_count,
                tribulation_pills=tribulation_pills,
                completed_modes=completed_modes,
                weak_point=dict(weak_point) if weak_point else None,
                ai_status=provider_status(),
            ),
        )

    @router.post("/review/start", name="review_start")
    def review_start(request: Request, mode: str = Form("yesterday")):
        allowed = {
            "yesterday", "quick", "mechanism", "counterexample", "beast",
            "tribulation",
        }
        mode = mode if mode in allowed else "yesterday"
        gate = None
        gate_title = ""
        with connect() as conn:
            pending = pending_review_group(conn) if mode == "yesterday" else None
            if mode == "yesterday":
                sources = pending["sources"] if pending else []
                source_date = pending["source_date"] if pending else ""
            else:
                source_limit = 4 if mode == "quick" else 12
                sources = [
                    dict(row)
                    for row in conn.execute(
                        """
                        SELECT id,title,source_text,source_type,source_date
                        FROM review_sources
                        ORDER BY source_date DESC,id DESC
                        LIMIT ?
                        """,
                        (source_limit,),
                    )
                ]
                source_date = sources[0]["source_date"] if sources else ""
            if not sources:
                flash(request, "还没有可用于出题的复盘关键文本。先完成一次交付并填写关键文本。", "error")
                return RedirectResponse(
                    request.url_for(
                        "review_page" if mode == "yesterday" else "trials_page"
                    ),
                    status_code=303,
                )
            if mode == "tribulation":
                gate = first_pending_tribulation(
                    total_xp(conn),
                    _passed_gate_keys(conn),
                )
                if gate is None:
                    flash(
                        request,
                        "雷劫只在金丹以上、修为达到下一大境界门槛时开启。",
                        "error",
                    )
                    return RedirectResponse(
                        request.url_for("trials_page"),
                        status_code=303,
                    )
                active_attempt = conn.execute(
                    """
                    SELECT t.session_id
                    FROM realm_tribulations t
                    JOIN review_sessions s ON s.id=t.session_id
                    WHERE t.gate_key=? AND t.status='active' AND s.status='active'
                    ORDER BY t.id DESC LIMIT 1
                    """,
                    (gate.key,),
                ).fetchone()
                if active_attempt:
                    return RedirectResponse(
                        request.url_for(
                            "review_session",
                            session_id=int(active_attempt["session_id"]),
                        ),
                        status_code=303,
                    )
                if _pill_quantity(conn, "tribulation_pill") < 1:
                    flash(request, "突破雷劫需要一枚渡劫丹，请先去炼丹炉炼制。", "error")
                    return RedirectResponse(request.url_for("alchemy_page"), status_code=303)
                gate_title = current_realm(total_xp(conn)).get(
                    "tribulation_title",
                    gate.title,
                )

        count = {
            "yesterday": 3,
            "quick": 3,
            "mechanism": 4,
            "counterexample": 4,
            "beast": 5,
            "tribulation": 5,
        }[mode]
        questions, provider, fallback_reason = generate_questions(
            combine_sources(sources),
            count=count,
            mode=mode,
        )
        titles = {
            "yesterday": f"{source_date} 交付复盘",
            "quick": "灵光问答 · 快速提取",
            "mechanism": "因果迷宫 · 机制重建",
            "counterexample": "反证之境 · 寻找边界",
            "beast": "万象秘境 · 综合会试",
            "tribulation": f"突破雷劫 · {gate_title or '五问渡关'}",
        }
        with connect() as conn:
            if mode == "tribulation":
                conn.execute(
                    "UPDATE inventory_items SET quantity=quantity-1,updated_at=? WHERE item_key='tribulation_pill' AND quantity>0",
                    (now_iso(),),
                )
                conn.execute(
                    "DELETE FROM inventory_items WHERE item_key='tribulation_pill' AND quantity<=0"
                )
            cur = conn.execute(
                """
                INSERT INTO review_sessions(
                    mode,title,source_date,questions_json,status,provider,fallback_reason,created_at
                ) VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    mode,
                    titles[mode],
                    source_date,
                    json.dumps(questions, ensure_ascii=False),
                    "active",
                    provider,
                    fallback_reason[:1000],
                    now_iso(),
                ),
            )
            session_id = int(cur.lastrowid)
            for source in sources:
                conn.execute(
                    "INSERT OR IGNORE INTO review_session_sources(session_id,review_source_id) VALUES (?,?)",
                    (session_id, source["id"]),
                )
            if mode == "tribulation" and gate is not None:
                conn.execute(
                    """
                    INSERT INTO realm_tribulations(
                        gate_key,from_stage_key,to_stage_key,session_id,status,created_at
                    ) VALUES (?,?,?,?,?,?)
                    """,
                    (
                        gate.key,
                        gate.from_key,
                        gate.to_key,
                        session_id,
                        "active",
                        now_iso(),
                    ),
                )
            conn.commit()
        return RedirectResponse(
            request.url_for("review_session", session_id=session_id),
            status_code=303,
        )

    @router.get("/review/sessions/{session_id}", response_class=HTMLResponse, name="review_session")
    def review_session(request: Request, session_id: int):
        with connect() as conn:
            row = conn.execute("SELECT * FROM review_sessions WHERE id=?", (session_id,)).fetchone()
            if not row:
                raise HTTPException(status_code=404)
            session = dict(row)
            questions = _questions(session["questions_json"])
            answers = {
                int(item["question_index"]): dict(item)
                for item in conn.execute(
                    "SELECT * FROM review_answers WHERE session_id=? ORDER BY question_index",
                    (session_id,),
                )
            }
            sources = [
                dict(item)
                for item in conn.execute(
                    """
                    SELECT s.title,s.source_date,s.source_type
                    FROM review_sources s
                    JOIN review_session_sources l ON l.review_source_id=s.id
                    WHERE l.session_id=?
                    ORDER BY s.id
                    """,
                    (session_id,),
                )
            ]
            trial_result = conn.execute(
                "SELECT * FROM realm_tribulations WHERE session_id=?",
                (session_id,),
            ).fetchone()
            trial_result = dict(trial_result) if trial_result else None
        active_page = "review" if session["mode"] == "yesterday" else "trials"
        return templates.TemplateResponse(
            request=request,
            name="review.html",
            context=context(
                request,
                active_page,
                pending=None,
                history=[],
                source_count=0,
                due_count=0,
                tribulation_pills=0,
                ai_status=provider_status(),
                session=session,
                questions=questions,
                answers=answers,
                sources=sources,
                trial_result=trial_result,
            ),
        )

    @router.post("/review/sessions/{session_id}/answer", name="review_answer")
    def review_answer(
        request: Request,
        session_id: int,
        question_index: int = Form(...),
        answer: str = Form(...),
    ):
        answer = answer.strip()
        if not answer:
            flash(request, "请先写下你的回答。", "error")
            return RedirectResponse(request.url_for("review_session", session_id=session_id), status_code=303)
        with connect() as conn:
            session_row = conn.execute("SELECT * FROM review_sessions WHERE id=?", (session_id,)).fetchone()
            if not session_row:
                raise HTTPException(status_code=404)
            questions = _questions(session_row["questions_json"])
            if question_index < 0 or question_index >= len(questions):
                raise HTTPException(status_code=400)
            question = questions[question_index]

        result, provider, fallback_reason = grade_answer(question, answer)
        ts = now_iso()
        completion_message = ""
        with connect() as conn:
            session_row = conn.execute("SELECT status,mode FROM review_sessions WHERE id=?", (session_id,)).fetchone()
            conn.execute(
                """
                INSERT INTO review_answers(
                    session_id,question_index,answer,score,level,feedback,evidence_quote,
                    confidence,provider,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(session_id,question_index) DO UPDATE SET
                    answer=excluded.answer,score=excluded.score,level=excluded.level,
                    feedback=excluded.feedback,evidence_quote=excluded.evidence_quote,
                    confidence=excluded.confidence,provider=excluded.provider,updated_at=excluded.updated_at
                """,
                (
                    session_id,
                    question_index,
                    answer,
                    result["score"],
                    result["level"],
                    result["feedback"],
                    result["evidence_quote"],
                    result["confidence"],
                    provider,
                    ts,
                    ts,
                ),
            )
            answer_count = conn.execute(
                "SELECT COUNT(*) n FROM review_answers WHERE session_id=?",
                (session_id,),
            ).fetchone()["n"]
            if answer_count >= len(questions) and session_row["status"] != "completed":
                conn.execute(
                    "UPDATE review_sessions SET status='completed',completed_at=? WHERE id=?",
                    (ts, session_id),
                )
                mode = str(session_row["mode"])
                xp = {
                    "yesterday": 8,
                    "quick": 10,
                    "mechanism": 18,
                    "counterexample": 18,
                    "beast": 24,
                }.get(mode, 8)
                if mode == "tribulation":
                    average_score = int(
                        conn.execute(
                            "SELECT COALESCE(ROUND(AVG(score)),0) score FROM review_answers WHERE session_id=?",
                            (session_id,),
                        ).fetchone()["score"]
                    )
                    passed = average_score >= 70
                    conn.execute(
                        """
                        UPDATE realm_tribulations
                        SET status=?,score=?,completed_at=?
                        WHERE session_id=?
                        """,
                        (
                            "passed" if passed else "failed",
                            average_score,
                            ts,
                            session_id,
                        ),
                    )
                    xp = 40 if passed else 8
                    attempt = conn.execute(
                        "SELECT gate_key,to_stage_key FROM realm_tribulations WHERE session_id=?",
                        (session_id,),
                    ).fetchone()
                    target = (
                        TRIBULATION_GATE_BY_KEY.get(attempt["gate_key"])
                        if attempt
                        else None
                    )
                    completion_message = (
                        f"雷劫通过，境界已解锁：{target.title if target else '突破成功'}。"
                        if passed
                        else f"本次雷劫均分 {average_score}，达到 70 分才可突破；可整理薄弱点后再试。"
                    )
                if mode in {"quick", "mechanism", "counterexample", "beast"}:
                    completed_modes = int(
                        conn.execute(
                            """
                            SELECT COUNT(DISTINCT mode) n FROM review_sessions
                            WHERE status='completed'
                              AND mode IN (
                                'quick','mechanism','counterexample','beast'
                              )
                            """
                        ).fetchone()["n"]
                    )
                    if completed_modes >= 3:
                        conn.execute(
                            """
                            UPDATE easter_eggs
                            SET unlocked=1,discovered_at=COALESCE(discovered_at,?)
                            WHERE egg_key='trial_triad'
                            """,
                            (ts,),
                        )
                conn.execute(
                    "INSERT INTO activities(action,xp,detail,created_at) VALUES (?,?,?,?)",
                    (
                        "tribulation_pass"
                        if mode == "tribulation" and xp == 40
                        else "review_complete",
                        xp,
                        f"完成：{mode}",
                        ts,
                    ),
                )
            if fallback_reason:
                conn.execute(
                    "UPDATE review_sessions SET fallback_reason=CASE WHEN fallback_reason='' THEN ? ELSE fallback_reason END WHERE id=?",
                    (fallback_reason[:1000], session_id),
                )
            conn.commit()
        flash(
            request,
            completion_message
            or "已保存评价。先看证据，再给自己一个掌握度判断。",
            "success",
        )
        return RedirectResponse(
            f"{request.url_for('review_session', session_id=session_id)}#question-{question_index}",
            status_code=303,
        )

    @router.post("/review/sessions/{session_id}/rate", name="review_rate")
    def review_rate(
        request: Request,
        session_id: int,
        question_index: int = Form(...),
        self_rating: str = Form(...),
    ):
        if self_rating not in {"mastered", "partial", "forgot"}:
            raise HTTPException(status_code=400)
        with connect() as conn:
            conn.execute(
                """
                UPDATE review_answers
                SET self_rating=?,next_due=?,updated_at=?
                WHERE session_id=? AND question_index=?
                """,
                (
                    self_rating,
                    next_due_from_rating(self_rating),
                    now_iso(),
                    session_id,
                    question_index,
                ),
            )
            conn.commit()
        return RedirectResponse(
            f"{request.url_for('review_session', session_id=session_id)}#question-{question_index}",
            status_code=303,
        )

    @router.post("/review/snooze", name="review_snooze")
    def review_snooze(request: Request):
        with connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO review_snoozes(review_day,created_at) VALUES (?,?)",
                (date.today().isoformat(), now_iso()),
            )
            conn.commit()
        flash(request, "今天先不弹出；复盘材料仍会保留。", "success")
        return RedirectResponse(request.url_for("dashboard"), status_code=303)

    app.include_router(router)
