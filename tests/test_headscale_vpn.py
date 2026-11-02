"""Headscale configuration validation contracts."""

from __future__ import annotations

import unittest

from services import headscale_vpn


class HeadscaleConfigTests(unittest.TestCase):
    def test_control_url_allows_https_and_private_http_only(self) -> None:
        normalized, error = headscale_vpn._normalize_control_url("https://headscale.example.com/")
        self.assertEqual(error, "")
        self.assertEqual(normalized, "https://headscale.example.com")
        normalized, error = headscale_vpn._normalize_control_url("http://127.0.0.1:18080")
        self.assertEqual(error, "")
        self.assertEqual(normalized, "http://127.0.0.1:18080")
        _normalized, error = headscale_vpn._normalize_control_url("http://8.8.8.8:18080")
        self.assertIn("HTTPS", error)

    def test_peer_accepts_ip_hostname_and_rejects_paths(self) -> None:
        self.assertEqual(headscale_vpn._normalize_peer("100.64.0.10")[0], "100.64.0.10")
        self.assertEqual(headscale_vpn._normalize_peer("http://wendao-hub.tailnet.ts.net/")[0], "wendao-hub.tailnet.ts.net")
        _peer, error = headscale_vpn._normalize_peer("https://host/path")
        self.assertIn("只能", error)

    def test_hostname_is_normalized(self) -> None:
        self.assertEqual(headscale_vpn._normalize_hostname(" Wendao Hub! "), "wendao-hub")
        self.assertEqual(headscale_vpn._normalize_hostname("***"), "wendao-node")


if __name__ == "__main__":
    unittest.main()
