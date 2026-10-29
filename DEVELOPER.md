# 开发版入口

开发仓库同时保留个人节点与轻量联机中心。正式用户只需要一个 `WendaoResearchV3.exe`，
源码调试使用同一套子命令。

## 一个入口

```text
python run_local.py                 个人端（源码模式）
python run_local.py --mode team     同行会中心
python run_local.py diagnose        内置自检
python run_local.py version         版本与数据目录
python run_local.py install         安装已构建的 EXE
python run_local.py reset-env --yes 删除 .venv（数据库与资料不受影响）
```

打包后的 EXE 使用完全相同的词：

```text
WendaoResearchV3.exe
WendaoResearchV3.exe --mode team
WendaoResearchV3.exe diagnose
WendaoResearchV3.exe install
```

## 数据位置

| 运行方式 | 数据目录 |
| --- | --- |
| 源码模式 | 仓库下 `.runtime/`（可用 `RESEARCH_OS_DATA_DIR` 覆盖） |
| 安装版 EXE | `%LOCALAPPDATA%\WendaoResearchV3` |
| 便携版（同目录有 `portable.flag`） | 程序旁 `user_data/` |

安装与更新只替换程序文件，不迁移也不覆盖数据目录。

## 命令行调试

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run_local.py
```

需要热重载时：

```powershell
.\.venv\Scripts\python.exe -m uvicorn web.app:app --reload --host 127.0.0.1 --port 5000
.\.venv\Scripts\python.exe -m uvicorn hub.app:app --reload --host 127.0.0.1 --port 5050
```

## 校验与构建

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe run_local.py diagnose
ruff check .
.\.venv\Scripts\python.exe tools\build.py --assemble --output out
```

不要把真实数据库、论文附件、管理员凭据、API Token 或 `.env` 提交到 Git。
