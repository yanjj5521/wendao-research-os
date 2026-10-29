from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from services import github_update


class _Response:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self.headers: dict[str, str] = {}

    def read(self, size: int = -1) -> bytes:
        return self._data

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class _Stream(_Response):
    def __init__(self, data: bytes) -> None:
        super().__init__(data)
        self._sent = False

    def read(self, size: int = -1) -> bytes:
        if self._sent:
            return b""
        self._sent = True
        return self._data


def _payload(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "tag_name": "v9.9.9",
        "name": "test release",
        "body": "notes",
        "html_url": "https://github.com/example/release",
        "published_at": "2026-10-29T00:00:00Z",
        "draft": False,
        "prerelease": False,
        "assets": [
            {
                "name": "WendaoResearch-v9.9.9-User.zip",
                "browser_download_url": github_update.DOWNLOAD_PREFIX
                + "v9.9.9/WendaoResearch-v9.9.9-User.zip",
                "size": 1024,
            },
            {
                "name": "SHA256SUMS.txt",
                "browser_download_url": github_update.DOWNLOAD_PREFIX
                + "v9.9.9/SHA256SUMS.txt",
                "size": 128,
            },
        ],
    }
    data.update(overrides)
    return data


class GithubUpdateTests(unittest.TestCase):
    def test_latest_release_selects_user_package(self) -> None:
        body = json.dumps(_payload()).encode("utf-8")
        with patch.object(github_update, "_open", return_value=_Response(body)):
            release = github_update.latest_release()
        self.assertIsNotNone(release)
        assert release is not None
        self.assertEqual(release["version"], "9.9.9")
        self.assertEqual(release["package_name"], "WendaoResearch-v9.9.9-User.zip")
        self.assertTrue(release["newer"])

    def test_latest_release_skips_prerelease(self) -> None:
        body = json.dumps(_payload(prerelease=True)).encode("utf-8")
        with patch.object(github_update, "_open", return_value=_Response(body)):
            self.assertIsNone(github_update.latest_release())

    def test_latest_release_rejects_foreign_download_host(self) -> None:
        payload = _payload()
        payload["assets"][0]["browser_download_url"] = "https://evil.example/app.zip"  # type: ignore[index]
        with patch.object(github_update, "_open", return_value=_Response(json.dumps(payload).encode())):
            with self.assertRaises(github_update.GithubUpdateError):
                github_update.latest_release()

    def test_checksums_parse_digest_lines(self) -> None:
        digest = "a" * 64
        text = f"{digest}  WendaoResearch-v9.9.9-User.zip\n".encode()
        with patch.object(github_update, "_open", return_value=_Response(text)):
            result = github_update._checksums("https://example/SHA256SUMS.txt")
        self.assertEqual(result["WendaoResearch-v9.9.9-User.zip"], digest)

    def test_validate_user_package_accepts_user_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "user.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("WendaoResearchV3/WendaoResearchV3.exe", b"MZ")
                archive.writestr("WendaoResearchV3/使用说明.txt", "说明")
                archive.writestr("WendaoResearchV3/SHA256SUMS.txt", "sum")
            github_update._validate_user_package(path)

    def test_validate_user_package_rejects_missing_exe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("WendaoResearchV3/使用说明.txt", "说明")
            with self.assertRaises(github_update.GithubUpdateError):
                github_update._validate_user_package(path)

    def test_validate_user_package_rejects_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("../evil.exe", b"MZ")
                archive.writestr("WendaoResearchV3/使用说明.txt", "说明")
            with self.assertRaises(github_update.GithubUpdateError):
                github_update._validate_user_package(path)

    def test_prepare_rejects_hash_mismatch(self) -> None:
        release = {
            "version": "9.9.9",
            "package_url": github_update.DOWNLOAD_PREFIX + "v9.9.9/User.zip",
            "checksum_url": github_update.DOWNLOAD_PREFIX + "v9.9.9/SHA256SUMS.txt",
            "package_name": "User.zip",
        }
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(github_update, "STORAGE_ROOT", Path(tmp)):
                with patch.object(github_update, "_checksums", return_value={"User.zip": "b" * 64}):
                    with patch.object(github_update, "_open", return_value=_Stream(b"payload")):
                        with self.assertRaises(github_update.GithubUpdateError):
                            github_update.prepare_github_update(release)


if __name__ == "__main__":
    unittest.main()
