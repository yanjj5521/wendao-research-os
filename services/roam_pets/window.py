"""桌面窗口：透明 Tk 窗口渲染当前宠物的待机动画与交互。

契约（沿用灵知桌宠）：
- 窗口背景 = TRANSPARENT_KEY（wm_attributes transparentcolor），绘制内容不得使用该键色；
- 说话使用独立 Toplevel（自适应宽度、出现在宠物上方、自动关闭）；
- 可拖动、位置有屏幕夹紧、退出时保存位置。
"""
from __future__ import annotations

import argparse
import tkinter as tk

from . import service, storage
from .catalog import SPECIES, get
from .frames import TRANSPARENT_KEY, build_frame

WIDTH, HEIGHT = 240, 250


class RoamPetWindow:
    def __init__(self, species_id: str | None = None) -> None:
        self.data = storage.load()
        self.data = service.tick(self.data)
        self.species_id = species_id or self.data.get("active") or next(iter(self.data["pets"]), "emberpup")
        if self.species_id not in SPECIES:
            self.species_id = "emberpup"
        self.species = get(self.species_id)  # type: ignore[arg-type]
        self.quiet = False
        self._speech: tk.Toplevel | None = None
        self._t = 0.0

        self.root = tk.Tk()
        self.root.title("漫游宠物")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        try:
            self.root.wm_attributes("-transparentcolor", TRANSPARENT_KEY)
        except tk.TclError:
            pass
        self.canvas = tk.Canvas(self.root, width=WIDTH, height=HEIGHT, bg=TRANSPARENT_KEY, highlightthickness=0)
        self.canvas.pack()
        self._place_initial()
        self._bind()
        self._tick()

    # ---- 位置 ----
    def _place_initial(self) -> None:
        pos = self.data.get("window_pos", {}).get(self.species_id) or [60, 160]
        x, y = self._clamp(int(pos[0]), int(pos[1]))
        self.root.geometry(f"+{x}+{y}")

    def _clamp(self, x: int, y: int) -> tuple[int, int]:
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = max(0, min(int(x), screen_w - WIDTH))
        y = max(0, min(int(y), screen_h - HEIGHT))
        return x, y

    def _save_pos(self) -> None:
        x, y = self._clamp(self.root.winfo_x(), self.root.winfo_y())
        self.data.setdefault("window_pos", {})[self.species_id] = [x, y]
        storage.save(self.data)

    # ---- 交互 ----
    def _bind(self) -> None:
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Double-Button-1>", self._on_double)
        self.canvas.bind("<Button-3>", self._on_menu)
        self.canvas.bind("<ButtonPress-1>", self._on_press_start)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="喂食", command=self._feed)
        self.menu.add_command(label="玩耍", command=self._play)
        self.menu.add_command(label="切换宠物", command=self._switch)
        self.menu.add_command(label="安静模式", command=self._toggle_quiet)
        self.menu.add_separator()
        self.menu.add_command(label="收回（退出）", command=self._quit)
        self._press = (0, 0)

    def _on_press_start(self, event) -> None:
        self._press = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())

    def _on_drag(self, event) -> None:
        x, y = self._clamp(event.x_root - self._press[0], event.y_root - self._press[1])
        self.root.geometry(f"+{x}+{y}")

    def _on_click(self, _event) -> None:
        state = self.data["pets"].get(self.species_id)
        mood = service.mood_of(state) if state else "平静"
        if not self.quiet:
            self._speak(service.speak_line(self.species_id, mood))
        else:
            self._speak("（安静模式）")

    def _on_double(self, _event) -> None:
        # 双击摸头：走好感度通道，不消耗饥饿
        state = self.data["pets"].get(self.species_id)
        if state is not None:
            state["bond"] = min(100, int(state.get("bond", 0)) + 1)
            storage.save(self.data)
        self._speak("（开心地蹭了蹭你）")

    def _on_menu(self, event) -> None:
        self.menu.tk_popup(event.x_root, event.y_root)

    def _feed(self) -> None:
        try:
            self.data = service.feed(self.data, self.species_id)
            storage.save(self.data)
            self._speak("唔——好吃！")
        except ValueError as exc:
            self._speak(str(exc))

    def _play(self) -> None:
        try:
            self.data = service.play(self.data, self.species_id)
            storage.save(self.data)
            self._speak("再玩一次嘛！")
        except ValueError as exc:
            self._speak(str(exc))

    def _switch(self) -> None:
        owned = list(self.data["pets"].keys())
        if len(owned) < 2:
            self._speak("还没有其他伙伴，去宠物乐园领养吧。")
            return
        index = owned.index(self.species_id)
        self.species_id = owned[(index + 1) % len(owned)]
        self.species = get(self.species_id)  # type: ignore[arg-type]
        self.data["active"] = self.species_id
        self._save_pos()
        self._speak(f"{self.species.name} 来啦！")

    def _toggle_quiet(self) -> None:
        self.quiet = not self.quiet
        self.menu.entryconfig(3, label="退出安静模式" if self.quiet else "安静模式")

    def _quit(self) -> None:
        self._save_pos()
        self.root.destroy()

    # ---- 说话（独立 Toplevel，沿灵知契约） ----
    def _speak(self, text: str) -> None:
        if self._speech is not None and self._speech.winfo_exists():
            self._speech.destroy()
        x, y = self.root.winfo_x(), self.root.winfo_y()
        top = tk.Toplevel(self.root)
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        try:
            top.wm_attributes("-transparentcolor", TRANSPARENT_KEY)
        except tk.TclError:
            pass
        frame = tk.Frame(top, bg="#fffdf6", highlightbackground="#c9a06a", highlightthickness=2)
        frame.pack()
        tk.Label(frame, text=text, bg="#fffdf6", fg="#4a3a2c",
                 font=("Microsoft YaHei", 10), wraplength=230, justify="left", padx=10, pady=8).pack()
        top.update_idletasks()
        sx = max(0, min(x + WIDTH // 2 - top.winfo_width() // 2, top.winfo_screenwidth() - top.winfo_width()))
        sy = max(0, y - top.winfo_height() - 6)
        top.geometry(f"+{sx}+{sy}")
        top.after(3200, top.destroy)
        self._speech = top

    # ---- 动画循环 ----
    def _tick(self) -> None:
        self._t += 0.08
        state = self.data["pets"].get(self.species_id)
        mood = service.mood_of(state) if state else "平静"
        self.canvas.delete("all")
        for item in build_frame(self.species, self._t, mood):
            if item["kind"] == "oval":
                self.canvas.create_oval(item["x1"], item["y1"], item["x2"], item["y2"],
                                        fill=item["fill"], outline=item["outline"], width=item["width"])
            elif item["kind"] == "polygon":
                self.canvas.create_polygon(item["points"], fill=item["fill"],
                                           outline=item["outline"], width=item["width"])
            elif item["kind"] == "line":
                self.canvas.create_line(item["x1"], item["y1"], item["x2"], item["y2"],
                                        fill=item["fill"], width=item["width"], capstyle="round")
            elif item["kind"] == "text":
                self.canvas.create_text(item["x"], item["y"], text=item["text"],
                                        fill=item["fill"], font=("Microsoft YaHei", 11, "bold"))
        self.root.after(80, self._tick)

    def run(self) -> None:
        self.root.mainloop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run-roam-pet", description="唤出漫游桌宠")
    parser.add_argument("--species", default="", help="物种 id（默认用当前跟随的宠物）")
    args = parser.parse_args(argv or [])
    RoamPetWindow(args.species or None).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
