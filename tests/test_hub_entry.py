from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from hub import common


def _request(host: str) -> types.SimpleNamespace:
    return types.SimpleNamespace(client=types.SimpleNamespace(host=host))


class InitialAdminHintTests(unittest.TestCase):
    def _credential_file(self) -> Path:
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".txt", delete=False, encoding="utf-8"
        )
        handle.write("用户名: admin\n密码: secret123\nAPI Token: token\n")
        handle.close()
        return Path(handle.name)

    def test_remote_client_never_sees_the_credential(self) -> None:
        path = self._credential_file()
        try:
            with patch.object(common, "HUB_ADMIN_PATH", path):
                self.assertIsNone(common.initial_admin_hint(_request("10.0.0.8")))
        finally:
            path.unlink()

    def test_loopback_reads_a_verified_credential(self) -> None:
        path = self._credential_file()
        connection = MagicMock()
        manager = MagicMock()
        manager.__enter__.return_value = connection
        try:
            with patch.object(common, "HUB_ADMIN_PATH", path):
                with patch.object(common, "connect_hub", return_value=manager):
                    with patch.object(
                        common.accounts_repo, "authenticate", return_value={"id": 1}
                    ):
                        hint = common.initial_admin_hint(_request("127.0.0.1"))
        finally:
            path.unlink()
        self.assertEqual(hint, {"username": "admin", "password": "secret123"})

    def test_stale_file_is_not_shown(self) -> None:
        path = self._credential_file()
        connection = MagicMock()
        manager = MagicMock()
        manager.__enter__.return_value = connection
        try:
            with patch.object(common, "HUB_ADMIN_PATH", path):
                with patch.object(common, "connect_hub", return_value=manager):
                    with patch.object(
                        common.accounts_repo, "authenticate", return_value=None
                    ):
                        self.assertIsNone(
                            common.initial_admin_hint(_request("127.0.0.1"))
                        )
        finally:
            path.unlink()

    def test_missing_file_is_not_shown(self) -> None:
        with patch.object(common, "HUB_ADMIN_PATH", Path("no-such-credential.txt")):
            self.assertIsNone(common.initial_admin_hint(_request("127.0.0.1")))


if __name__ == "__main__":
    unittest.main()
