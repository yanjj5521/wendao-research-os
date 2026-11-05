# 问道科研 1.12.5

个人科研工作流与内置同行会，使用 FastAPI、Jinja2 和 SQLite。Windows 发布版是单文件桌面 EXE，同行会作为同一个程序内的模块运行，不另开浏览器或第二个程序。

## 快速开始

Windows 只有一个入口：`WendaoResearchV3.exe`。安装、启动、自检都是它的子命令。

```text
WendaoResearchV3.exe              个人端桌面窗口（默认）
WendaoResearchV3.exe --mode team  同行会中心
WendaoResearchV3.exe install      安装到 %LOCALAPPDATA% 并创建快捷方式
WendaoResearchV3.exe shortcut     只重建桌面/开始菜单快捷方式
WendaoResearchV3.exe diagnose     自检并输出诊断信息
WendaoResearchV3.exe version      版本与当前数据目录
```

源码模式需要 Python 3.11 以上版本，首次启动会自动创建 `.venv` 并安装依赖；子命令与 EXE 一致。

```powershell
python -m pip install -r requirements.txt
python run_local.py                 # 个人端
python run_local.py --mode team     # 同行会中心
python run_local.py diagnose        # 自检
python run_local.py install         # 安装已构建的 EXE
```

Linux 使用 `start_linux.sh`。

## Windows 桌面版

```powershell
python tools/build.py --assemble --output out
out\WendaoResearch-v1.10.0-Windows-x64.exe install
```

构建会生成单文件 `WendaoResearchV3.exe`、便携 ZIP 和同行会更新 ZIP。`install` 把程序安装到 `%LOCALAPPDATA%\Programs\WendaoResearchV3`，并创建桌面和开始菜单图标；程序本身仍可直接双击运行。

管理员在同行会“版本中心”上传 `Team-Update.zip` 后，成员可在应用内检查并原地更新。更新前会校验 SHA-256、创建数据库快照并保留旧 EXE；启动失败会自动回滚。更新只替换程序文件，不会覆盖数据目录。

首次启动会识别早期的 `wendao-v3-clean.db`，以 SQLite 一致性备份迁移到当前数据库文件名，并保留原文件。若 5000 已被旧版进程或其他服务占用，程序会通过本机实例身份校验自动选择 5001 起的空闲端口，不会连接到错误的旧数据。

## 功能边界

- 今日学习、近期计划、温故知新、文献精读、知识库、领域管理与项目证据链构成默认科研闭环。
- 实用工作台提供文本、图片与**积分面积计算**：读取 .txt/.csv/.bin 曲线数据，按梯形法或 Simpson 1/3 输出净面积、正负面积、公式与推导。
- 交付必须有可检查证据；复盘只读取本人交付的关键文本。
- 联机中心只保存账号、公开主页、公开交流与虚拟资产，不同步论文原文件、实验数据或私人附件。
- 同行会支持论坛、聊天（即时发送、表情弹层与增量拉取）、定向红包、多人群红包、资产交换与资料卡；论文资料卡按低优先级延迟渲染。
- 同一会话可添加多个同行会账号并在右上角切换；管理员代注册成员后仍保持当前管理员身份，新账号加入切换列表。
- 个人端“同行会”页可启动、停止并打开本机联机中心；自动列出有线、无线、VPN 成员地址，并允许管理员固定 VPN/HTTPS 入口。
- 新增内置 Headscale 用户态虚拟专网：中心端把同行会发布到 Headscale 虚拟 IP，成员端通过程序内本地代理访问，不要求同一局域网或系统级 VPN 网卡。
- 注册邮箱为可选字段；用户名、显示名称、密码和邀请码均有服务端校验；内嵌 `/team` 与个人端会话完全隔离。
- 同行会按身份分流：自己开中心时 `/team` 就是你的中心；连接他人中心时「进入同行会」直接打开对方地址，本机空副本不再误导注册。
- 版本包下载不再依赖浏览器保存框：成员可在「同行会 → 更新与同步」把版本包取到本机并校验 SHA-256；中心端下载支持 `?token=` 链接与数据目录迁移后的路径回退。
- 新增「修仙百艺」：炼丹师 / 炼器师 / 御兽师三职业、六阶等阶，经验只来自真实行动（炼成丹、淬炼法器、灵兽结契）。
- 福利额度改为「管理员境界 × 成员人数」缩放，中心库 v5 迁移补齐福利列，修掉升级后发不出福利的问题。
- 跨网络直连支持一键自动组网与启动自动恢复；中心身份取版本包改为原地定位高亮。
- 同行会数据库 v4 会自动修复旧身份外键；管理员可删除邀请码，所有导航和表单跳转会保留阅读位置。
- 趣味层默认开启、可在设置中关闭；秘境试炼与复习调度保留。
- 宠物乐园提供完整单人循环：初始领养、喂食/玩耍/星莓、四张等级地图、野外战斗、精灵球捕捉、商店补给、队伍切换、图鉴与桌宠陪伴；状态本地保存并支持并发写入保护。
- 迁移执行器在升级数据库前创建快照，失败时整体回滚。

## 数据位置

- 源码模式：项目目录下的 `.runtime/`，运行时自动生成且不应提交。
- Windows EXE：`%LOCALAPPDATA%\WendaoResearchV3`。
- 便携模式：程序旁边的 `user_data/`。
- 联机中心：独立的数据目录，不与个人端混用。
- 管理员凭据：`<数据目录>/instance/HUB_ADMIN_CREDENTIALS.txt`，仅在首次创建 admin 账户时生成，改密后自动销毁；发布包按设计不包含该文件。可用 `WendaoResearchV3.exe admin-path` 查看路径。

数据目录与程序目录分离，是自动更新不丢数据的核心约束。安装、便携运行和原地更新都必须保持这条边界。

不要把真实数据库、论文附件、管理员凭据、API Token 或 `.env` 提交到 Git。

## 云端部署

公网运行必须同时设置访问口令与 `PORT`；缺少访问口令时程序会拒绝启动，避免误暴露个人科研数据。

```powershell
$env:RESEARCH_OS_ACCESS_CODE = "设置一个高强度随机口令"
$env:PORT = "8080"
python run_local.py --serve
```

可选环境变量：

- `RESEARCH_OS_HTTPS_ONLY=1`：强制门禁 Cookie 使用 Secure 标记。
- `RESEARCH_OS_SECRET`：替换个人端会话签名密钥。
- `RESEARCH_OS_MAX_UPLOAD_BYTES`：覆盖默认 1 GiB 的流式上传上限。

## 文档

- [架构说明](ARCHITECTURE.md)
- [开发入口](DEVELOPER.md)
- [1.0 收敛决策](docs/CONVERGENCE_1.0.md)
- [数据层约束](docs/DATA_LAYER.md)
- [发布策略与更新机制](docs/RELEASE_STRATEGY.md)
- [1.12.5 发布说明](docs/releases/v1.12.5.md)
- [1.12.4 发布说明](docs/releases/v1.12.4.md)
- [1.12.3 发布说明](docs/releases/v1.12.3.md)
- [1.12.2 发布说明](docs/releases/v1.12.2.md)
- [1.12.1 发布说明](docs/releases/v1.12.1.md)
- [1.12.0 发布说明](docs/releases/v1.12.0.md)
- [1.11.1 发布说明](docs/releases/v1.11.1.md)
- [1.11.0 发布说明](docs/releases/v1.11.0.md)
- [1.10.0 发布说明](docs/releases/v1.10.0.md)
- [自建 Headscale 虚拟专网指南](docs/HEADSCALE.md)
- [1.8.2 发布说明](docs/releases/v1.8.2.md)
- [同行会网络连接指南](docs/HUB_NETWORK.md)
- [1.8.1 发布说明](docs/releases/v1.8.1.md)
- [1.8.0 发布说明](docs/releases/v1.8.0.md)
- [规模化同步接口](docs/SCALE_READY_SYNC_INTERFACE.md)

仓库只保留当前源码、运行资源、测试和最新文档；旧发行包、历史版本说明、生成缓存及本地虚拟环境不在版本库内。

## 校验

```powershell
python run_local.py diagnose
.\.venv\Scripts\python.exe -m pytest -q
ruff check .
```

Windows 发布程序暂无商业代码签名，首次运行可能出现 SmartScreen；Android 为侧载安装包。

## 文件导航

看不懂某个文件是干什么的？先查 [docs/FILES.md](docs/FILES.md)——每个文件对应哪个功能的中文地图；趣味层的设计约束见 [docs/FUN_LAYER.md](docs/FUN_LAYER.md)——每个文件对应哪个功能的中文地图。
