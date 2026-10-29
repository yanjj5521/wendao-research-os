from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from services.research_tools import unpack_lammps_bundle


class BundleExtractionTests(unittest.TestCase):
    def test_extraction_rejects_traversal_paths(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wendao-bundle-test-") as temp_dir:
            root = Path(temp_dir)
            archive = root / "bundle.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("case/log.lammps", "LAMMPS")
                bundle.writestr("../escape.txt", "bad")
                bundle.writestr("/absolute.txt", "bad")
            destination = root / "extract"
            extracted = unpack_lammps_bundle(archive, destination)
            self.assertEqual(len(extracted), 1)
            self.assertEqual(extracted[0].name, "log.lammps")
            self.assertFalse((root / "escape.txt").exists())

    def test_extraction_enforces_uncompressed_limit(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wendao-bundle-test-") as temp_dir:
            root = Path(temp_dir)
            archive = root / "bundle.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("case/log.lammps", b"x" * 4096)
            with self.assertRaises(ValueError):
                unpack_lammps_bundle(archive, root / "extract", max_bytes=1024)


if __name__ == "__main__":
    unittest.main()
