# 问道科研 1.2.0

个人科研工作流与轻量联机中心，使用 FastAPI、Jinja2 和 SQLite。源码模式支持 Windows、Linux、Android 客户端构建与团队中心部署。

## 快速开始

需要 Python 3.11 以上版本。首次启动会自动创建 `.venv` 并安装依赖。

```text
Start_Research_OS.cmd   个人端
Start_Shared_Hub.cmd    轻量联机中心
Developer_Run_Tests.cmd 开发自检
start_linux.sh          Linux 个人端
```

也可以在安装依赖后直接运行：

```powershell
python -m pip install -r requirements.txt
python run_local.py
python run_hub.py
```

## 功能边界

- 今日学习、近期计划、温故知新、文献精读、知识库、领域管理与项目证据链构成默认科研闭环。
- 交付必须有可检查证据；复盘只读取本人交付的关键文本。
- 联机中心只保存账号、公开主页、公开交流与虚拟资产，不同步论文原文件、实验数据或私人附件。
- 趣味层默认关闭；秘境试炼与复习调度保留。
- 迁移执行器在升级数据库前创建快照，失败时整体回滚。

## 数据位置

- 源码模式：项目目录下的 `.runtime/`，运行时自动生成且不应提交。
- Windows EXE：`%LOCALAPPDATA%\WendaoResearchV3`。
- 便携模式：程序旁边的 `user_data/`。
- 联机中心：独立的数据目录，不与个人端混用。

不要把真实数据库、论文附件、管理员凭据、API Token 或 `.env` 提交到 Git。

## 云端部署

公网运行必须同时设置访问口令与 `PORT`；缺少访问口令时程序会拒绝启动，避免误暴露个人科研数据。

```powershell
$env:RESEARCH_OS_ACCESS_CODE = "设置一个高强度随机口令"
$env:PORT = "8080"
python run_local.py --no-browser
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
- [1.2.0 发布说明](docs/releases/v1.2.0.md)
- [规模化同步接口](docs/SCALE_READY_SYNC_INTERFACE.md)

仓库只保留当前源码、运行资源、测试和最新文档；旧发行包、历史版本说明、生成缓存及本地虚拟环境不在版本库内。

## 校验

```powershell
Developer_Run_Tests.cmd
ruff check .
```

Windows 发布程序暂无商业代码签名，首次运行可能出现 SmartScreen；Android 为侧载安装包。

## 文件导航

看不懂某个文件是干什么的？先查 [docs/FILES.md](docs/FILES.md)——每个文件对应哪个功能的中文地图；趣味层的设计约束见 [docs/FUN_LAYER.md](docs/FUN_LAYER.md)——每个文件对应哪个功能的中文地图。
