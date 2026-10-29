"""访问门禁契约测试（子进程执行：环境变量必须在导入 web.app 前设置）。"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(script: str) -> str:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(ROOT)
    environment["RESEARCH_OS_DATA_DIR"] = tempfile.mkdtemp(prefix="wendao-gate-test-")
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise AssertionError(f"gate script failed:\n{result.stdout}\n{result.stderr}")
    return result.stdout.strip()


def _run_entrypoint(extra_env: dict[str, str], *extra_args: str) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment.update(extra_env)
    environment["PYTHONPATH"] = str(ROOT)
    environment["RESEARCH_OS_DATA_DIR"] = tempfile.mkdtemp(prefix="wendao-gate-entry-")
    return subprocess.run(
        [sys.executable, "run_local.py", "--no-browser", *extra_args],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
    )


NO_GATE = """
from fastapi.testclient import TestClient
from web.app import app

with TestClient(app) as client:
    assert client.get("/").status_code == 200
    assert client.get("/gate").status_code == 404, "未设置口令时不应存在门禁页"
print("no-gate-ok")
"""

GATE_ON = """
from fastapi.testclient import TestClient
from web.app import app

with TestClient(app) as client:
    # 未通过门禁：业务页全部重定向到 /gate
    first = client.get("/", follow_redirects=False)
    assert first.status_code == 303, first.status_code
    assert first.headers["location"].startswith("/gate?next=")
    assert client.get("/team", follow_redirects=False).status_code == 303
    assert client.get("/api/stats", follow_redirects=False).status_code == 303
    assert client.get("/media/note-images/x.png", follow_redirects=False).status_code == 303
    # 门禁页可访问
    gate = client.get("/gate")
    assert gate.status_code == 200 and "问道科研" in gate.text
    assert "default-src 'none'" in gate.headers.get("content-security-policy", "")
    assert gate.headers.get("x-frame-options") == "DENY"
    injected = client.get("/gate", params={"next": '/?x="><img src=x onerror=alert(1)>'})
    assert '<img src=x onerror=alert(1)>' not in injected.text
    assert '&lt;img' in injected.text or '&lt;' in injected.text
    # 错误口令：回到门禁页并提示，不写入有效 Cookie
    wrong = client.post("/gate", data={"code": "wrong-code", "next": "/"}, follow_redirects=True)
    assert "口令不正确" in wrong.text
    assert client.get("/", follow_redirects=False).status_code == 303
    # 正确口令：写入 Cookie 并放行
    ok = client.post(
        "/gate",
        data={"code": "unit-test-code", "next": "/"},
        follow_redirects=False,
    )
    assert ok.status_code == 303, ok.status_code
    assert "research_os_gate" in ok.headers.get("set-cookie", "")
    assert client.get("/").status_code == 200
    assert client.get("/api/stats").status_code == 200
    # Cookie 有效期内再次访问门禁页应直接放行
    assert client.get("/gate", follow_redirects=False).status_code == 303
print("gate-on-ok")
"""


class AccessGateTests(unittest.TestCase):
    def test_gate_is_off_by_default(self) -> None:
        self.assertIn("no-gate-ok", _run(NO_GATE))

    def test_gate_protects_everything_when_enabled(self) -> None:
        script = "import os\nos.environ['RESEARCH_OS_ACCESS_CODE'] = 'unit-test-code'\n" + GATE_ON
        self.assertIn("gate-on-ok", _run(script))

    def test_public_port_requires_access_code(self) -> None:
        # 显式 --serve（云服务模式）必须先设置访问口令，否则拒绝启动。
        result = _run_entrypoint({"PORT": "43210"}, "--serve")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("RESEARCH_OS_ACCESS_CODE", result.stderr + result.stdout)

    def test_stray_port_env_does_not_break_desktop_launch(self) -> None:
        # 机器上无关的 PORT 环境变量不能劫持本地桌面启动（2026-10-28 用户报障）。
        result = _run_entrypoint({"PORT": "43210"}, "--self-check")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
