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
from models import MusicAlbum, MusicRecording
from publish_to_archive_org import media_files
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

    def test_none_expected_value_does_not_crash(self):
        # release_metadata() always includes "licenseurl", even as None for
        # a release with no license set - audit_item must treat that as an
        # absent field (matching archive.org's own "" default), not crash
        # on re.sub(None) like it used to.
        mock_item = MagicMock()
        mock_item.exists = True
        mock_item.files = []
        mock_item.metadata = {}

        with patch("internetarchive.get_item", return_value=mock_item):
            problems = audit_item("item-123", {}, {"licenseurl": None})

        self.assertEqual(problems, [])

    def test_release_with_lost_track_does_not_crash_media_files(self):
        # main() builds `[t.audio for t in release.track if t.audio is not
        # None] + release.image + release.video` before calling
        # media_files() - a release with a known-but-unpreserved track
        # (audio=None, e.g. ko-band's "Репетиционная запись") used to leave
        # a bare None in that list and crash media_files() with
        # AttributeError before any archive.org call was even made.
        release = MusicAlbum(
            name="Test Release",
            slug="1999-test-release",
            datePublished="1999",
            byArtist="test-band",
            track=[
                MusicRecording(position=1, name="Lost Track"),
                MusicRecording(
                    position=2,
                    name="Preserved Track",
                    audio={"contentUrl": "02-preserved-track.mp3", "encodingFormat": "audio/mpeg"},
                ),
            ],
        )

        files = media_files(
            Path("/fake/release/dir"),
            [t.audio for t in release.track if t.audio is not None] + release.image + release.video,
        )

        self.assertEqual(list(files), ["02-preserved-track.mp3"])

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
