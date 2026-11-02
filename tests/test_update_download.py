from __future__ import annotations

import hashlib
import http.server
import json
import re
import threading
import unittest
from pathlib import Path

from services.download import DownloadError, fetch_resumable, file_sha256


class _Handler(http.server.BaseHTTPRequestHandler):
    def server_payload(self) -> bytes:
        return self.server.payload  # type: ignore[attr-defined]

    def do_GET(self) -> None:  # noqa: N802
        payload = self.server_payload()
        behavior = getattr(self.server, "behavior", "range")
        if behavior == "500":
            self.send_error(500)
            return
        range_header = self.headers.get("Range", "")
        match = re.match(r"bytes=(\d+)-", range_header) if range_header else None
        start = int(match.group(1)) if match else 0
        if behavior == "no206" or not match:
            start, status = 0, 200
        elif start >= len(payload):
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{len(payload)}")
            self.end_headers()
            return
        else:
            status = 206
        body = payload[start:]
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{len(payload) - 1}/{len(payload)}")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # silence
        pass


class RangeServer:
    def __init__(self, payload: bytes, behavior: str = "range") -> None:
        self.payload = payload
        self.behavior = behavior
        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.payload = payload  # type: ignore[attr-defined]
        self._server.behavior = behavior  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/file"

    def __enter__(self) -> "RangeServer":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._server.shutdown()
        self._server.server_close()


class FetchResumableTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self._tmp = tempfile.TemporaryDirectory(prefix="fetch-")
        self.directory = Path(self._tmp.name)
        self.payload = bytes(range(256)) * 4096  # 1 MiB deterministic
        self.sha = hashlib.sha256(self.payload).hexdigest()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _dest(self) -> Path:
        return self.directory / "pkg.zip"

    def _meta(self) -> Path:
        return self.directory / "pkg.zip.json"

    def _part(self) -> Path:
        return self.directory / "pkg.zip.part"

    def test_fresh_download_verifies_hash(self) -> None:
        with RangeServer(self.payload) as server:
            result = fetch_resumable(server.url, self._dest(), expected_sha256=self.sha, max_bytes=10 * 1024 * 1024, timeout=10)
        self.assertEqual(result.read_bytes(), self.payload)
        self.assertEqual(file_sha256(result), self.sha)
        self.assertFalse(self._part().exists())
        self.assertFalse(self._meta().exists())

    def test_resume_with_206(self) -> None:
        half = self.payload[: len(self.payload) // 2]
        self._part().write_bytes(half)
        self._meta().write_text(json.dumps({"url": "u", "sha256": self.sha}))
        with RangeServer(self.payload, behavior="range") as server:
            fetch_resumable(server.url, self._dest(), expected_sha256=self.sha, max_bytes=10 * 1024 * 1024, timeout=10)
        self.assertEqual(self._dest().read_bytes(), self.payload)

    def test_server_ignoring_range_starts_over(self) -> None:
        self._part().write_bytes(self.payload[: 100])
        self._meta().write_text(json.dumps({"url": "u", "sha256": self.sha}))
        with RangeServer(self.payload, behavior="no206") as server:
            fetch_resumable(server.url, self._dest(), expected_sha256=self.sha, max_bytes=10 * 1024 * 1024, timeout=10)
        self.assertEqual(self._dest().read_bytes(), self.payload)

    def test_416_with_complete_part_succeeds(self) -> None:
        self._part().write_bytes(self.payload)
        self._meta().write_text(json.dumps({"url": "u", "sha256": self.sha}))
        with RangeServer(self.payload, behavior="range") as server:
            fetch_resumable(server.url, self._dest(), expected_sha256=self.sha, max_bytes=10 * 1024 * 1024, timeout=10)
        self.assertEqual(self._dest().read_bytes(), self.payload)

    def test_hash_mismatch_discards_part(self) -> None:
        with RangeServer(self.payload, behavior="range") as server:
            with self.assertRaises(DownloadError) as caught:
                fetch_resumable(server.url, self._dest(), expected_sha256="0" * 64, max_bytes=10 * 1024 * 1024, timeout=10)
        self.assertIn("SHA-256", str(caught.exception))
        self.assertFalse(self._part().exists())
        self.assertFalse(self._meta().exists())

    def test_server_error_keeps_progress(self) -> None:
        with RangeServer(self.payload, behavior="500") as server:
            with self.assertRaises(DownloadError) as caught:
                fetch_resumable(server.url, self._dest(), expected_sha256=self.sha, max_bytes=10 * 1024 * 1024, timeout=10)
        self.assertIn("已保留进度", str(caught.exception))

    def test_invalid_sha_is_rejected_upfront(self) -> None:
        with RangeServer(self.payload) as server:
            with self.assertRaises(DownloadError):
                fetch_resumable(server.url, self._dest(), expected_sha256="short", max_bytes=1024, timeout=5)




class RemoteDisconnectRetryTests(unittest.TestCase):
    def test_transient_disconnect_retries_and_succeeds(self) -> None:
        import tempfile
        from unittest.mock import patch

        import http.client
        import urllib.request as urllib_request

        payload = b"hello-update-world" * 1000
        sha = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory(prefix="retry-") as directory:
            dest = Path(directory) / "pkg.zip"
            calls = {"n": 0}

            class _Resp:
                status = 200
                def __enter__(self):
                    return self
                def __exit__(self, *exc):
                    return False
                def read(self, size=-1):
                    if not hasattr(self, "_sent"):
                        self._sent = True
                        return payload
                    return b""

            def fake_urlopen(request, timeout=0):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise http.client.RemoteDisconnected("remote end closed")
                return _Resp()

            with patch.object(urllib_request, "urlopen", side_effect=fake_urlopen):
                result = fetch_resumable(
                    "http://example/file", dest, expected_sha256=sha, max_bytes=1024 * 1024, timeout=5
                )
            self.assertEqual(calls["n"], 2)
            self.assertEqual(result.read_bytes(), payload)


if __name__ == "__main__":
    unittest.main()
