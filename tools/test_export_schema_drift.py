#!/usr/bin/env python3
"""
Regression check: schema/*.schema.json must match what
tools/export_schema.py would generate from the current tools/models.py.

If this fails, someone changed models.py without re-running
`python tools/export_schema.py` to regenerate the checked-in schema files.
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import export_schema

REPO_SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schema"


class SchemaExportDriftTest(unittest.TestCase):
    def test_schema_files_match_generated_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            original_schema_dir = export_schema.SCHEMA_DIR
            export_schema.SCHEMA_DIR = tmp_dir
            try:
                export_schema.main()
            finally:
                export_schema.SCHEMA_DIR = original_schema_dir

            for filename in ("band.schema.json", "release.schema.json"):
                generated = (tmp_dir / filename).read_text()
                checked_in = (REPO_SCHEMA_DIR / filename).read_text()
                self.assertEqual(
                    checked_in,
                    generated,
                    f"schema/{filename} is out of date with tools/models.py -- "
                    f"run `python tools/export_schema.py` to regenerate it.",
                )


if __name__ == "__main__":
    unittest.main()
