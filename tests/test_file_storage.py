from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from services.file_storage import UploadLimitExceeded, save_upload_limited


class _FakeUpload:
    def __init__(self, data: bytes) -> None:
        self.file = io.BytesIO(data)


class FileStorageTests(unittest.TestCase):
    def test_upload_limit_removes_partial_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wendao-upload-test-") as temp_dir:
            target = Path(temp_dir) / "large.bin"
            with self.assertRaises(UploadLimitExceeded):
                save_upload_limited(_FakeUpload(b"x" * 4096), target, max_bytes=1024)
            self.assertFalse(target.exists())

    def test_upload_is_streamed_within_limit(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wendao-upload-test-") as temp_dir:
            target = Path(temp_dir) / "small.bin"
            size = save_upload_limited(_FakeUpload(b"x" * 512), target, max_bytes=1024)
            self.assertEqual(size, 512)
            self.assertEqual(target.read_bytes(), b"x" * 512)


if __name__ == "__main__":
    unittest.main()
