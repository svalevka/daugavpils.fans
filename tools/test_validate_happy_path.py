#!/usr/bin/env python3
"""
Happy-path coverage: a fully valid, fully populated archive tree passes
tools/validate.py cleanly.
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from archive_fixture import build_valid_archive, run_validate


class HappyPathValidatorTest(unittest.TestCase):
    def test_fully_populated_archive_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            bands_dir = Path(tmp) / "bands"
            build_valid_archive(bands_dir)

            result = run_validate(bands_dir)

            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertIn("OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
