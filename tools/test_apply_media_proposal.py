#!/usr/bin/env python3
"""
Tests for tools/apply_media_proposal.py.
Verifies applying image and video proposals to band and release scopes,
file placement, sha256 checksums, and YAML updates.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from archive_fixture import build_archive_with_nested_fields
from models import MusicAlbum, MusicGroup

APPLY_MEDIA_PY = Path(__file__).resolve().parent / "apply_media_proposal.py"
SAMPLE_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
SAMPLE_VIDEO = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ApplyMediaProposalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)
        self.bands_dir = self.tmp_path / "bands"
        self.fx = build_archive_with_nested_fields(self.bands_dir)

    def _run_tool(self, proposal: dict, media_path: Path) -> subprocess.CompletedProcess:
        proposal_file = self.tmp_path / "proposal.json"
        proposal_file.write_text(json.dumps(proposal))
        return subprocess.run(
            [
                sys.executable,
                str(APPLY_MEDIA_PY),
                "--proposal-file",
                str(proposal_file),
                "--media-file",
                str(media_path),
                "--bands-dir",
                str(self.bands_dir),
                "--skip-upload",
            ],
            capture_output=True,
            text=True,
        )

    def test_apply_band_image(self):
        media_file = self.tmp_path / "photo.jpg"
        media_file.write_bytes(SAMPLE_JPEG)

        proposal = {
            "id": 1,
            "band_slug": self.fx.band_slug,
            "release_slug": None,
            "media_type": "image",
            "original_filename": "concert.jpg",
            "content_type": "image/jpeg",
            "caption": "Live concert 1995",
        }

        res = self._run_tool(proposal, media_file)
        self.assertEqual(res.returncode, 0, res.stderr)

        band_data = yaml.safe_load(self.fx.band_yaml.read_text())
        band = MusicGroup.model_validate(band_data)

        target_media_file = self.fx.band_dir / "media" / f"{self.fx.band_slug}-concert.jpg"
        self.assertTrue(target_media_file.exists())
        self.assertEqual(target_media_file.read_bytes(), SAMPLE_JPEG)

        matching = [img for img in band.image if img.contentUrl == f"media/{self.fx.band_slug}-concert.jpg"]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0].caption, "Live concert 1995")
        self.assertEqual(matching[0].identifier[0].value, _sha256(SAMPLE_JPEG))

    def test_apply_release_image(self):
        media_file = self.tmp_path / "cover.jpg"
        media_file.write_bytes(SAMPLE_JPEG)

        proposal = {
            "id": 2,
            "band_slug": self.fx.band_slug,
            "release_slug": self.fx.release_slug,
            "media_type": "image",
            "original_filename": "album-cover.jpg",
            "content_type": "image/jpeg",
            "caption": "Original cassette cover",
        }

        res = self._run_tool(proposal, media_file)
        self.assertEqual(res.returncode, 0, res.stderr)

        release_data = yaml.safe_load(self.fx.release_yaml.read_text())
        release = MusicAlbum.model_validate(release_data)

        target_file = self.fx.release_dir / "album-cover.jpg"
        self.assertTrue(target_file.exists())

        matching = [img for img in release.image if img.contentUrl == "album-cover.jpg"]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0].caption, "Original cassette cover")
        self.assertEqual(matching[0].identifier[0].value, _sha256(SAMPLE_JPEG))

    @mock.patch("apply_media_proposal.ffprobe_av_info", return_value=("PT4M15S", "1200 kbps"))
    def test_apply_band_video(self, mock_ffprobe):
        media_file = self.tmp_path / "video.mp4"
        media_file.write_bytes(SAMPLE_VIDEO)

        proposal = {
            "id": 3,
            "band_slug": self.fx.band_slug,
            "release_slug": None,
            "media_type": "video",
            "original_filename": "rehearsal.mp4",
            "content_type": "video/mp4",
            "caption": "Rehearsal 1994",
        }

        from apply_media_proposal import apply_media_proposal

        target_file, target_yaml, content_url = apply_media_proposal(
            proposal,
            media_file,
            self.bands_dir,
            skip_upload=True,
        )

        self.assertTrue(target_file.exists())
        self.assertEqual(content_url, f"media/{self.fx.band_slug}-rehearsal.mp4")

        band_data = yaml.safe_load(target_yaml.read_text())
        band = MusicGroup.model_validate(band_data)
        matching = [v for v in band.video if v.contentUrl == content_url]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0].name, "Rehearsal 1994")
        self.assertEqual(matching[0].duration, "PT4M15S")
        self.assertEqual(matching[0].bitrate, "1200 kbps")

    @mock.patch("apply_media_proposal.ffprobe_av_info", return_value=("PT2M30S", "800 kbps"))
    def test_apply_release_video(self, mock_ffprobe):
        media_file = self.tmp_path / "clip.mp4"
        media_file.write_bytes(SAMPLE_VIDEO)

        proposal = {
            "id": 4,
            "band_slug": self.fx.band_slug,
            "release_slug": self.fx.release_slug,
            "media_type": "video",
            "original_filename": "clip.mp4",
            "content_type": "video/mp4",
            "caption": "Music video",
        }

        from apply_media_proposal import apply_media_proposal

        target_file, target_yaml, content_url = apply_media_proposal(
            proposal,
            media_file,
            self.bands_dir,
            skip_upload=True,
        )

        self.assertTrue(target_file.exists())
        self.assertEqual(content_url, "video-clip.mp4")

        release_data = yaml.safe_load(target_yaml.read_text())
        release = MusicAlbum.model_validate(release_data)
        matching = [v for v in release.video if v.contentUrl == "video-clip.mp4"]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0].name, "Music video")
        self.assertEqual(matching[0].duration, "PT2M30S")

    def test_collision_with_different_content_appends_id(self):
        media_file_1 = self.tmp_path / "img1.jpg"
        media_file_1.write_bytes(SAMPLE_JPEG)

        proposal_1 = {
            "id": 10,
            "band_slug": self.fx.band_slug,
            "release_slug": None,
            "media_type": "image",
            "original_filename": "promo.jpg",
            "content_type": "image/jpeg",
        }
        res1 = self._run_tool(proposal_1, media_file_1)
        self.assertEqual(res1.returncode, 0)

        # Second file with same original_filename but different content
        media_file_2 = self.tmp_path / "img2.jpg"
        different_jpeg = SAMPLE_JPEG + b"extra_content"
        media_file_2.write_bytes(different_jpeg)

        proposal_2 = {
            "id": 11,
            "band_slug": self.fx.band_slug,
            "release_slug": None,
            "media_type": "image",
            "original_filename": "promo.jpg",
            "content_type": "image/jpeg",
        }
        res2 = self._run_tool(proposal_2, media_file_2)
        self.assertEqual(res2.returncode, 0)

        file1 = self.fx.band_dir / "media" / f"{self.fx.band_slug}-promo.jpg"
        file2 = self.fx.band_dir / "media" / f"{self.fx.band_slug}-promo-11.jpg"
        self.assertTrue(file1.exists())
        self.assertTrue(file2.exists())
        self.assertEqual(file1.read_bytes(), SAMPLE_JPEG)
        self.assertEqual(file2.read_bytes(), different_jpeg)

    def test_invalid_slug_rejected(self):
        media_file = self.tmp_path / "photo.jpg"
        media_file.write_bytes(SAMPLE_JPEG)

        proposal = {
            "id": 99,
            "band_slug": "../evil-slug",
            "release_slug": None,
            "media_type": "image",
            "original_filename": "photo.jpg",
            "content_type": "image/jpeg",
        }
        res = self._run_tool(proposal, media_file)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("not a valid slug", res.stderr)


if __name__ == "__main__":
    unittest.main()
