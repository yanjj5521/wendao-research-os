from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class FastDownloadTests(unittest.TestCase):
    """测速选线 + 并发分段：支持 Range 的服务器必须走并发，且哈希要一致。"""

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
server.shutdown()

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
