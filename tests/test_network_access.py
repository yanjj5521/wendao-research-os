"""Network discovery and cross-LAN/VPN URL contracts."""

from __future__ import annotations

import http.server
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

from services.network_access import (
    AdapterAddress,
    build_access_urls,
    classify_address,
    http_host_allowed,
    member_access_urls,
    normalize_base_url,
)
from services.sync_backend import validate_hub_url


class _HealthHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        body = b"network-ok"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class NetworkAccessContractTests(unittest.TestCase):
    def test_addresses_are_classified_for_wired_wireless_and_vpn(self) -> None:
        self.assertEqual(classify_address("192.168.1.20", "以太网"), "wired")
        self.assertEqual(classify_address("192.168.1.21", "WLAN 24"), "wireless")
        self.assertEqual(classify_address("10.8.0.2", "WireGuard Tunnel"), "vpn")
        self.assertEqual(classify_address("100.64.0.10", "Tailscale"), "vpn")
        self.assertEqual(classify_address("172.20.0.1", "vEthernet (WSL)"), "virtual")

    def test_member_urls_include_all_active_adapters_in_useful_order(self) -> None:
        rows = build_access_urls(
            (
                AdapterAddress("100.64.0.10", "Tailscale", "vpn"),
                AdapterAddress("192.168.1.21", "WLAN 24", "wireless"),
                AdapterAddress("192.168.1.20", "Ethernet 8", "wired"),
                AdapterAddress("127.0.0.1", "Loopback", "lan"),
            ),
            5050,
        )
        self.assertEqual([row["kind"] for row in rows], ["wired", "wireless", "vpn"])
        self.assertEqual(rows[0]["url"], "http://192.168.1.20:5050")
        self.assertTrue(rows[0]["recommended"])
        self.assertFalse(rows[-1]["recommended"])

    def test_http_private_and_cgnat_hosts_are_allowed_but_public_http_is_not(self) -> None:
        self.assertTrue(http_host_allowed("192.168.1.20"))
        self.assertTrue(http_host_allowed("10.8.0.2"))
        self.assertTrue(http_host_allowed("100.64.0.10"))
        self.assertTrue(http_host_allowed("host.tailnet-example.ts.net"))
        self.assertFalse(http_host_allowed("0.0.0.0"))
        self.assertFalse(http_host_allowed("8.8.8.8"))
        self.assertTrue(validate_hub_url("http://100.64.0.10:5050")[0])
        self.assertFalse(validate_hub_url("http://8.8.8.8:5050")[0])
        self.assertTrue(validate_hub_url("https://hub.example.com")[0])

    def test_base_url_normalization_supports_vpn_and_reverse_proxy_paths(self) -> None:
        normalized, error = normalize_base_url("100.64.0.10:5050")
        self.assertEqual(error, "")
        self.assertEqual(normalized, "http://100.64.0.10:5050")
        normalized, error = normalize_base_url("https://hub.example.com/team/")
        self.assertEqual(error, "")
        self.assertEqual(normalized, "https://hub.example.com/team")
        _normalized, error = normalize_base_url("http://8.8.8.8:5050")
        self.assertIn("HTTPS", error)

    def test_real_non_loopback_listener_is_reachable(self) -> None:
        server = http.server.ThreadingHTTPServer(("0.0.0.0", 0), _HealthHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = int(server.server_address[1])
            rows = member_access_urls(port)
            if not rows:
                self.skipTest("no active non-loopback IPv4 adapter")
            with urllib.request.urlopen(str(rows[0]["url"]) + "/health", timeout=5) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.read(), b"network-ok")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)



class HubHostNetworkLifecycleTests(unittest.TestCase):
    def test_embedded_hub_host_publishes_and_uses_member_addresses(self) -> None:
        root = Path(__file__).resolve().parents[1]
        script = r"""
import os
import socket
import time
import urllib.request
from pathlib import Path

from services import hub_host
from hub.db import connect_hub, get_hub_setting

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])

status = hub_host.start(Path(os.environ["RESEARCH_OS_DATA_DIR"]), port=port)
assert status["running"], status
for _ in range(120):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as response:
            if response.status == 200:
                break
    except Exception:
        time.sleep(0.1)
else:
    raise SystemExit("hub host did not become ready")

status = hub_host.status()
assert status["running"], status
assert status["access_urls"], status
assert any(str(item["url"]).startswith("http://") for item in status["access_urls"])
preferred = str(status["preferred_url"])
with connect_hub() as conn:
    runtime = get_hub_setting(conn, "runtime_base_url")
assert runtime, runtime
with urllib.request.urlopen(preferred + "/health", timeout=5) as response:
    assert response.status == 200
hub_host.stop()
with connect_hub() as conn:
    assert get_hub_setting(conn, "runtime_base_url") == ""
print("hub-host-network-ok")
"""
        with tempfile.TemporaryDirectory(prefix="hub-host-network-") as directory:
            env = os.environ.copy()
            env["RESEARCH_OS_DATA_DIR"] = directory
            env["PYTHONPATH"] = str(root)
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                timeout=120,
            )
            self.assertEqual(result.returncode, 0, result.stderr[-5000:])
            self.assertIn("hub-host-network-ok", result.stdout)


if __name__ == "__main__":
    unittest.main()
