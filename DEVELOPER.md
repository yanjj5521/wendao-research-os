# 开发版入口

开发仓库同时保留个人节点与轻量联机中心，正式用户不需要这些命令。

## Windows 一键调试

- `Start_Research_OS.cmd`：创建/复用 `.venv` 并启动个人节点；
- `Start_Shared_Hub.cmd`：创建/复用 `.venv` 并启动联机中心；
- ``：分别打开两个终端，同时运行个人节点和中心；
- `Developer_Run_Tests.cmd`：运行基础、完整科研链与联机中心测试。

日常使用仍可直接运行 Release 中的 `ResearchOS.exe`。开发版和 EXE 默认使用
不同的数据位置：源码模式数据留在仓库的 `instance/`、`storage/`，
正式 EXE 数据留在当前 Windows 账户的本地应用数据目录。

## 命令行调试

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run_local.py
.\.venv\Scripts\python.exe run_hub.py
```

需要热重载时分别运行：

```powershell
.\.venv\Scripts\python.exe -m uvicorn web.app:app --reload --host 127.0.0.1 --port 5000
.\.venv\Scripts\python.exe -m uvicorn hub.app:app --reload --host 127.0.0.1 --port 5050
```

不要把真实数据库、论文附件、管理员凭据、API Token 或 `.env` 提交到 Git。
