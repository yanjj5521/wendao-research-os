"""漫游桌宠（洛克王国风格的多宠物系统）。

分层结构，为后续玩法预留扩展点：
- models/catalog：物种定义（外观参数、属性、性格、栖息地）
- storage/service：存档与养成逻辑（喂食/玩耍/衰减/等级），纯函数可测
- capture/maps：捕捉引擎与地图场景（数据与公式已就位，UI 后续接入）
- frames/window：过程化渲染帧 + 透明 Tk 桌面窗口
- web/routes/roam.py + templates/roam.html：宠物乐园页面

视觉契约（沿用灵知桌宠的约定）：窗口背景使用透明键色，绘制内容不得使用该键色；
说话使用独立 Toplevel；所有持久化走原子写入。
"""
