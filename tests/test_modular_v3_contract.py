from __future__ import annotations

import ast
import copy
import json
import logging
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def route_manifest(paths: list[Path]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if (
                    not isinstance(decorator, ast.Call)
                    or not isinstance(decorator.func, ast.Attribute)
                    or decorator.func.attr not in {"get", "post", "put", "delete", "patch"}
                    or not decorator.args
                    or not isinstance(decorator.args[0], ast.Constant)
                    or not isinstance(decorator.args[0].value, str)
                ):
                    continue
                name = node.name
                for keyword in decorator.keywords:
                    if (
                        keyword.arg == "name"
                        and isinstance(keyword.value, ast.Constant)
                        and isinstance(keyword.value.value, str)
                    ):
                        name = keyword.value.value
                items.append(
                    {
                        "method": decorator.func.attr.upper(),
                        "path": decorator.args[0].value,
                        "name": name,
                    }
                )
    return sorted(items, key=lambda item: (item["path"], item["method"], item["name"]))


class ModularV3ContractTests(unittest.TestCase):
    def test_learning_first_surface_is_present(self) -> None:
        # v3.5 used a frozen pixel-surface manifest, which made intentional
        # usability improvements fail CI.  Keep durable product contracts
        # instead: the learning-first tools must remain reachable and the
        # obsolete ChatGPT jump-out must not reappear.
        for relative in (
            "templates/dashboard.html",
            "templates/workbench.html",
            "templates/literature_report.html",
            "templates/idle.html",
            "static/style.css",
        ):
            self.assertTrue((ROOT / relative).is_file(), relative)
        dashboard = (ROOT / "templates" / "dashboard.html").read_text(encoding="utf-8")
        workbench = (ROOT / "templates" / "workbench.html").read_text(encoding="utf-8")
        idle = (ROOT / "templates" / "idle.html").read_text(encoding="utf-8")
        self.assertIn("literature_report_page", dashboard)
        self.assertIn("英文精读", workbench)
        self.assertIn("多面板图", workbench)
        self.assertIn("我的壁纸", idle)
        self.assertNotIn("打开 ChatGPT", dashboard + workbench)

    def test_default_surface_exposes_every_implemented_feature(self) -> None:
        from core.navigation import (
            DEFAULT_NAV_LAYOUT,
            NAV_GROUP_DEFAULT_ITEMS,
            NAV_ITEM_DEFAULT_VISIBLE,
        )
        from web.runtime import GAMIFICATION_ITEMS

        visible = {
            item["key"]
            for group in DEFAULT_NAV_LAYOUT
            for item in group["items"]
            if item["visible"]
        }
        self.assertEqual(visible, set(NAV_ITEM_DEFAULT_VISIBLE))
        self.assertTrue(all(NAV_ITEM_DEFAULT_VISIBLE.values()))
        for key in (
            "dashboard", "daily", "plans", "review", "literature_report",
            "projects", "experiments", "datasets", "simulations", "library",
            "workspaces", "settings", "workbench", "career", "retreat", "idle",
            "trials", "achievements", "alchemy", "world", "assistant", "online",
        ):
            self.assertIn(key, visible, f"{key} must be on the full default surface")
        self.assertTrue(GAMIFICATION_ITEMS.issubset(visible))
        self.assertEqual(
            NAV_GROUP_DEFAULT_ITEMS["research"],
            ("literature_report", "projects", "experiments", "datasets", "simulations"),
        )
        self.assertIn("trials", NAV_GROUP_DEFAULT_ITEMS["growth"])

    def test_navigation_upgrade_exposes_all_but_preserves_custom_choices(self) -> None:
        from core.database import refresh_positioning_copy
        from core.navigation import DEFAULT_NAV_LAYOUT, PREVIOUS_DEFAULT_NAV_LAYOUT

        def refresh(layout: list[dict]) -> list[dict]:
            connection = sqlite3.connect(":memory:")
            connection.row_factory = sqlite3.Row
            try:
                connection.execute(
                    "CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
                )
                connection.execute(
                    "INSERT INTO settings(key,value) VALUES (?,?)",
                    ("nav_layout", json.dumps(layout)),
                )
                refresh_positioning_copy(connection)
                raw = connection.execute(
                    "SELECT value FROM settings WHERE key='nav_layout'"
                ).fetchone()[0]
                return json.loads(raw)
            finally:
                connection.close()

        self.assertEqual(refresh(copy.deepcopy(PREVIOUS_DEFAULT_NAV_LAYOUT)), DEFAULT_NAV_LAYOUT)

        custom = copy.deepcopy(PREVIOUS_DEFAULT_NAV_LAYOUT)
        custom[0]["items"][0]["visible"] = False
        upgraded_custom = refresh(custom)
        custom_items = {
            item["key"]: item["visible"]
            for group in upgraded_custom
            for item in group["items"]
        }
        self.assertFalse(custom_items["dashboard"])
        self.assertTrue(custom_items["experiments"])
        self.assertTrue(custom_items["simulations"])
        self.assertTrue(custom_items["datasets"])

    def test_learning_domain_modules_exist(self) -> None:
        from services.workspace_profiles import DEFAULT_WORKSPACES, WORKSPACE_MODULES, profile_for

        for module in ("course", "skill"):
            self.assertIn(module, WORKSPACE_MODULES)
            self.assertTrue(profile_for(module)["workflow"])
        keys = {item["workspace_key"] for item in DEFAULT_WORKSPACES}
        self.assertIn("english-lab", keys)
        self.assertTrue(any(item["module"] == "skill" for item in DEFAULT_WORKSPACES))

    def test_v3_route_contract_is_unchanged(self) -> None:
        expected = json.loads(
            (TESTS / "v3_route_manifest.json").read_text(encoding="utf-8")
        )
        paths = sorted((ROOT / "web" / "routes").glob("*.py"))
        paths += sorted((ROOT / "features").glob("*.py"))
        self.assertEqual(route_manifest(paths), expected)

    def test_team_social_routes_and_safety_boundaries_are_present(self) -> None:
        hub = "\n".join((ROOT / rel).read_text(encoding="utf-8") for rel in ("hub/app.py", "hub/social.py", "hub/routes/social.py", "hub/routes/market.py"))
        database = (ROOT / "hub" / "db.py").read_text(encoding="utf-8")
        schema = (ROOT / "core" / "schema" / "hub.py").read_text(encoding="utf-8")
        for marker in (
            'name="hub_forum"',
            'name="hub_chat"',
            'name="hub_market"',
            'name="hub_red_packet_send"',
            'name="hub_trade_accept"',
            "verify_csrf(request, csrf_value)",
            "SOCIAL_TRANSFER_LIMITS",
        ):
            self.assertIn(marker, hub)
        for marker in (
            "CREATE TABLE IF NOT EXISTS hub_forum_posts",
            "CREATE TABLE IF NOT EXISTS hub_chat_messages",
            "CREATE TABLE IF NOT EXISTS hub_social_transfers",
            "CREATE TABLE IF NOT EXISTS accounts",
            "CREATE TABLE IF NOT EXISTS account_credentials",
            "CREATE TABLE IF NOT EXISTS account_profiles",
            "accounts_fts",
        ):
            self.assertIn(marker, schema)
        self.assertIn('"cultivation"', database)
        for template in ("forum.html", "chat.html", "market.html"):
            self.assertTrue((ROOT / "hub_templates" / template).is_file(), template)

    def test_layers_are_reachable_without_compatibility_shims(self) -> None:
        # 1.0 删掉了 app.py / db.py 两个转发层：入口与数据访问各自只有一条路径。
        self.assertFalse((ROOT / "app.py").exists(), "app.py shim must stay deleted")
        self.assertFalse((ROOT / "db.py").exists(), "db.py shim must stay deleted")
        self.assertTrue((ROOT / "web" / "app.py").is_file())
        self.assertTrue((ROOT / "core" / "database.py").is_file())
        self.assertTrue((ROOT / "core" / "accounts.py").is_file())

        route_modules = {path.stem for path in (ROOT / "web" / "routes").glob("*.py")}
        self.assertTrue({"home", "library", "settings", "analysis", "research_tools"}.issubset(route_modules))

        # 不允许任何模块再引用已删除的转发层
        for path in list(ROOT.rglob("*.py")):
            if path.name in {"test_modular_v3_contract.py"}:
                continue
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("from db import", source, f"{path} still imports the deleted db shim")

    def test_no_old_database_compatibility_code_remains(self) -> None:
        checked = [
            path
            for path in [
                ROOT / "run_local.py",
                *sorted((ROOT / "core").glob("*.py")),
                *sorted((ROOT / "core" / "schema").glob("*.py")),
            ]
            # 迁移执行器是唯一合法的 ALTER TABLE 使用方
            if path.name != "migrations.py"
        ]
        forbidden = (
            "ALTER TABLE",
            "_add_column",
            "migrate_legacy_data",
            "migrate_adjacent_legacy_data",
            'INSTANCE_DIR / "research_os.db"',
        )
        combined = "\n".join(path.read_text(encoding="utf-8") for path in checked)
        for marker in forbidden:
            self.assertNotIn(marker, combined)
        self.assertIn('DATABASE_FILENAME = "wendao.db"', combined)
        self.assertIn("SCHEMA_VERSION = 5", combined)
        self.assertIn('PRODUCT_DIR_NAME = "WendaoResearchV3"', combined)

    def test_hub_entrypoint_self_check_uses_current_account_schema(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wendao-hub-entrypoint-test-") as temp_dir:
            environment = dict(os.environ)
            environment["PYTHONPATH"] = str(ROOT)
            result = subprocess.run(
                [sys.executable, "run_hub.py", "--self-check", "--data-dir", temp_dir],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("self-check PASS", result.stdout)

    def test_clean_database_is_seeded_once_and_rejects_old_schema(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wendao-v3-db-test-") as temp_dir:
            environment = dict(os.environ)
            environment["RESEARCH_OS_DATA_DIR"] = temp_dir
            environment["PYTHONPATH"] = str(ROOT)
            script = """
from core.database import DB_PATH, connect, init_db
init_db()
init_db()
with connect() as connection:
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 5
    assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM research_tracks").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM workspaces").fetchone()[0] == 7
    assert connection.execute("SELECT COUNT(*) FROM quests").fetchone()[0] == 6
    assert connection.execute("SELECT value FROM settings WHERE key='site_name'").fetchone()[0] == "问道科研"
print(DB_PATH.name)
"""
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertEqual(result.stdout.strip(), "wendao.db")

        with tempfile.TemporaryDirectory(prefix="wendao-old-db-test-") as temp_dir:
            environment = dict(os.environ)
            environment["RESEARCH_OS_DATA_DIR"] = temp_dir
            environment["PYTHONPATH"] = str(ROOT)
            script = """
import sqlite3
from core.runtime_paths import INSTANCE_DIR
INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
old = INSTANCE_DIR / "wendao.db"
with sqlite3.connect(old) as connection:
    connection.execute("CREATE TABLE old_data(id INTEGER PRIMARY KEY)")
from core.database import init_db
try:
    init_db()
except RuntimeError:
    print("rejected")
else:
    raise SystemExit("old schema was accepted")
"""
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertEqual(result.stdout.strip(), "rejected")

    def test_current_motion_layer_is_isolated(self) -> None:
        base = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
        motion = (ROOT / "static" / "motion-vnext.css").read_text(encoding="utf-8")
        javascript = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("motion-vnext.css", base)
        self.assertIn("vnext-ambient-orbit", base)
        self.assertIn("@keyframes vnext-drift", motion)
        self.assertIn("body.motion-reduced .vnext-ambient", motion)
        self.assertIn("livingScene.addEventListener('pointermove'", javascript)
        self.assertNotIn("requestAnimationFrame", javascript)

    def test_windowed_server_has_no_console_dependency(self) -> None:
        from launchers import server

        with tempfile.TemporaryDirectory(prefix="wendao-v3-log-test-") as temp_dir:
            try:
                with patch.object(server.uvicorn, "run") as run:
                    with patch.object(sys, "stdout", None), patch.object(
                        sys, "stderr", None
                    ):
                        server.run_server(
                            object(),
                            host="127.0.0.1",
                            port=5000,
                            data_root=Path(temp_dir),
                        )
                kwargs = run.call_args.kwargs
                self.assertIsNone(kwargs["log_config"])
                self.assertFalse(kwargs["access_log"])
                self.assertTrue(
                    (Path(temp_dir) / "logs" / "wendao-v3.log").exists()
                )
            finally:
                for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
                    logger = logging.getLogger(name)
                    for handler in logger.handlers[:]:
                        logger.removeHandler(handler)
                        handler.close()

    def test_safe_print_survives_cp1252_console(self) -> None:
        import io

        from run_local import safe_print

        output = io.BytesIO()
        console = io.TextIOWrapper(output, encoding="cp1252", errors="strict")
        with patch.object(sys, "stdout", console):
            safe_print("问道科研 1.2.0 self-check PASS")
            console.flush()
        self.assertEqual(
            output.getvalue().decode("cp1252").strip(),
            r"\u95ee\u9053\u79d1\u7814 1.2.0 self-check PASS",
        )

    def test_packaging_and_ci_use_current_entrypoints(self) -> None:
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        workflow = (
            ROOT / ".github" / "workflows" / "build-v3-modular.yml"
        ).read_text(encoding="utf-8")
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        architecture = (ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")
        version_source = (ROOT / "core" / "version.py").read_text(encoding="utf-8")
        self.assertIn(f"# 问道科研 {version}", readme)
        self.assertIn(f"version = \"{version}\"", pyproject)
        self.assertIn(f"return \"{version}\"", version_source)
        self.assertIn(version, architecture.splitlines()[0])
        self.assertTrue((ROOT / "docs" / "releases" / f"v{version}.md").is_file())
        self.assertIn('include = ["core*", "features*", "hub*", "launchers*", "services*", "web*"]', pyproject)
        self.assertNotIn('"app", "content_library"', pyproject)
        self.assertNotIn('"db", "desktop_pet"', pyproject)
        self.assertNotIn('"hub_app"', pyproject)
        self.assertIn('"share/wendao-research/templates"', pyproject)
        self.assertIn('"share/wendao-research/static/pet"', pyproject)
        self.assertIn("      - main", workflow)
        self.assertIn("compileall -q core features hub", workflow)
        self.assertIn("ruff check core features hub", workflow)
        self.assertNotIn("compileall -q app.py db.py", workflow)
        self.assertNotIn("ruff check app.py db.py", workflow)

    def test_cloud_assistant_sdk_is_pinned_and_rendered_safely(self) -> None:
        workbench = (ROOT / "templates" / "workbench.html").read_text(encoding="utf-8")
        assistant = (ROOT / "static" / "cloud-assistant.js").read_text(encoding="utf-8")
        javascript = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("workbuddy-cloud-sdk@0.1.4-dev.ca27990.202610161713", workbench)
        self.assertNotIn("workbuddy-cloud-sdk@dev", workbench)
        self.assertIn("integrity=\"sha384-", workbench)
        self.assertIn('crossorigin="anonymous"', workbench)
        self.assertIn('referrerpolicy="no-referrer"', workbench)
        self.assertIn("粘贴的内容会发送到云端服务", workbench)
        self.assertNotIn("innerHTML", assistant)
        self.assertIn("textContent", assistant)
        self.assertNotIn("preview.innerHTML", javascript)
        self.assertIn("preview.replaceChildren", javascript)

    def test_release_shell_is_single_program_and_clean_identity(self) -> None:
        version = (ROOT / "VERSION").read_text().strip()
        spec = (ROOT / "packaging" / "WendaoResearchV3.spec").read_text(
            encoding="utf-8"
        )
        build = (ROOT / "tools" / "build.py").read_text(encoding="utf-8")
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        installer = (ROOT / "packaging" / "windows" / "install_windows.ps1").read_text(
            encoding="utf-8"
        )
        workflow = (
            ROOT / ".github" / "workflows" / "build-v3-modular.yml"
        ).read_text(encoding="utf-8")
        android = (ROOT / "mobile" / "android" / "app" / "build.gradle").read_text(
            encoding="utf-8"
        )
        self.assertIn('name="WendaoResearchV3"', spec)
        self.assertIn("console=False", spec)
        self.assertIn('"portable.flag", "使用说明.txt"', build)
        self.assertNotIn('"启动团队中心.cmd", portable_root', build)
        self.assertIn("Team-Update.zip", build)
        self.assertIn('"pywebview>=5.4,<6"', pyproject)
        self.assertIn('Join-Path $env:LOCALAPPDATA "Programs\\WendaoResearchV3"', installer)
        self.assertIn('Join-Path ([Environment]::GetFolderPath("Desktop")) "问道科研.lnk"', installer)
        self.assertIn('applicationId "cn.wendao.research.v3.mobile"', android)
        self.assertIn("python tools/build.py --assemble --output out", workflow)
        self.assertIn('$PSNativeCommandUseErrorActionPreference = $true', workflow)
        self.assertIn("Start-Process -FilePath $exe", workflow)
        self.assertIn('"--mode", "team"', workflow)
        self.assertIn("motion-vnext.css", workflow)
        self.assertIn("cn.wendao.research.v3.mobile", workflow)
        self.assertIn("Team-Update.zip", workflow)
        self.assertEqual(version, "1.3.7")
        for obsolete in (
            ".github/workflows/release-windows.yml",
            ".github/workflows/validate-development.yml",
            "Restore_Data.cmd",
            "Safe_Update_From_Zip.cmd",
            "restore_data.py",
            "safe_update.py",
            "migration_test.py",
            "packaging/ResearchOS.spec",
            "packaging/ResearchHub.spec",
            "packaging/windows/迁移旧版数据.cmd",
            "Start_Research_OS.cmd",
            "Start_Shared_Hub.cmd",
            "Install_Windows.cmd",
            "Create_Desktop_Shortcut.cmd",
            "Developer_Run_Tests.cmd",
            "Reset_Local_Environment.cmd",
            "packaging/windows/启动团队中心.cmd",
        ):
            self.assertFalse((ROOT / obsolete).exists())

    def test_ci_checks_only_existing_entry_files(self) -> None:
        workflow = (
            ROOT / ".github" / "workflows" / "build-v3-modular.yml"
        ).read_text(encoding="utf-8")
        command = next(
            line for line in workflow.splitlines() if "python -m ruff check" in line
        )
        targets = command.split("ruff check", 1)[1].split()
        self.assertTrue(targets)
        for target in targets:
            self.assertTrue(
                (ROOT / target).exists(),
                f"CI references a path that no longer exists: {target}",
            )

    def test_navigation_keeps_scroll_position(self) -> None:
        script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        for marker in (
            "scrollRestoration",
            "NAV_SCROLL_KEY",
            "SIDEBAR_SCROLL_KEY",
            "window.scrollTo(0, Math.min",
            "addEventListener('submit'",
            "rememberScroll(null)",
            "rememberScroll(url.pathname + url.search)",
        ):
            self.assertIn(marker, script)
        base = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
        self.assertIn('data-no-scroll-restore="1"', base)

    def test_hub_entry_offers_return_and_first_run_credentials(self) -> None:
        login = (ROOT / "hub_templates" / "login.html").read_text(encoding="utf-8")
        base = (ROOT / "hub_templates" / "base.html").read_text(encoding="utf-8")
        css = (ROOT / "hub_static" / "system-entry.css").read_text(encoding="utf-8")
        self.assertIn("返回系统界面", base)
        self.assertIn("system_home", base)
        self.assertIn("管理员首次登录凭据", login)
        self.assertIn("initial_admin", login)
        self.assertIn(".admin-first-run", css)

    def test_experiment_ledger_is_generic(self) -> None:
        template = (ROOT / "templates" / "experiments.html").read_text(encoding="utf-8")
        route = (ROOT / "web" / "routes" / "research_tools.py").read_text(encoding="utf-8")
        profiles = (ROOT / "services" / "workspace_profiles.py").read_text(encoding="utf-8")
        for marker in ('name="conditions"', 'name="measurements"', "实验条件", "观测指标"):
            self.assertIn(marker, template)
        for legacy in (
            "eg_content",
            "water_cement_ratio",
            "compaction_pressure",
            "specific_capacitance",
            "conductivity",
        ):
            self.assertNotIn(legacy, template)
            self.assertNotIn(legacy, route)
        self.assertIn('"通用DIY实验"', profiles)
        self.assertNotIn('"EG 实验"', profiles)

    def test_single_executable_entry_replaces_every_cmd(self) -> None:
        from launchers.cli import COMMANDS

        cli = (ROOT / "launchers" / "cli.py").read_text(encoding="utf-8")
        entry = (ROOT / "run_local.py").read_text(encoding="utf-8")
        spec = (ROOT / "packaging" / "WendaoResearchV3.spec").read_text(encoding="utf-8")
        self.assertEqual(
            set(COMMANDS),
            {"install", "shortcut", "diagnose", "version", "reset-env", "help"},
        )
        for command in ("install", "shortcut", "diagnose", "version", "reset-env"):
            self.assertIn(f'"{command}"', cli, f"{command} must be a subcommand")
        self.assertIn("dispatch_if_command", entry)
        self.assertIn("install_windows.ps1", spec)
        self.assertIn('"packaging/windows"', spec)


if __name__ == "__main__":
    unittest.main()
