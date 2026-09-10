#!/usr/bin/env python3
"""
Unit tests for verify_archive_org.py: IA derivative filtering, metadata drift
detection, and metadata bundle auditing.
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from archive_fixture import build_valid_archive
from verify_archive_org import _is_ia_generated, audit_item, audit_metadata_bundle, local_md5


class VerifyArchiveOrgTest(unittest.TestCase):
    def test_derivative_files_are_not_flagged_as_orphans(self):
        with tempfile.TemporaryDirectory() as tmp:
            bands_dir = Path(tmp) / "bands"
            fx = build_valid_archive(bands_dir)

            # Simulate an archive.org item having the original audio file
            # plus an IA-derived mp3 and an actual orphan
            mock_item = MagicMock()
            mock_item.exists = True
            mock_item.files = [
                {"name": "01-test-track.mp3", "source": "original", "md5": local_md5(fx.audio_path)},
                {"name": "01-test-track-derived.mp3", "source": "derivative"},
                {"name": "metadata.xml", "source": "metadata"},
                {"name": "stale-old-track.mp3", "source": "original"},
            ]
            mock_item.metadata = {"title": "Test Release"}

            expected_files = {"01-test-track.mp3": fx.audio_path}
            expected_metadata = {"title": "Test Release"}

            with patch("internetarchive.get_item", return_value=mock_item):
                problems = audit_item("item-123", expected_files, expected_metadata)

            # Only stale-old-track.mp3 should be reported as an orphan,
            # NOT the derived mp3 or metadata.xml
            self.assertEqual(len(problems), 1)
            self.assertIn("ORPHAN: stale-old-track.mp3", problems[0])

    def test_detects_metadata_drift(self):
        mock_item = MagicMock()
        mock_item.exists = True
        mock_item.files = []
        mock_item.metadata = {"description": "Old description on archive.org"}

        with patch("internetarchive.get_item", return_value=mock_item):
            problems = audit_item("item-123", {}, {"description": "New description in git"})

        self.assertEqual(len(problems), 1)
        self.assertIn("METADATA DRIFT: 'description'", problems[0])

    def test_metadata_bundle_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            bands_dir = Path(tmp) / "bands"
            fx = build_valid_archive(bands_dir)

            # Bundle with one matching file and one missing file
            mock_item = MagicMock()
            mock_item.exists = True
            mock_item.files = [
                {"name": "test-band/band.yaml", "md5": "wrong-hash"},
            ]

            with patch("internetarchive.get_item", return_value=mock_item):
                problems = audit_metadata_bundle(bands_dir)

            self.assertTrue(any("CONTENT MISMATCH" in p and "band.yaml" in p for p in problems))
            self.assertTrue(any("MISSING" in p and "release.yaml" in p for p in problems))


if __name__ == "__main__":
    unittest.main()
