# 文件地图（中文导航）

> 看不懂某个文件是干什么的，先查这里。改名或增删文件后请同步更新本表。
> 命名约定：`features/` 页面路由层，`services/` 业务逻辑层，`core/` 内核，`web/` 应用装配，`hub/` 同行会中心，`templates/` 页面模板。

## 根目录（只有入口与桌面层）

| 文件 | 是什么 |
| --- | --- |
| `run_local.py` | **唯一入口**：个人端启动、云端模式、安装/自检/版本等子命令分发 |
| `run_hub.py` | 同行会中心的独立服务入口（开发调试用；正式版用 `--mode team`） |
| `self_test.py` | 个人端自检脚本（基础 / `--integration` 全链路） |
| `hub_self_test.py` | 同行会中心自检脚本 |
| `online_integration_test.py` | 联机链路集成测试脚本 |
| `desktop_shell.py` / `desktop_pet.py` | 桌面窗口与桌宠（仅本地桌面形态使用） |
| `launchers/cli.py` | 统一子命令：`install` / `shortcut` / `diagnose` / `version` / `reset-env` |
| `packaging/windows/install_windows.ps1` | 安装与快捷方式实现（由 `launchers/cli.py` 调用，并随 EXE 内置） |

## core/（内核：数据、迁移、账号、配置）

| 文件 | 是什么 |
| --- | --- |
| `database.py` | 个人库连接、初始化、设置读写 |
| `schema/`（`personal.py` / `hub.py`） | 两套数据库的建表定义（个人端 / 中心端） |
| `migrations.py` | 版本化迁移执行器（快照 + 回滚） |
| `accounts.py` | 同行会账号仓储（注册、登录、Token、分页、检索） |
| `runtime_paths.py` | 数据目录与资源路径推算 |
| `navigation.py` | 侧栏导航标签与布局 |
| `bootstrap.py` | 新库种子数据 |
| `packaged_self_check.py` | 打包自检的具体检查项 |
| `version.py` | 版本号 |

## features/（页面功能层：每个文件对应一个页面族）

| 文件 | 是什么（对应侧栏/页面） |
| --- | --- |
| `daily.py` | 今日学习（每日任务、连续打卡） |
| `review.py` | 温故知新（提取卡片、雷劫试炼） |
| `literature_report.py` | 文献精读（论文汇报生成） |
| `projects.py` | 项目与证据闸门 |
| `career.py` | 成长罗盘（长期阶段） |
| `practical_workbench.py` | 学习工作台（润色、精读训练、图表） |
| `workspaces.py` | 学习领域管理 |
| `assistant.py` | AI 协作台（与 AI 对话、结果回存） |
| `sync.py` | 联机通道页（同步状态、个性化包） |
| `game.py` | 洞府与趣味层（游戏世界、炼丹、休闲空间，三合一） |
| `folders.py` | 交付文件夹路由（配合 foundation 模块） |
| `discover.py` | 发现页路由 |
| `foundation_ui.py` | 科研主线旧页面（交付文件夹的后端） |

## web/（应用装配与基础路由）

| 文件 | 是什么 |
| --- | --- |
| `app.py` | 应用装配：注册全部路由、挂载同行会（/team） |
| `runtime.py` | FastAPI 实例、模板、会话、静态资源挂载 |
| `gate.py` | 云端访问门禁（RESEARCH_OS_ACCESS_CODE） |
| `routes/home.py` | 主页（山门）、双检索、工作台坞区 |
| `routes/library.py` | 知识库（条目增删改查、收藏、导出） |
| `routes/settings.py` | 设置（外观、备份、个性化包） |
| `routes/analysis.py` | 数据分析页 |
| `routes/research_tools.py` | 科研工具页（配比计算等） |
| `routes/foundation.py` | 科研主线旧路由（含文件夹上传） |

## services/（业务逻辑层）

| 文件 | 是什么 |
| --- | --- |
| `plan_import.py` | 近期计划文本导入与导出 |
| `review_engine.py` | 复盘卡片提取引擎 |
| `progression.py` | 境界（39 阶）与修为推算 |
| `career.py` | 生涯节点与阶段推进 |
| `project_progress.py` | 项目闸门推进逻辑 |
| `prompt_builder.py` | AI 提示词组装 |
| `ai_provider.py` | AI 供应商切换（离线 / Ollama / OpenAI） |
| `scholar_search.py` | 学术检索（Crossref 等） |
| `content_library.py` | 知识库内容整理 |
| `workspace_profiles.py` | 七个学习领域的默认配置 |
| `game_world.py` | 洞府建筑与法器数据 |
| `economy.py` | 灵石与修为结算 |
| `idle_space.py` | 休闲空间逻辑 |
| `extractors.py` | PDF / Word / PPT 文本提取 |
| `file_storage.py` / `profile_media.py` | 文件与头像/壁纸存储 |
| `backups.py` | 自动备份任务 |
| `sync_backend.py` / `team_update.py` | 同步事件、版本下载、哈希校验、备份和可回滚原地更新 |
| `hub_host.py` | 把同行会中心作为后台服务启动 |

## hub/（同行会中心：独立数据库、可独立部署）

| 文件 | 是什么 |
| --- | --- |
| `app.py` | 中心应用装配 |
| `common.py` | 会话、安全、上下文、审计 |
| `db.py` | 中心数据库连接与辅助（原 `hub_db.py`） |
| `social.py` | 红包/交换的限额与成员辅助 |
| `sync.py` | 成员节点事件的落库校验 |
| `routes/` | 按领域拆分的路由（system/pages/auth/social/market/me/admin/api） |

## tools/（开发与部署工具）

| 文件 | 是什么 |
| --- | --- |
| `bench_accounts.py` | 账号检索基准（10 万账号压测） |
| `gen_copy_guide.py` | 重新生成界面文案速查 |
| `prepare_cloud_deploy.py` | 构建云端部署目录 |

## 其他目录

| 目录 | 是什么 |
| --- | --- |
| `templates/` / `hub_templates/` | 个人端与中心端页面模板 |
| `static/` / `hub_static/` | 两端静态资源（样式、脚本、壁纸） |
| `launchers/` | 被 run_local / run_hub 复用的服务启动器 |
| `tests/` | pytest 契约与行为测试 |
| `docs/` | 架构、数据层、文案规范、发布说明 |
| `packaging/` | PyInstaller 打包配置 |
| `mobile/` | 安卓客户端源码 |
| `deploy/` | 中心 Docker 部署示例 |
| `assets/` | 桌面图标等静态素材 |
