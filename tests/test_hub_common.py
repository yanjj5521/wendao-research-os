from __future__ import annotations

import time
import unittest
from collections import deque

from hub import common


class LoginLimiterTests(unittest.TestCase):
    def setUp(self) -> None:
        common._LOGIN_ATTEMPTS.clear()

    def tearDown(self) -> None:
        common._LOGIN_ATTEMPTS.clear()

    def test_eight_failures_block_until_success_clears(self) -> None:
        for _ in range(8):
            common.record_login_failure("203.0.113.8")
        self.assertFalse(common.login_allowed("203.0.113.8"))
        common.record_login_success("203.0.113.8")
        self.assertTrue(common.login_allowed("203.0.113.8"))
        self.assertNotIn("203.0.113.8", common._LOGIN_ATTEMPTS)

    def test_stale_bucket_is_pruned(self) -> None:
        common._LOGIN_ATTEMPTS["198.51.100.4"] = deque([time.time() - 700])
        self.assertTrue(common.login_allowed("198.51.100.4"))
        self.assertNotIn("198.51.100.4", common._LOGIN_ATTEMPTS)

    def test_unknown_ip_is_not_cached(self) -> None:
        self.assertTrue(common.login_allowed("192.0.2.55"))
        self.assertNotIn("192.0.2.55", common._LOGIN_ATTEMPTS)


if __name__ == "__main__":
    unittest.main()
