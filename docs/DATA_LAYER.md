# 数据层说明（1.0 / schema v2）

## 一、为什么重建

3.x 的库是十几次迭代叠出来的：表按功能追加，字段按页面需要补，没有索引规划。表现是"功能能跑，但数据量一大就慢"，而且账号只有一张 `hub_users` 表，口令、Token、档案、社交资产全塞在一起。

1.0 直接废弃旧库，按**查询路径**重建，而不是按页面重建。

| 项 | 3.x | 1.0 |
| --- | --- | --- |
| 个人库文件 | `wendao-v3-clean.db` | `wendao.db` |
| 版本 | `user_version = 1` | `user_version = 2` + `schema_migrations` 记账 |
| 个人库表数 | 38（含 3 张遗留表） | 39（遗留表保留结构、不再写种子） |
| 个人库索引 | 以唯一约束为主 | 新增 20 条高频查询索引 |
| 中心账号 | 单表 `hub_users` | 身份 / 凭据 / 档案 / 全文索引 四层 |

## 二、账号模型

```
accounts              身份：是谁
├── account_credentials  凭据：怎么证明是本人
├── account_profiles     档案：别人能看到什么
└── accounts_fts         检索：按用户名/昵称找人
```

**为什么拆开**

- 身份是长期稳定的主键。成员改名、换邮箱、停用再启用，`accounts.id` 不变，所有社交表的外键不动。
- 凭据会变。口令迭代次数从 31 万提到 60 万、将来加二次验证或 OAuth，只改 `account_credentials`，身份表零改动。
- 档案会长。简介、技能、主题 JSON、可见性都是"可公开且会膨胀"的内容，单独一张表可以独立备份与限流。

**字段**

| 表 | 关键字段 | 说明 |
| --- | --- | --- |
| `accounts` | `username`(唯一, NOCASE)、`email`(唯一)、`display_name`、`role`、`status`、`created_at`、`last_login_at`、`last_seen_at`、`metadata_json` | `status` 是唯一真相；`active` 是派生列，兼容旧查询 |
| `account_credentials` | `password_algo`、`password_iterations`、`password_salt`、`password_hash`、`api_token`(唯一)、`token_rotated_at` | 算法与迭代次数随行存储，老账号可用原参数校验 |
| `account_profiles` | `title`、`bio`、`skills`、`capabilities`、`goals`、`avatar_symbol`、`theme_json`、`public`、`revision` | `revision` 便于将来做乐观锁 |
| `accounts_fts` | FTS5(username, display_name) | 触发器自动同步，按昵称搜索不走 `LIKE` |

**扩展方式**（不破坏已有数据）

1. 加"所有账号都可能有的字段" → 给 `accounts` 加列，同时把 `SCHEMA_VERSION` 加 1，写一条迁移步骤；
2. 加"少数账号才有的字段" → 写进 `metadata_json`，键名自带命名空间（如 `prefs.timezone`）；
3. 加"会单独查询的扩展属性" → 再建一张 `account_attributes(account_id, key, value)`，不要往身份表里堆列。

## 三、索引与查询计划

个人库新增的索引全部对应真实查询，不做"看起来专业"的冗余索引：

| 场景 | 索引 |
| --- | --- |
| 知识库按状态 + 领域筛选 | `idx_entries_status_workspace`、`idx_entries_kind_status` |
| 最近保存 | `idx_entries_updated` |
| 每日任务按计划 + 天 | `idx_missions_plan_day` |
| 到期复盘 | `idx_review_answers_due` |
| 交付与附件 | `idx_deliveries_mission`、`idx_delivery_files_delivery` |
| 项目闸门 / 推进记录 | `idx_milestones_project`、`idx_project_updates_project` |
| 同步队列积压 | `idx_queue_status` |

中心库账号索引：

| 查询 | 索引 | 计划 |
| --- | --- | --- |
| 按用户名登录 | `sqlite_autoindex_accounts_1` | SEARCH |
| 按 Token 鉴权 | `account_credentials` 唯一索引 | SEARCH（covering） |
| 成员分页 | `idx_accounts_role_created` | SCAN **USING INDEX**，无临时排序 |
| 活跃成员计数 | `idx_accounts_role_status` | covering |
| 昵称搜索 | `accounts_fts` | FTS5 |

> 分页最初写成 `ORDER BY role='admin' DESC, created_at DESC`，SQLite 因此放弃索引、退化为全表扫描（10 万账号 116 ms）。改成 `ORDER BY role, created_at DESC`（`admin` 字母序天然在前）后命中复合索引，降到 3.7 ms。

## 四、10 万账号基准

命令：`python tools/bench_accounts.py --accounts 100000`（只测查询，不测口令哈希的密码学开销）

| 操作 | 耗时 |
| --- | --- |
| 按用户名（唯一索引） | 0.11 ms |
| 按 Token（联表） | 0.04 ms |
| 成员分页 offset=50000 | 3.71 ms |
| 昵称检索（FTS5） | 0.50 ms |
| 活跃成员计数 | 3.75 ms |
| 登录校验（含 PBKDF2 31 万次） | 52.7 ms |

库体积 60 MB。**登录的 52 ms 不是查询慢，是哈希该有的成本**——它同时构成了撞库的速率上限。

## 五、迁移执行器（1.1.0）

从 1.1.0 起，schema 演进不再需要重建库：

| 项 | 做法 |
| --- | --- |
| 版本化 | `Migration(version, note, statements, runner)`，按序执行，记入 `schema_migrations` |
| 快照 | 迁移前用 backup API 复制整库到 `instance/migration_snapshots/`，保留 5 份 |
| 原子性 | 迁移器显式 `BEGIN IMMEDIATE … COMMIT`（sqlite3 驱动对 DDL 不做隐式事务） |
| 失败 | 全部回滚、版本号复位、报错指向快照位置 |

注册新迁移的规则：只追加，不修改已发布的 `Migration`；写错了就追加新版本修正。
测试见 `tests/test_migrations.py`（升级保数据、失败回滚、新旧版本拒收、中心库迁移）。

## 六、规模边界

SQLite 的瓶颈不在读，而在**并发写**：默认单写者。对本系统意味着：

- 读多写少的个人端：几十万条记录、上百个领域都不会明显变慢；
- 中心端：万级账号、每天数千次同步事件仍可承受；
- 若真到"上千人同时在线写"，必须换 PostgreSQL。届时只需替换 `hub_db.connect_hub()` 与 `core/accounts.py` 的 SQL 方言，业务层不动——这也是把账号读写收敛到一个仓储的原因。

**不要**把库放到网盘同步目录，WAL 与多端写入会直接损坏文件。
