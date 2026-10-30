from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from services import download


class _Response:
    def __init__(self, data: bytes, status: int = 200) -> None:
        self._data = data
        self.status = status
        self.headers: dict[str, str] = {}

    def read(self, size: int = -1) -> bytes:
        if not self._data:
            return b""
        if size is None or size < 0:
            chunk, self._data = self._data, b""
            return chunk
        chunk, self._data = self._data[:size], self._data[size:]
        return chunk

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ResumableDownloadTests(unittest.TestCase):
    def _target(self, tmp: str) -> Path:
        return Path(tmp) / "pkg.zip"

    def test_resumes_from_partial_file(self) -> None:
        payload = bytes(range(256)) * 8
        with tempfile.TemporaryDirectory() as tmp:
            target = self._target(tmp)
            part = target.with_name(target.name + ".part")
            part.write_bytes(payload[:1000])
            target.with_name(target.name + ".part.json").write_text(
                json.dumps({"url": "https://example/pkg.zip", "sha256": _sha(payload)}),
                encoding="utf-8",
            )
            with patch.object(
                download.urllib.request,
                "urlopen",
                return_value=_Response(payload[1000:], status=206),
            ):
                download.fetch_resumable(
                    "https://example/pkg.zip",
                    target,
                    expected_sha256=_sha(payload),
                    max_bytes=1024 * 1024,
                    timeout=10,
                )
            self.assertEqual(target.read_bytes(), payload)
            self.assertFalse(part.exists())
            self.assertFalse(part.with_name(part.name + ".json").exists())

    def test_ignores_range_when_server_sends_whole_file(self) -> None:
        payload = b"y" * 2048
        with tempfile.TemporaryDirectory() as tmp:
            target = self._target(tmp)
            part = target.with_name(target.name + ".part")
            part.write_bytes(payload[:500])
            target.with_name(target.name + ".part.json").write_text(
                json.dumps({"url": "https://example/pkg.zip", "sha256": _sha(payload)}),
                encoding="utf-8",
            )
            with patch.object(
                download.urllib.request,
                "urlopen",
                return_value=_Response(payload, status=200),
            ):
                download.fetch_resumable(
                    "https://example/pkg.zip",
                    target,
                    expected_sha256=_sha(payload),
                    max_bytes=1024 * 1024,
                    timeout=10,
                )
            self.assertEqual(target.read_bytes(), payload)

    def test_discards_part_when_descriptor_hash_differs(self) -> None:
        payload = b"z" * 1024
        with tempfile.TemporaryDirectory() as tmp:
            target = self._target(tmp)
            part = target.with_name(target.name + ".part")
            part.write_bytes(b"old" * 100)
            target.with_name(target.name + ".part.json").write_text(
                json.dumps({"url": "https://example/pkg.zip", "sha256": "a" * 64}),
                encoding="utf-8",
            )
            with patch.object(
                download.urllib.request,
                "urlopen",
                return_value=_Response(payload, status=200),
            ):
                download.fetch_resumable(
                    "https://example/pkg.zip",
                    target,
                    expected_sha256=_sha(payload),
                    max_bytes=1024 * 1024,
                    timeout=10,
                )
            self.assertEqual(target.read_bytes(), payload)

    def test_hash_mismatch_removes_corrupt_part(self) -> None:
        payload = b"good" * 100
        with tempfile.TemporaryDirectory() as tmp:
            target = self._target(tmp)
            with patch.object(
                download.urllib.request,
                "urlopen",
                return_value=_Response(b"corrupt", status=200),
            ):
                with self.assertRaises(download.DownloadError):
                    download.fetch_resumable(
                        "https://example/pkg.zip",
                        target,
                        expected_sha256=_sha(payload),
                        max_bytes=1024 * 1024,
                        timeout=10,
                    )
            self.assertFalse(target.exists())
            self.assertFalse(target.with_name(target.name + ".part").exists())

    def test_rejects_oversized_stream(self) -> None:
        payload = b"x" * 4096
        with tempfile.TemporaryDirectory() as tmp:
            target = self._target(tmp)
            with patch.object(
                download.urllib.request,
                "urlopen",
                return_value=_Response(payload, status=200),
            ):
                with self.assertRaises(download.DownloadError):
                    download.fetch_resumable(
                        "https://example/pkg.zip",
                        target,
                        expected_sha256=_sha(payload),
                        max_bytes=1024,
                        timeout=10,
                    )
            self.assertFalse(target.exists())

    def test_completed_part_survives_http_416(self) -> None:
        payload = b"complete" * 200
        with tempfile.TemporaryDirectory() as tmp:
            target = self._target(tmp)
            part = target.with_name(target.name + ".part")
            part.write_bytes(payload)
            target.with_name(target.name + ".part.json").write_text(
                json.dumps({"url": "https://example/pkg.zip", "sha256": _sha(payload)}),
                encoding="utf-8",
            )
            error = urllib.error.HTTPError(
                "https://example/pkg.zip", 416, "Range Not Satisfiable", {}, None
            )
            with patch.object(download.urllib.request, "urlopen", side_effect=error):
                download.fetch_resumable(
                    "https://example/pkg.zip",
                    target,
                    expected_sha256=_sha(payload),
                    max_bytes=1024 * 1024,
                    timeout=10,
                )
            self.assertEqual(target.read_bytes(), payload)

    def test_network_error_keeps_part_for_resume(self) -> None:
        payload = b"n" * 1024
        with tempfile.TemporaryDirectory() as tmp:
            target = self._target(tmp)
            part = target.with_name(target.name + ".part")
            part.write_bytes(payload[:300])
            target.with_name(target.name + ".part.json").write_text(
                json.dumps({"url": "https://example/pkg.zip", "sha256": _sha(payload)}),
                encoding="utf-8",
            )
            error = urllib.error.URLError("offline")
            with patch.object(download.urllib.request, "urlopen", side_effect=error):
                with self.assertRaises(download.DownloadError):
                    download.fetch_resumable(
                        "https://example/pkg.zip",
                        target,
                        expected_sha256=_sha(payload),
                        max_bytes=1024 * 1024,
                        timeout=10,
                    )
            self.assertEqual(part.stat().st_size, 300)


if __name__ == "__main__":
    unittest.main()
