## [12:55] - Bug修复: 中心库 v6 迁移 + 启动自愈，修复升级库福利发放

- **文件**: core/schema/hub.py, core/migrations.py, hub/db.py, tests/test_hub_welfare.py
- **决策**: 不依赖版本号判定，`init_hub_db` 每次按 PRAGMA 实际列名补齐福利列
- **验证**: 伪造 11 列 + user_version=5 的库 → 启动后 19 列、版本 6 → 管理员发放返回“福利已入账”；回归用例留仓

## [12:58] - 功能实现: 灵兽袋 6→12，页头显示容量，结契区常驻

- **文件**: services/roam_pets/service.py, web/routes/roam.py, templates/roam.html
- **验证**: tests/test_ui_fixes.py 断言容量与多只收养

## [13:00] - 功能实现: 静修页常驻快捷入口，切换页面不清零

- **文件**: templates/idle.html, static/style.css
- **验证**: 页面含 /daily /plans /review 链接与“可随时切到其他页面”提示

## [13:05] - 重构: 今日学习页排版重做（正向表述、进度前置、交付内联）

- **文件**: templates/daily.html
- **验证**: 页面 200，含“交付打卡/今天怎么样/只想留一句下一步？”，删除劝退式文案

## [13:10] - 重构: AI 协作页字号 15px/行高 1.7，降低密度

- **文件**: templates/assistant.html
- **验证**: pytest 237 passed、ruff PASS、四套自检 PASS、click_audit PASS

## [13:30] - Bug修复: 静修壁纸无法更换（两个单选组互相覆盖）

- **文件**: templates/idle.html, features/game.py
- **根因**: `builtin_wallpaper` 与 `custom_wallpaper` 是两个独立 name，两组都会被提交；
  服务端先处理后者的顺序导致自定义永远覆盖内置
- **决策**: 合并为 `wallpaper=builtin:key|custom:file`，互斥且后选生效；旧字段仅在缺新字段时兜底
- **验证**: 自定义→内置→自定义 三次切换断言；页面含 name="wallpaper"

## [13:35] - 重构: 今日学习与 AI 协作配色（去纯白、暖调分层）

- **文件**: templates/daily.html, templates/assistant.html
- **决策**: 页面级渐变底 + 卡片渐变米白 + 内高光/淡投影，避免整屏 #fff 刺眼
- **验证**: 页面 200，结构断言通过

## [13:38] - Bug修复: “下一步？”展不开（details 换成按钮驱动）

- **文件**: templates/daily.html
- **决策**: `<button aria-expanded>` + `hidden` + 少量 JS，绕开 details 与全局 summary 样式冲突
- **验证**: 断言 toggle/aria/hidden/JS 均存在

## [13:45] - 重构: 导航按类别重组（今日/研究/知识/修行/领域/系统）

- **文件**: core/navigation.py, web/routes/settings.py, templates/settings.html
- **决策**: 只改默认分组，不动用户已保存布局；新增「恢复推荐分类」按钮供主动采纳
- **验证**: 契约测试更新；pytest 238 passed；点击核验全通过

## [14:30] - 功能实现: 同行会显示修为境界

- **文件**: hub/db.py, hub/common.py, hub/routes/{pages,admin}.py, hub_templates/{home,members,me,admin}.html
- **决策**: realm_label 与福利额度共用 REALM_LADDER；展示点四处，纯展示不影响权限
- **验证**: tests/test_reading_and_realm.py::test_hub_realm_display

## [14:32] - 功能实现: 论文三遍读与汇报工作台

- **文件**: services/reading_flow.py, features/reading.py, templates/reading.html, core/schema/personal.py, core/migrations.py
- **决策**: 只做节奏卡点 + 大纲生成 + 问答准备，不做自动读 PDF；数据本机表，个人库 v7
- **验证**: 建档→三遍填写→大纲六段→问答保存→推进遍次，完成度 100%

## [19:00] - Bug修复: GitHub 下载失败（代理 + API digest + 镜像回退）

- **文件**: services/github_update.py, services/download.py, web/routes/settings.py, templates/settings.html
- **决策**: 哈希以 API digest 为准；镜像仅作传输通道，必须过同一 SHA-256；无代理时保持 urlopen 旧路径
- **验证**: 实测 6.1s 下载 73MB，digest c314e283… 与本地一致；tests/test_aux_features.py 覆盖回退

## [19:10] - 功能实现: 39 阶境界名同步至中心

- **文件**: services/hub_realm.py, hub/routes/api.py, hub/db.py, hub/routes/{pages,admin,common}.py, templates/online.html
- **决策**: 复用 accounts.metadata_json 存快照，不新增中心库列；展示优先快照
- **验证**: 推送「金丹三层」后成员页与管理台均显示该名

## [19:20] - 功能实现: PDF 辅助抽取（默认折叠）

- **文件**: services/pdf_sketch.py, features/reading.py, templates/reading.html, core/schema/personal.py, core/migrations.py
- **决策**: 只抽标题/图表标题/结论；扫描件直接拒绝，不 OCR
- **验证**: 路由级抽取后页面显示三项并可「填入」

## [19:25] - 验证: 管理员上传 → 成员安装 全链路

- **文件**: tests/test_release_e2e.py
- **验证**: 真实 HTTP 上传/取包/SHA/结构校验全通过（pytest 244 passed）

## [20:00] - 功能实现: 境界自动同步（联机/立即同步/启动三处 + 6h 节流）

- **文件**: services/hub_realm.py, features/sync.py, web/app.py
- **验证**: tests/test_auto_sync_and_art.py::test_auto_realm_throttle

## [20:05] - 功能实现: 高速下载（测速选线 + 2 路并发分段）

- **文件**: services/download.py, services/github_update.py, services/release_feed.py, services/team_update.py
- **决策**: 2 路并发（实测 13.3MB/s，4 路反而 11.9）；失败回退可续传单连接
- **验证**: tests/test_fast_download.py（Range/无 Range/哈希不符/不可达），真实包 5.2s

## [20:10] - 素材: 炼丹炉/炼器炉/灵兽三张插画并接入四页

- **文件**: static/art/*.webp, templates/{alchemy,world,roam,professions}.html, static/style.css
- **决策**: 裁掉生成图底部 6%（含水印留白），转 WebP 1280 宽
- **验证**: 四个页面引用正确、静态资源 200

## [11:50 11-07] - 重构: 多用户并发稳定性与速度专项（1.12.8）

- **文件**: core/database.py, hub/db.py, hub/routes/{admin,market}.py, hub/app.py, core/schema/hub.py, core/migrations.py
- **决策**: 读连接永不升级为写事务（额度生成按 conn.in_transaction 分流）；资金类路由一律 immediate；
  设置 1s TTL 缓存写穿透；total_xp 用 MAX(id) 守卫
- **验证**: tests/test_concurrency.py（新）+ pytest 251 passed + 四套自检 + click_audit
