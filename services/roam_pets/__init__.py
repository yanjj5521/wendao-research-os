"""漫游桌宠（洛克王国风格的多宠物系统）。

分层结构，为后续玩法预留扩展点：
- models/catalog：物种定义（外观参数、属性、性格、栖息地）
- storage/service：带锁事务存档，养成、探索、战斗、捕捉、商店与奖励
- capture/maps：精灵球公式与动态地图遭遇
- frames/window：过程化渲染帧 + 透明 Tk 桌面窗口
- web/routes/roam.py + templates/roam.html：宠物乐园页面

视觉契约（沿用灵知桌宠的约定）：窗口背景使用透明键色，绘制内容不得使用该键色；
说话使用独立 Toplevel；所有持久化走文件锁、原子写入与备份恢复。
"""
