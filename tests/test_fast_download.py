from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services import download

ROOT = Path(__file__).resolve().parents[1]


class FastDownloadTests(unittest.TestCase):
    """测速选线 + 并发分段：支持 Range 的服务器必须走并发，且哈希要一致。"""

    def test_parallel_stops_oversized_parts_and_preserves_existing_file(self) -> None:
        class OversizedResponse:
            status = 206

            def __init__(self, request):
                match = re.fullmatch(r"bytes=(\d+)-(\d+)", request.get_header("Range"))
                start, end = map(int, match.groups())
                self.headers = {"Content-Range": f"bytes {start}-{end}/10"}
                self.read_bytes = 0
                responses.append(self)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, size):
                self.read_bytes += size
                return b"x" * size

        responses = []
        with tempfile.TemporaryDirectory(prefix="bounded-parts-") as directory:
            target = Path(directory) / "package.zip"
            target.write_bytes(b"previous verified file")
            with patch.object(download.urllib.request, "urlopen", side_effect=lambda request, **_: OversizedResponse(request)):
                with self.assertRaises(download.DownloadError):
                    download._fetch_parallel(
                        "https://example.invalid/pkg", target, total=10, max_bytes=10,
                        expected_sha256="0" * 64, headers={}, proxy="", timeout=1,
                    )
            self.assertEqual(target.read_bytes(), b"previous verified file")
            self.assertTrue(responses)
            self.assertTrue(all(response.read_bytes <= 6 for response in responses))
            self.assertEqual(list(Path(directory).iterdir()), [target])

    def test_parallel_rejects_incorrect_content_range(self) -> None:
        class WrongRange:
            status = 206
            headers = {"Content-Range": "bytes 0-9/10"}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, size):
                raise AssertionError("A wrong Content-Range must be rejected before reading")

        with tempfile.TemporaryDirectory(prefix="wrong-range-") as directory:
            target = Path(directory) / "package.zip"
            with patch.object(download.urllib.request, "urlopen", return_value=WrongRange()):
                with self.assertRaises(download.DownloadError):
                    download._fetch_parallel(
                        "https://example.invalid/pkg", target, total=10, max_bytes=10,
                        expected_sha256="0" * 64, headers={}, proxy="", timeout=1,
                    )
            self.assertFalse(target.exists())

    def test_parallel_and_fallback(self) -> None:
        script = r'''
import hashlib
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from services.download import DownloadError, fetch_fast

PAYLOAD = os.urandom(5 * 1024 * 1024)
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()
seen_ranges: list[str] = []


class RangeHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        range_header = self.headers.get("Range", "")
        seen_ranges.append(range_header)
        if range_header.startswith("bytes="):
            start_s, _, end_s = range_header.split("=", 1)[1].partition("-")
            start = int(start_s or 0)
            end = int(end_s) if end_s else len(PAYLOAD) - 1
            chunk = PAYLOAD[start : end + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(PAYLOAD)}")
        else:
            chunk = PAYLOAD
            self.send_response(200)
        self.send_header("Content-Length", str(len(chunk)))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        self.wfile.write(chunk)

    def log_message(self, *args):
        return


class PlainHandler(RangeHandler):
    def do_GET(self):  # noqa: N802
        seen_ranges.append("plain")
        self.send_response(200)
        self.send_header("Content-Length", str(len(PAYLOAD)))
        self.end_headers()
        self.wfile.write(PAYLOAD)


def serve(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}/pkg.zip"


work = Path(tempfile.mkdtemp())

server, url = serve(RangeHandler)
target = work / "range.zip"
saved = fetch_fast(url, target, expected_sha256=DIGEST, max_bytes=50 * 1024 * 1024, timeout=30)
assert Path(saved).read_bytes() == PAYLOAD
ranges = [r for r in seen_ranges if r.startswith("bytes=")]
assert len(ranges) >= 3, seen_ranges  # 1 次测速 + 2 个分段并发

server2, url2 = serve(PlainHandler)
target2 = work / "plain.zip"
saved2 = fetch_fast(url2, target2, expected_sha256=DIGEST, max_bytes=50 * 1024 * 1024, timeout=30)
assert Path(saved2).read_bytes() == PAYLOAD
server2.shutdown()

# 哈希不符必须删文件并报错
try:
    fetch_fast(url, work / "bad.zip", expected_sha256="0" * 64, max_bytes=50 * 1024 * 1024, timeout=30)
    raise AssertionError("哈希不符应报错")
except DownloadError:
    pass
assert not (work / "bad.zip").exists()
server.shutdown()

# 所有线路不可达时给出 DownloadError，而不是裸异常
try:
    fetch_fast("http://127.0.0.1:1/none.zip", work / "none.zip",
               expected_sha256=DIGEST, max_bytes=50 * 1024 * 1024, timeout=3)
    raise AssertionError("不可达应报错")
except DownloadError:
    pass
print("fast download pass")
'''
        with tempfile.TemporaryDirectory(prefix="fast-") as directory:
            env = os.environ.copy()
            env["RESEARCH_OS_DATA_DIR"] = directory
            result = subprocess.run(
                [sys.executable, "-c", script], cwd=ROOT, env=env,
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("fast download pass", result.stdout)


if __name__ == "__main__":
    unittest.main()
