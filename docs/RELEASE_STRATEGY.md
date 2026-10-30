# 发布策略与更新机制

> 适用版本：1.3.4 起。仓库 `yanjj5521/wendao-research-os` 为**公开**仓库，
> 因此任何进入 Git 的内容都视为公开内容；凭据、数据库、运行时目录永不入库。

## 一、两类受众

| | 普通用户 | 管理员 / 开发者 |
| --- | --- | --- |
| 使用的程序 | 同一个 `WendaoResearchV3.exe` | 同一个 `WendaoResearchV3.exe` |
| 看到的界面 | 个人端全部功能 + 同行会的成员界面（资料驿站、福利、更新） | 个人端全部功能 + 同行会管理界面（成员、邀请、福利、版本中心、审计） |
| 版本号 | 语义版本，如 `1.3.1` | 同一语义版本，另可看到提交号、构建时间与数据目录 |
| 从哪里取 | GitHub Release 的 **User 包** | GitHub Release 的 **Admin-Dev 包** |
| 怎么更新 | 应用内“检查更新”，或替换 EXE | 上传 `Team-Update.zip` 到版本中心；自己同样走应用内更新 |

界面差异只来自登录身份（`hub` 的角色校验），**不是两个程序、不是两个版本**。
管理端页面在 `/admin`，成员访问会被拒绝；普通成员不会看到成员管理、版本上传与审计。

## 二、GitHub 上放什么

一次正式发布 = 一个标签 + 一个 Release + 两类资产。

| 位置 | 内容 | 说明 |
| --- | --- | --- |
| `main` 分支 | 全部源码 | 开发主线，允许领先于已发布标签 |
| 标签 `vX.Y.Z` | 发布点 | 只打在通过测试的提交上 |
| Release 资产 `WendaoResearch-vX.Y.Z-User.zip` | 普通用户包 | 唯一面向用户的下载项 |
| Release 资产 `WendaoResearch-vX.Y.Z-Admin-Dev.zip` | 管理开发包 | 管理员与开发者使用 |
| Release 资产 `SHA256SUMS.txt` | 校验和 | 覆盖上面两个包 |
| Release 说明 | 下载指引 + 变更 + 升级注意 | 必须写清“你是谁 → 下哪个” |
| GitHub 自动源码包 | 由标签生成 | 备用，不承诺可构建 |

**不发布**：`.venv/`、`.runtime/`、`*.db`、会话密钥、`HUB_ADMIN_CREDENTIALS.txt`、
`out/` 中间产物、`deploy-cloud/` 的运行时副本（可用 `tools/prepare_cloud_deploy.py` 重建）、
`.workbuddy/`。

## 三、两个包的内容

### 1. `WendaoResearch-vX.Y.Z-User.zip`（普通用户）

```text
WendaoResearchV3/
  WendaoResearchV3.exe     单文件程序，双击即用
  使用说明.txt              入口、同行会、更新、数据位置
  SHA256SUMS.txt            EXE 与说明的校验和
```

- **不含** `portable.flag`：解压后运行，数据固定在 `%LOCALAPPDATA%\WendaoResearchV3`，
  换版本只替换程序，资料不受影响。
- 也不含源码、构建脚本、测试；用户不需要 Python。

### 2. `WendaoResearch-vX.Y.Z-Admin-Dev.zip`（管理员 / 开发者）

```text
WendaoResearch-vX.Y.Z-Admin/
  README.md                 这份说明的精简版
  ADMIN_GUIDE.md            发布、上传、回滚操作手册（即本文档）
  RELEASE_NOTES.md          本版本变更
  SHA256SUMS.txt
  app/
    Team-Update.zip         直接上传到同行会版本中心
    VERSION
  source/                   该标签的完整源码快照（含 .github、tools、tests、packaging、docs、mobile）
```

- `app/Team-Update.zip` 与应用内更新器要求一致：内含唯一 EXE、`portable.flag`、
  `使用说明.txt`；成员更新时替换 EXE，不动数据。
- `source/` 来自 `git archive <tag>`，只含受版本控制的文件，天然排除数据库与密钥。
- 管理员需要新机器部署时，从同一 Release 再下 User 包即可；两包版本号必然一致。

## 四、更新机制

三条通道，按可用性从高到低：

### 通道 A：同行会中心推送（团队默认）

```text
管理员                         同行会中心                      成员客户端
  │  上传 Team-Update.zip          │                              │
  ├──────────────────────────────▶│ hub_releases 记录            │
  │                               │ version / size / sha256      │
  │                               │                              │
  │                               │◀── GET /api/v1/releases/latest
  │                               │─── 返回最新版本与 sha256 ────▶│
  │                               │◀── GET /api/v1/releases/{id}/download
  │                               │─── .part 下载 ───────────────▶│
  │                               │                              │ 校验 sha256
  │                               │                              │ 校验 ZIP 结构
  │                               │                              │ 备份数据库
  │                               │                              │ 替换 EXE
  │                               │                              │ 启动探活
  │                               │                              │ 失败则回滚旧 EXE
```

关键约束：

- 版本号必须真的变大（`core/version.py::is_newer_version` 做数值比较，`1.3.10 > 1.3.9`）。
- 更新包只替换 EXE；数据库、附件、设置在数据目录中原地保留。
- 下载用 `.part` 落地后原子改名，避免半包被当成完整包。
- 替换前有数据库快照，替换后有一次启动探活；失败自动回滚。

### 通道 B：GitHub Release（单机用户 / 无中心）

- 应用内“更新与同步”提供 **检查 GitHub 新版本**：读取公开仓库的 `releases/latest`，
  只接受同时带 `-User.zip` 与 `SHA256SUMS.txt` 的正式发布（预发布与旧格式会被忽略）。
- 启动后延迟数秒**静默查询一次**，12 小时内最多一次，只取版本号不下载；可在
  「设置 → 版本更新」关闭。发现新版本时侧栏“同行会”旁显示红点，安装仍需用户确认。
- 下载后先比对 `SHA256SUMS.txt` 中的摘要，再校验 ZIP 结构，随后复用与同行会完全相同的
  “备份数据库 → 替换 EXE → 启动探活 → 失败回滚”流程。
- 两条通道共用 `services/download.py`：断点续传（Range）、500 MB 上限、失败保留 `.part`、
  校验失败删除损坏分片。慢网或断网后可重试续传，不必从零开始。
- 全程由用户点击触发，没有后台轮询；查询失败的提示不会影响本地使用。
- 手动更新等价于“替换 EXE”：解压新包，覆盖旧 EXE，数据不动。
- 校验方式：对比 Release 页 `SHA256SUMS.txt`。

### 通道 C：离线分发（无网络）

- 拷贝 User 包或 `Team-Update.zip`；管理员也可用 U 盘把更新包带到内网中心上传。

## 五、发布操作清单

### 开发者

> CI 在每次推送时只做校验与构建（`compileall`、`ruff`、`pytest`、自检、冻结 EXE 冒烟），
> **不创建 Release**；候选预发布只在手动触发 `workflow_dispatch` 时生成，避免污染发布页。

```powershell
# 1. 确认干净、测试通过
git status --short
.\.venv\Scripts\python.exe -m pytest -q

# 2. 升版本：VERSION、pyproject.toml、core/version.py、README、使用说明、测试断言
# 3. 构建三件产物（EXE / Portable / Team-Update）
.\.venv\Scripts\python.exe tools\build.py --assemble --output out

# 4. 生成两个受众包
.\.venv\Scripts\python.exe tools\package_release.py

# 5. 提交并打标签
git add -A; git commit -m "问道科研 X.Y.Z"
git tag -a vX.Y.Z -m "问道科研 X.Y.Z"

# 6. 推送并发布
git push origin main --tags
gh release create vX.Y.Z --title "问道科研 vX.Y.Z" --notes-file docs/releases/vX.Y.Z.md `
  out\WendaoResearch-vX.Y.Z-User.zip `
  out\WendaoResearch-vX.Y.Z-Admin-Dev.zip `
  out\SHA256SUMS.txt
```

### 管理员

1. 从 Release 下载 `Admin-Dev.zip` 并解压。
2. 登录同行会 → 版本中心 → 上传 `app/Team-Update.zip`，版本号填 `X.Y.Z`。
3. 通知成员在“同行会 → 更新与同步”检查更新（或等其手动触发）。
4. 确认至少一台成员机更新后能正常启动；异常按下一节回滚。

## 六、回滚

- **应用内自动回滚**：替换后启动探活失败，会恢复旧 EXE。
- **中心侧回滚**：在版本中心停用出错版本（`hub_releases.active=0`），成员不会再取到它；
  重新上传上一版 `Team-Update.zip` 并填入更小的版本号不会被识别为“更新”，
  因此回滚应直接分发上一版 EXE，或发布一个更高版本号的修复包。
- **数据回滚**：更新前快照位于数据目录 `instance/migration_snapshots/` 与 `storage/autobackups/`。

## 七、版本号规则

- `MAJOR.MINOR.PATCH`，纯数字点分；比较时按数值逐段，`1.3.10 > 1.3.9`。
- 任何改变用户可见行为或入口的提交都必须升版本，否则更新通道不会触发。
- 标签只打一次，不移动已发布的标签；修正版用新版本号。

## 八、云端演示实例

- `deploy-cloud/` 由 `tools/prepare_cloud_deploy.py` 生成，经 WorkBuddy Sites 发布；云端只承载演示数据，不同步任何个人论文、笔记与附件。
- **2026-10-30 已下线**，原分享链接失效，站点根路径返回 404。需要时重新生成并部署，访问口令通过环境变量 `RESEARCH_OS_ACCESS_CODE` 提供，不写入仓库。
- 云端实例不是发布主线：正式分发只走本文件的 GitHub Release 两个包，以及同行会版本中心的更新包。
