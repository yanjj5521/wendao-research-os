"""灵知：一只低干扰的桌面研究小灵宠。

互动一览
--------
- 单击：报告今日进度与心情
- 双击：摸头（好感度 +1，连续摸有彩蛋）
- 滚轮 / 菜单：调整大小
- 右键菜单：进度、研究提示、安静模式、关闭
- 闲置时每隔一两分钟主动说一句话（可在菜单里切安静模式）

心情系统：根据今日必做完成度、近 7 天学习活跃天数与当前时段推断心情，
心情决定灵知的色调、摇摆节奏和说话语气——它不只是贴图，会回应你的状态。

成长系统
--------
- 经验：每日首次唤醒 +10；每条学习活动 +2（每日上限 40）；每完成一项今日必做 +8
  （每日上限 24）；摸摸头 +1（每日上限 5）。
- 等级：Lv.n 的累计经验为 25·(n−1)·n（Lv.2=50，Lv.3=150…），上限 Lv.20。
- 形态：Lv.3 青叶、Lv.5 灵花、Lv.8 道果、Lv.12 星灵；形态只改灵知本体色调与轻微尺寸，
  背景保持透明。
- 存储：`user_config/desktop_pet.json` 的 `progression` 字段；首次升级到本版时只标记历史，
  不会把旧活动一次性折算成经验。
"""
from __future__ import annotations

import base64
import json
import math
import os
import random
import tempfile
import tkinter as tk
from datetime import date, datetime, timedelta
from pathlib import Path

from PIL import Image, ImageTk

from core.database import connect
from core.runtime_paths import USER_CONFIG_DIR
from services.pet_progression import (
    award,
    describe,
    form_for_level,
    form_gallery,
    load_progress,
    record_pat,
    rules_text,
)

PET_LINES = (
    "先喝一口水，再继续读这一段。",
    "把这张图讲成一句因果关系，便是进展。",
    "卡住时，只写下：作者究竟证明了什么？",
    "休息 30 秒，回来只推进一个最小动作。",
    "不要替论文补结论，先找它的证据。",
    "今天只求一个最小交付，不求完美。",
    "读到反例时别沮丧——它帮你划掉了半条弯路。",
    "把公式旁边的单位检查一遍，常常有惊喜。",
    "图比文字诚实：先看坐标轴，再看结论。",
    "写不下去的时候，假装在写给三天后的自己。",
)
CALM_LINES = (
    "我在这里陪你。慢慢来。",
    "深呼吸。下一步已经写好了。",
    "你只需要负责开始，剩下的交给惯性。",
)
HAPPY_LINES = (
    "今天的必做都完成了！奖励自己五分钟发呆。",
    "灵知很骄傲。尾巴（如果有的话）在摇。",
    "进度条今天在笑。",
)
SLEEPY_LINES = (
    "这么晚了……灵知困了，你也早点休息。",
    "深夜的灵感很贵，明早的你更想要睡眠。",
    "屏幕会等你，身体不会。",
)
PAT_LINES = (
    "被摸头了……很舒服。",
    "再摸也要做实验的。",
    "灵知的修为 +0，心情 +10。",
    "好啦好啦，这就替你盯着进度。",
)
PAT_EGG = "摸了这么多下……灵知决定把这份好感存进修为里（并没有）。"
SETTINGS_PATH = USER_CONFIG_DIR / "desktop_pet.json"
DEFAULT_SIZE = 132
MIN_SIZE = 92
MAX_SIZE = 260

MOOD_TINT = {
    "happy": (255, 214, 130, 34),
    "proud": (255, 186, 200, 30),
    "sleepy": (150, 172, 220, 44),
    "nudge": (255, 236, 180, 22),
}
MOOD_EMOJI = {
    "happy": "✨",
    "proud": "🎉",
    "sleepy": "😴",
    "calm": "🌿",
    "nudge": "💡",
}
# 透明键取灵知自身边缘的青光色：Tk 会把半透明像素与键色混合，
# 用洋红会混出一圈粉边；用同色系则混成自然的辉光。
TRANSPARENT_KEY = "#a8e6f2"
# 灵知保持透明背景：不铺光晕、不铺阴影，窗口只显示灵知自身像素。
# 心情只通过灵知本体染色表达，避免在桌面上出现一圈粉色/彩色的光斑。


def _clamp_position(
    x: int, y: int, width: int, height: int, screen_width: int, screen_height: int
) -> tuple[int, int]:
    return (
        max(0, min(int(x), max(0, screen_width - width))),
        max(0, min(int(y), max(0, screen_height - height))),
    )


def _asset_path() -> Path:
    bundled = Path(__file__).resolve().parent / "static" / "pet" / "lingzhi_3d.png"
    if bundled.exists():
        return bundled
    encoded = bundled.with_suffix(".png.b64")
    target = Path(tempfile.gettempdir()) / "wendao-lingzhi-assistant.png"
    if encoded.exists() and not target.exists():
        target.write_bytes(base64.b64decode(encoded.read_text(encoding="ascii")))
    return target


def _load_settings() -> dict:
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def _save_settings(data: dict) -> None:
    try:
        USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        temporary = SETTINGS_PATH.with_name(SETTINGS_PATH.name + ".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, SETTINGS_PATH)
    except OSError:
        pass


def _load_size() -> int:
    settings = _load_settings()
    # 旧版扁平桌宠很大：一次性迁移，让新 3D 伙伴从最小尺寸开始。
    if settings.get("appearance") != "3d":
        return DEFAULT_SIZE
    return max(MIN_SIZE, min(MAX_SIZE, int(settings.get("size", DEFAULT_SIZE))))


def _mission_progress() -> tuple[int, int]:
    """(必做已完成, 必做总数)；没有活跃计划时返回 (0, 0)。"""
    try:
        with connect() as conn:
            row = conn.execute(
                """SELECT COUNT(*) total,
                          COALESCE(SUM(CASE WHEN completed=1 THEN 1 ELSE 0 END), 0) done
                     FROM daily_missions m
                     JOIN study_plans p ON p.id=m.plan_id
                    WHERE p.status='active' AND m.day_index=p.current_day AND m.optional=0"""
            ).fetchone()
        return int(row["done"]), int(row["total"])
    except Exception:
        return 0, 0


def _recent_days() -> int:
    """最近 7 天里有学习活动的天数（用于「得意」心情）。"""
    try:
        since = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        with connect() as conn:
            row = conn.execute(
                "SELECT COUNT(DISTINCT substr(created_at,1,10)) n FROM activities WHERE substr(created_at,1,10) >= ?",
                (since,),
            ).fetchone()
        return int(row["n"])
    except Exception:
        return 0


def _progress_snapshot(day: str) -> tuple[list[int], int]:
    """今日活动 id 与已完成必做数；数据库异常时安静退回空快照。"""
    try:
        with connect() as conn:
            ids = [
                int(row["id"])
                for row in conn.execute(
                    "SELECT id FROM activities WHERE substr(created_at,1,10)=? ORDER BY id",
                    (day,),
                )
            ]
            row = conn.execute(
                """SELECT COUNT(*) n FROM daily_missions m
                     JOIN study_plans p ON p.id=m.plan_id
                    WHERE p.status='active' AND m.day_index=p.current_day
                      AND m.optional=0 AND m.completed=1"""
            ).fetchone()
        return ids, int(row["n"])
    except Exception:
        return [], 0


def _activity_stats() -> tuple[int, int, int]:
    """(今日活动数, 近 7 天活跃天数, 连续活跃天数)；失败时返回 0。"""
    try:
        today = date.today()
        since = (today - timedelta(days=6)).isoformat()
        with connect() as conn:
            today_count = int(
                conn.execute(
                    "SELECT COUNT(*) n FROM activities WHERE substr(created_at,1,10)=?",
                    (today.isoformat(),),
                ).fetchone()["n"]
            )
            days = [
                str(row["d"])
                for row in conn.execute(
                    "SELECT DISTINCT substr(created_at,1,10) d FROM activities "
                    "WHERE substr(created_at,1,10) >= ? ORDER BY d DESC",
                    (since,),
                )
            ]
        streak = 0
        cursor = today
        for day in days:
            if day == cursor.isoformat():
                streak += 1
                cursor -= timedelta(days=1)
            else:
                break
        return today_count, len(days), streak
    except Exception:
        return 0, 0, 0


def _form_tinted(frame: Image.Image, form: dict) -> Image.Image:
    """Only the pet's own pixels receive the form colour; alpha is preserved."""
    tint = form.get("tint")
    alpha = float(form.get("alpha", 0) or 0)
    if not tint or alpha <= 0:
        return frame
    frame = frame.convert("RGBA")
    overlay = Image.new("RGBA", frame.size, tuple(tint) + (255,))
    blended = Image.blend(frame, overlay, alpha)
    blended.putalpha(frame.getchannel("A"))
    return blended


def detect_mood() -> str:
    """心情 = 时段 + 今日完成度 + 近期活跃。只读，不打扰。"""
    hour = datetime.now().hour
    if hour >= 23 or hour < 6:
        return "sleepy"
    done, total = _mission_progress()
    if total > 0 and done >= total:
        return "happy"
    if _recent_days() >= 5:
        return "proud"
    if total > 0 and done == 0:
        return "nudge"
    return "calm"


def mood_line(mood: str) -> str:
    done, total = _mission_progress()
    emoji = MOOD_EMOJI.get(mood, "🌿")
    if mood == "sleepy":
        return f"{emoji} {random.choice(SLEEPY_LINES)}"
    if mood == "happy":
        return f"{emoji} {random.choice(HAPPY_LINES)}"
    if mood == "proud":
        return f"{emoji} 连续一周都有学习记录。这份惯性很珍贵，别浪费在自责上。"
    if mood == "nudge":
        return f"{emoji} 今日进度 {done}/{total}。只推进下一项，不必一次做完。"
    if total <= 0:
        return f"{emoji} 今天还没有必做任务。先写下一个 10 分钟的小动作吧。"
    return f"{emoji} {random.choice(CALM_LINES)}"


def _trim_haze(frame: Image.Image, threshold: int = 8) -> Image.Image:
    """Drop near-transparent background haze that would tint the whole window.

    The source render carries a very low alpha veil; blended with the key colour
    it becomes a faint rectangular wash.  Pixels below the threshold are cleared,
    while genuine anti-aliased edges keep their alpha.
    """
    alpha = frame.getchannel("A").point(lambda value: 0 if value < threshold else value)
    frame.putalpha(alpha)
    return frame


def _tinted(frame: Image.Image, mood: str) -> Image.Image:
    """Tint only the pet's own pixels; transparent corners stay transparent."""
    color = MOOD_TINT.get(mood)
    if not color:
        return frame
    frame = frame.convert("RGBA")
    overlay = Image.new("RGBA", frame.size, color)
    tinted = Image.blend(frame, overlay, 0.16)
    tinted.putalpha(frame.getchannel("A"))
    return tinted


def run_pet() -> None:
    root = tk.Tk()
    root.title("灵知")
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    transparent = TRANSPARENT_KEY
    try:
        root.wm_attributes("-transparentcolor", transparent)
    except tk.TclError:
        transparent = "#f4e7d5"
    root.configure(bg=transparent)

    settings = _load_settings()
    today = datetime.now().strftime("%Y-%m-%d")
    raw_progress = settings.get("progression")
    progress = load_progress(raw_progress, today)
    activity_ids, missions_done = _progress_snapshot(today)
    progress, pending_events = award(
        progress,
        day=today,
        activity_ids=activity_ids,
        missions_done=missions_done,
        bootstrap=raw_progress is None,
    )
    form = form_for_level(progress.level)
    settings["progression"] = progress.to_dict()
    _save_settings(settings)
    original = Image.open(_asset_path()).convert("RGBA")
    desired_size = _load_size()
    mood = detect_mood()
    pats = int(settings.get("pats", 0))
    muted = tk.BooleanVar(value=bool(settings.get("muted", False)))
    locked = tk.BooleanVar(value=bool(settings.get("locked", False)))
    phase = 0.0
    squish_until = 0.0
    quiet_until = 0.0
    dragging = False
    moved = False
    offset_x = offset_y = 0
    press_root = (0, 0)
    image_label = tk.Label(root, bg=transparent, borderwidth=0, cursor="hand2")
    image_label.pack()
    hint = tk.Toplevel(root)
    hint.overrideredirect(True)
    hint.attributes("-topmost", True)
    hint.configure(bg="#213242")
    hint_label = tk.Label(
        hint, text="", bg="#213242", fg="#fff4d2",
        font=("Microsoft YaHei UI", 9), padx=10, pady=6,
        wraplength=260, justify="left",
    )
    hint_label.pack()
    hint.withdraw()
    hint_token = 0
    menu = tk.Menu(root, tearoff=False)

    def _wobble_scale() -> float:
        base = {"happy": 0.045, "proud": 0.035, "nudge": 0.03, "calm": 0.025, "sleepy": 0.014}.get(mood, 0.025)
        base *= float(form.get("wobble", 1.0))
        speed = {"happy": 0.3, "proud": 0.26, "sleepy": 0.12}.get(mood, 0.22)
        return 1.0 + (base * math.sin(phase * speed / 0.22))

    def render(*, animate: bool = True, squish: bool = False) -> None:
        now = datetime.now().timestamp()
        wobble = _wobble_scale() if animate and not dragging else 1.0
        if squish or now < squish_until:
            wobble *= 1.055
        width = max(MIN_SIZE, round(desired_size * wobble * float(form.get("scale", 1.0))))
        height_factor = 0.96 if (squish or now < squish_until) else 1.0
        height = max(MIN_SIZE, round(original.height * width / max(original.width, 1) * height_factor))
        frame = _trim_haze(
            _form_tinted(
                _tinted(
                    original.resize((width, height), Image.Resampling.LANCZOS), mood
                ),
                form,
            )
        )
        photo = ImageTk.PhotoImage(frame)
        image_label.configure(image=photo)
        image_label.image = photo

    def animate() -> None:
        nonlocal phase
        phase += 0.22
        render()
        root.after(130, animate)

    def _speak(text: str, seconds: int = 4200) -> None:
        nonlocal hint_token
        hint_token += 1
        token = hint_token
        hint_label.configure(text=text)
        hint.update_idletasks()
        width = max(hint.winfo_reqwidth(), 80)
        height = hint.winfo_reqheight()
        screen_width = root.winfo_screenwidth()
        screen_height = root.winfo_screenheight()
        x = root.winfo_x() + (root.winfo_width() - width) // 2
        y = root.winfo_y() + root.winfo_height() + 6
        if y + height > screen_height:
            y = root.winfo_y() - height - 6
        x = max(0, min(x, screen_width - width))
        y = max(0, min(y, screen_height - height))
        hint.geometry(f"+{x}+{y}")
        hint.deiconify()
        hint.lift()
        root.after(seconds, lambda: hint.withdraw() if token == hint_token else None)
    def _persist_progress() -> None:
        current = _load_settings()
        current["progression"] = progress.to_dict()
        _save_settings(current)

    def _announce(events: list[dict]) -> None:
        levels = [event for event in events if event.get("type") == "level"]
        if not levels:
            return
        event = levels[-1]
        text = f"✨ 灵知升到 Lv.{event['level']}"
        if event.get("unlocked"):
            text += f"，解锁形态：{event['form_name']}"
        _speak(text, 5600)

    def _refresh_menu() -> None:
        menu.entryconfigure(0, label=f"灵知成长 · Lv.{progress.level} {form['name']}")

    def sync_progress() -> None:
        nonlocal progress, form, mood
        current_day = datetime.now().strftime("%Y-%m-%d")
        activity_ids, missions_done = _progress_snapshot(current_day)
        progress, events = award(
            progress,
            day=current_day,
            activity_ids=activity_ids,
            missions_done=missions_done,
        )
        updated_form = form_for_level(progress.level)
        if updated_form["key"] != form["key"]:
            form = updated_form
            render(animate=False)
        mood = detect_mood()
        _persist_progress()
        _refresh_menu()
        _announce(events)
        root.after(300_000, sync_progress)

    def show_growth() -> None:
        summary = describe(progress)
        window = tk.Toplevel(root)
        window.title("灵知成长")
        window.configure(bg="#213242")
        window.resizable(False, False)
        window.transient(root)
        window.attributes("-topmost", True)
        body = tk.Frame(window, bg="#213242", padx=16, pady=14)
        body.pack(fill="both", expand=True)
        tk.Label(
            body, text=f"Lv.{summary['level']} · {summary['form']['name']}",
            bg="#213242", fg="#fff4d2", font=("Microsoft YaHei UI", 13, "bold"),
        ).pack(anchor="w")
        tk.Label(
            body, text=summary["form"]["note"], bg="#213242", fg="#b9c7d4",
            font=("Microsoft YaHei UI", 9), wraplength=300, justify="left",
        ).pack(anchor="w", pady=(2, 8))
        bar = tk.Canvas(body, width=280, height=10, bg="#3a4d5e", highlightthickness=0)
        bar.pack(anchor="w")
        bar.create_rectangle(
            0, 0, max(1, int(280 * summary["percent"])), 10, fill="#d9a85c", width=0
        )
        if summary["next_level_xp"]:
            exp = (
                f"XP {summary['xp']} / {summary['next_level_xp']} · "
                f"距下一级还差 {summary['next_level_xp'] - summary['xp']}"
            )
        else:
            exp = f"XP {summary['xp']} · 已满级"
        tk.Label(body, text=exp, bg="#213242", fg="#fff4d2", font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(6, 0))
        upcoming = summary["next_form"]
        tk.Label(
            body,
            text=(f"下一形态：{upcoming['name']}（Lv.{upcoming['min_level']}）" if upcoming else "全部形态已解锁"),
            bg="#213242", fg="#d9a85c", font=("Microsoft YaHei UI", 9),
        ).pack(anchor="w", pady=(4, 0))
        today = summary["today"]
        tk.Label(
            body,
            text=(
                f"今日获得：活动 {today.get('activity_xp', 0)} XP · "
                f"必做 {today.get('mission_xp', 0)} XP · 摸头 {today.get('pat_xp', 0)} XP"
            ),
            bg="#213242", fg="#b9c7d4", font=("Microsoft YaHei UI", 9),
        ).pack(anchor="w", pady=(8, 0))
        today_count, active_days, streak = _activity_stats()
        tk.Label(
            body,
            text=(
                f"今日活动 {today_count} 条 · 近 7 天活跃 {active_days} 天 · "
                f"连续 {streak} 天 · 累计摸头 {pats} 次"
            ),
            bg="#213242", fg="#b9c7d4", font=("Microsoft YaHei UI", 9),
            wraplength=300, justify="left",
        ).pack(anchor="w", pady=(4, 0))
        tk.Label(
            body, text="形态图鉴", bg="#213242", fg="#d9a85c",
            font=("Microsoft YaHei UI", 10, "bold"),
        ).pack(anchor="w", pady=(10, 2))
        gallery = tk.Frame(body, bg="#213242")
        gallery.pack(anchor="w")
        for item in form_gallery(progress.level):
            if item["current"]:
                mark, color, suffix = "●", "#d9a85c", " · 当前"
            elif item["unlocked"]:
                mark, color, suffix = "○", "#fff4d2", ""
            else:
                mark, color, suffix = "·", "#6f7f8e", "（未解锁）"
            tk.Label(
                gallery,
                text=f"{mark} Lv.{item['min_level']} {item['name']}{suffix}",
                bg="#213242", fg=color, font=("Microsoft YaHei UI", 9),
                anchor="w",
            ).pack(anchor="w")
        tk.Label(
            body, text="经验规则\n" + "\n".join(rules_text()),
            bg="#213242", fg="#b9c7d4", font=("Microsoft YaHei UI", 9),
            justify="left", wraplength=300,
        ).pack(anchor="w", pady=(10, 6))
        tk.Button(
            body, text="关闭", command=window.destroy,
            bg="#d9a85c", fg="#2b2016", relief="flat", padx=12, pady=4,
        ).pack(anchor="e", pady=(8, 0))
        window.update_idletasks()
        width, height = window.winfo_reqwidth(), window.winfo_reqheight()
        x = max(0, min(root.winfo_x() + root.winfo_width() - width, root.winfo_screenwidth() - width))
        y = root.winfo_y() + root.winfo_height() + 6
        if y + height > root.winfo_screenheight():
            y = max(0, root.winfo_y() - height - 6)
        window.geometry(f"+{x}+{y}")

    def show_hint(_event=None) -> None:
        summary = describe(progress)
        if summary["next_level_xp"]:
            exp = f"XP {summary['xp']}/{summary['next_level_xp']}"
        else:
            exp = f"XP {summary['xp']} · 满级"
        _speak(
            f"灵知 · Lv.{summary['level']} {summary['form']['name']} · {exp}\n"
            f"{mood_line(mood)}"
        )

    def pat(_event=None) -> None:
        nonlocal pats, squish_until, mood, progress, form
        pats += 1
        progress, events = record_pat(
            progress, day=datetime.now().strftime("%Y-%m-%d")
        )
        updated_form = form_for_level(progress.level)
        if updated_form["key"] != form["key"]:
            form = updated_form
        settings = _load_settings()
        settings["pats"] = pats
        settings["progression"] = progress.to_dict()
        _save_settings(settings)
        squish_until = datetime.now().timestamp() + 0.24
        render(animate=False, squish=True)
        mood = detect_mood()
        level_events = [event for event in events if event.get("type") == "level"]
        if level_events:
            _announce(events)
        else:
            line = PAT_EGG if pats % 7 == 0 else random.choice(PAT_LINES)
            _speak(f"{MOOD_EMOJI.get(mood, '🌿')} {line}", 3200)

    def chatter() -> None:
        if (
            not muted.get()
            and not dragging
            and datetime.now().timestamp() >= quiet_until
        ):
            if random.random() < 0.35:
                show_hint()
            else:
                _speak(f"{MOOD_EMOJI.get(mood, '🌿')} {random.choice(PET_LINES)}", 4600)
        root.after(random.randint(90_000, 150_000), chatter)

    def resize(delta: int) -> None:
        nonlocal desired_size
        desired_size = max(MIN_SIZE, min(MAX_SIZE, desired_size + delta))
        settings = _load_settings()
        settings["size"] = desired_size
        settings["appearance"] = "3d"
        _save_settings(settings)
        render(animate=False)
        _speak(f"灵知 · 当前大小 {desired_size}px", 1600)

    def toggle_mute() -> None:
        muted.set(not muted.get())
        settings = _load_settings()
        settings["muted"] = muted.get()
        _save_settings(settings)
        _speak("好的，我会安静待着。" if muted.get() else "灵知回来啦！", 2600)

    def wheel(event) -> None:
        resize(16 if event.delta > 0 else -16)

    def _remember_position() -> None:
        current = _load_settings()
        current["pos"] = [root.winfo_x(), root.winfo_y()]
        _save_settings(current)

    def drag_start(event) -> None:
        nonlocal dragging, moved, offset_x, offset_y, press_root
        dragging = True
        moved = False
        hint.withdraw()
        press_root = (event.x_root, event.y_root)
        offset_x = event.x_root - root.winfo_x()
        offset_y = event.y_root - root.winfo_y()

    def drag_move(event) -> None:
        nonlocal moved
        if not dragging or locked.get():
            return
        if abs(event.x_root - press_root[0]) + abs(event.y_root - press_root[1]) > 5:
            moved = True
        root.geometry(f"+{event.x_root - offset_x}+{event.y_root - offset_y}")

    def drag_end(_event) -> None:
        nonlocal dragging
        dragging = False
        if moved:
            _remember_position()
        else:
            show_hint()

    def toggle_lock() -> None:
        current = _load_settings()
        current["locked"] = locked.get()
        _save_settings(current)
        _speak("位置已锁定，不会再被拖动。" if locked.get() else "位置已解锁，可以拖动了。", 2400)

    def quiet_for(minutes: int) -> None:
        nonlocal quiet_until
        quiet_until = datetime.now().timestamp() + minutes * 60
        _speak(f"好，我安静 {minutes} 分钟。", 2400)

    def back_to_corner() -> None:
        width = max(image_label.winfo_width(), desired_size)
        height = max(image_label.winfo_height(), desired_size)
        x, y = _clamp_position(
            root.winfo_screenwidth() - width - 36,
            root.winfo_screenheight() - height - 64,
            width,
            height,
            root.winfo_screenwidth(),
            root.winfo_screenheight(),
        )
        root.geometry(f"+{x}+{y}")
        _remember_position()
        _speak("灵知回到右下角了。", 2000)

    def popup(event) -> None:
        menu.tk_popup(event.x_root, event.y_root)

    menu.add_command(label="灵知成长", command=show_growth)
    menu.add_separator()
    menu.add_command(label="查看今日进度", command=show_hint)
    menu.add_command(label="给我一句研究提示", command=lambda: _speak(f"💡 {random.choice(PET_LINES)}"))
    menu.add_command(label="摸摸头", command=pat)
    menu.add_separator()
    menu.add_checkbutton(label="锁定位置（防误拖）", variable=locked, command=toggle_lock)
    menu.add_command(label="安静 30 分钟", command=lambda: quiet_for(30))
    menu.add_checkbutton(label="安静模式（不主动说话）", variable=muted, command=toggle_mute)
    menu.add_separator()
    menu.add_command(label="放大", command=lambda: resize(16))
    menu.add_command(label="缩小", command=lambda: resize(-16))
    menu.add_command(label="恢复小尺寸", command=lambda: resize(DEFAULT_SIZE - desired_size))
    menu.add_command(label="回到右下角", command=back_to_corner)
    menu.add_separator()
    menu.add_command(label="关闭灵知", command=root.destroy)
    image_label.bind("<Double-Button-1>", pat)
    image_label.bind("<ButtonPress-1>", drag_start, add="+")
    image_label.bind("<B1-Motion>", drag_move)
    image_label.bind("<ButtonRelease-1>", drag_end)
    image_label.bind("<MouseWheel>", wheel)
    image_label.bind("<Button-4>", lambda _event: resize(16))
    image_label.bind("<Button-5>", lambda _event: resize(-16))
    image_label.bind("<Button-3>", popup)
    root.bind("<Escape>", lambda _event: root.destroy())
    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()
    window_width = desired_size
    window_height = round(desired_size * original.height / max(original.width, 1))
    saved_pos = settings.get("pos")
    if isinstance(saved_pos, list) and len(saved_pos) == 2:
        start_x, start_y = int(saved_pos[0]), int(saved_pos[1])
    else:
        start_x = screen_width - window_width - 36
        start_y = screen_height - window_height - 64
    start_x, start_y = _clamp_position(
        start_x, start_y, window_width, window_height, screen_width, screen_height
    )
    root.geometry(f"+{start_x}+{start_y}")
    _refresh_menu()
    _announce(pending_events)
    root.after(300_000, sync_progress)
    render(animate=False)
    animate()
    root.after(45_000, chatter)
    root.mainloop()


if __name__ == "__main__":
    run_pet()
