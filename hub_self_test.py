from __future__ import annotations

from fastapi.testclient import TestClient

from hub.app import app as hub_app_app
from hub.common import parse_theme as hub_parse_theme
from hub.db import connect_hub, get_hub_setting, init_hub_db
from core.version import APP_VERSION


def main() -> None:
    init_hub_db()
    client = TestClient(hub_app_app)
    failures: list[str] = []
    for path in (
        "/health",
        "/.well-known/research-cultivation-os",
        "/api/v1/ping",
        "/register",
    ):
        response = client.get(path)
        if response.status_code != 200:
            failures.append(f"{path}: HTTP {response.status_code}")
    mobile_discovery = client.get("/.well-known/research-cultivation-os")
    if mobile_discovery.status_code == 200:
        discovery = mobile_discovery.json()
        if (
            discovery.get("role") != "research_hub"
            or not discovery.get("mobile_client", {}).get("supported")
            or discovery.get("data_policy", {}).get("research_files") != "local_only"
        ):
            failures.append("mobile discovery endpoint reported an unsafe or incomplete contract")
    register_page = client.get("/register")
    if register_page.status_code == 200 and "researchos://connect?hub=" not in register_page.text:
        failures.append("ResearchHub did not expose the Android one-tap pairing link")
    parsed_theme = hub_parse_theme(
        {
            "nav_layout": [
                {
                    "key": "system",
                    "items": [
                        {"key": "online", "visible": True},
                        {"key": "assistant", "visible": False},
                        {"key": "settings", "visible": True},
                    ],
                }
            ]
        }
    )
    if (
        parsed_theme.get("nav_layout", [{}])[0].get("key") != "system"
        or parsed_theme["nav_layout"][0]["items"][1].get("visible") is not False
    ):
        failures.append("hub did not normalize synchronized navigation layout")
    with connect_hub() as conn:
        required = {
            "accounts", "account_profiles", "hub_asset_transactions", "hub_inventory",
            "hub_sync_events", "hub_invites", "hub_releases", "hub_resource_cards", "hub_audit_log",
        }
        found = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = sorted(required - found)
        if missing:
            failures.append("missing tables: " + ", ".join(missing))
        if conn.execute("SELECT COUNT(*) n FROM accounts WHERE role='admin'").fetchone()["n"] != 1:
            failures.append("admin account not initialized")
        if get_hub_setting(conn, "version") != APP_VERSION:
            failures.append(f"hub version was not migrated to {APP_VERSION}")
    if failures:
        print("HUB SELF TEST FAILED")
        for item in failures:
            print("-", item)
        raise SystemExit(1)
    print("HUB SELF TEST PASS")
    print("Health endpoint, registration page and central tables are ready.")


if __name__ == "__main__":
    main()
