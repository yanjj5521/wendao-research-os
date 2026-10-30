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
"""
from __future__ import annotations

import base64
import json
import math
import random
import tempfile
import tkinter as tk
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageTk

from core.database import connect
from core.runtime_paths import USER_CONFIG_DIR

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
# 灵知的新背景：一层随心情变色的柔光，加一块贴地的软阴影。
PET_PADDING = 30
AURA_TINT = {
    "happy": (255, 198, 112),
    "proud": (244, 164, 190),
    "sleepy": (128, 154, 208),
    "nudge": (255, 214, 126),
    "calm": (150, 192, 164),
}
_AURA_CACHE: dict[tuple[int, int, str], Image.Image] = {}


def _aura_layer(width: int, height: int, mood: str) -> Image.Image:
    """Cached radial glow plus a soft ground shadow for the pet window."""
    key = (width, height, mood)
    cached = _AURA_CACHE.get(key)
    if cached is not None:
        return cached
    pad = PET_PADDING
    size = (width + pad * 2, height + pad * 2)
    glow = Image.new("L", (160, 160), 0)
    draw = ImageDraw.Draw(glow)
    for radius in range(80, 0, -1):
        alpha = int(78 * (1 - radius / 80) ** 1.7)
        draw.ellipse(
            (80 - radius, 80 - radius, 80 + radius, 80 + radius), fill=alpha
        )
    glow = glow.resize(size, Image.Resampling.LANCZOS)
    aura = Image.new("RGBA", size, AURA_TINT.get(mood, AURA_TINT["calm"]) + (255,))
    aura.putalpha(glow)

    shadow = Image.new("RGBA", size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.ellipse(
        (pad + width * 0.16, pad + height * 0.88, pad + width * 0.84, pad + height * 1.0),
        fill=(58, 42, 32, 88),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(max(4, width // 22)))
    frame = Image.alpha_composite(aura, shadow)
    _AURA_CACHE[key] = frame
    if len(_AURA_CACHE) > 64:
        _AURA_CACHE.clear()
        _AURA_CACHE[key] = frame
    return frame


def _compose_frame(original: Image.Image, width: int, height: int, mood: str) -> Image.Image:
    frame = _aura_layer(width, height, mood).copy()
    pet = _tinted(
        original.resize((width, height), Image.Resampling.LANCZOS), mood
    )
    frame.alpha_composite(pet, (PET_PADDING, PET_PADDING))
    return frame


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
        SETTINGS_PATH.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
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
    transparent = "#ff00ff"
    try:
        root.wm_attributes("-transparentcolor", transparent)
    except tk.TclError:
        transparent = "#f4e7d5"
    root.configure(bg=transparent)

    settings = _load_settings()
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
    hint = tk.Label(
        root, text="", bg="#213242", fg="#fff4d2",
        font=("Microsoft YaHei UI", 9), padx=8, pady=4,
    )
    menu = tk.Menu(root, tearoff=False)

    def _wobble_scale() -> float:
        base = {"happy": 0.045, "proud": 0.035, "nudge": 0.03, "calm": 0.025, "sleepy": 0.014}.get(mood, 0.025)
        speed = {"happy": 0.3, "proud": 0.26, "sleepy": 0.12}.get(mood, 0.22)
        return 1.0 + (base * math.sin(phase * speed / 0.22))

    def render(*, animate: bool = True, squish: bool = False) -> None:
        now = datetime.now().timestamp()
        wobble = _wobble_scale() if animate and not dragging else 1.0
        if squish or now < squish_until:
            wobble *= 1.055
        width = max(MIN_SIZE, round(desired_size * wobble))
        height_factor = 0.96 if (squish or now < squish_until) else 1.0
        height = max(MIN_SIZE, round(original.height * width / max(original.width, 1) * height_factor))
        frame = _compose_frame(original, width, height, mood)
        photo = ImageTk.PhotoImage(frame)
        image_label.configure(image=photo)
        image_label.image = photo

    def animate() -> None:
        nonlocal phase
        phase += 0.22
        render()
        root.after(130, animate)

    def _speak(text: str, seconds: int = 4200) -> None:
        hint.configure(text=text)
        hint.place(x=10, y=max(0, image_label.winfo_height() - 8), anchor="sw")
        root.after(seconds, hint.place_forget)

    def show_hint(_event=None) -> None:
        _speak(f"灵知 · {mood_line(mood)}")

    def pat(_event=None) -> None:
        nonlocal pats, squish_until, mood
        pats += 1
        settings = _load_settings()
        settings["pats"] = pats
        _save_settings(settings)
        squish_until = datetime.now().timestamp() + 0.24
        render(animate=False, squish=True)
        mood = detect_mood()
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
        width = max(image_label.winfo_width(), desired_size + PET_PADDING * 2)
        height = max(image_label.winfo_height(), desired_size + PET_PADDING * 2)
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
    window_width = desired_size + PET_PADDING * 2
    window_height = round(desired_size * original.height / max(original.width, 1)) + PET_PADDING * 2
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
    render(animate=False)
    animate()
    root.after(45_000, chatter)
    root.mainloop()


if __name__ == "__main__":
    run_pet()
