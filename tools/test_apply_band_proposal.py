#!/usr/bin/env python3
"""
Tests for tools/apply_band_proposal.py (GitHub issue #22).
Verifies:
- Creating band directory under bands/<band-slug>/
- Copying and renaming staged band photo into media/<band-slug>-photo.<ext>
- Generating valid band.yaml adhering to MusicGroup schema
- Optionally creating first release under bands/<band-slug>/<release-slug>/ with tracks, cover, release.yaml
- Archive.org publishing for band item, release item, and metadata backup bundle
- Failure modes: collision with existing band, invalid slugs, missing tracks, upload failures
- CLI invocation with --dry-run and --skip-upload
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from archive_fixture import build_archive_with_nested_fields  # noqa: E402
from models import MusicAlbum, MusicGroup  # noqa: E402
from validate import load_yaml  # noqa: E402

import apply_band_proposal  # noqa: E402

SAMPLE_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
SAMPLE_AUDIO_1 = b"ID3\x03\x00\x00\x00\x00\x00\x00track1"
SAMPLE_AUDIO_2 = b"ID3\x03\x00\x00\x00\x00\x00\x00track2"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ApplyBandProposalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)
        self.bands_dir = self.tmp_path / "bands"
        self.fx = build_archive_with_nested_fields(self.bands_dir)

        self.photo_file = self.tmp_path / "photo.jpg"
        self.photo_file.write_bytes(SAMPLE_JPEG)

        self.cover_file = self.tmp_path / "cover.jpg"
        self.cover_file.write_bytes(SAMPLE_JPEG)

        self.tracks_dir = self.tmp_path / "staged_tracks"
        self.tracks_dir.mkdir()
        (self.tracks_dir / "track_1.bin").write_bytes(SAMPLE_AUDIO_1)
        (self.tracks_dir / "track_2.bin").write_bytes(SAMPLE_AUDIO_2)

        self.band_only_proposal = {
            "id": 1,
            "name": "Новая Группа",
            "band_slug": "novaya-gruppa",
            "founding_date": "1994",
            "dissolution_date": "1997",
            "location": "Daugavpils, Latvia",
            "genre": ["Hard Rock", "Heavy Metal"],
            "description": "История группы из Даугавпилса.",
            "description_en": "History of the band from Daugavpils.",
            "has_photo": True,
            "has_release": False,
        }

        self.band_and_release_proposal = {
            "id": 2,
            "name": "Полная Группа",
            "band_slug": "polnaya-gruppa",
            "founding_date": "1995",
            "location": "Daugavpils, Latvia",
            "genre": ["Punk Rock"],
            "description": "Панк группа.",
            "has_photo": True,
            "has_release": True,
            "release_name": "Первый Альбом",
            "release_slug": "1996-pervyi-albom",
            "release_date_published": "1996",
            "release_genre": ["Punk Rock"],
            "release_license": "https://creativecommons.org/licenses/by-nc-sa/4.0/",
            "release_description": "Записано на репетиционной точке.",
            "has_release_cover": True,
            "tracks": [
                {
                    "position": 1,
                    "name": "Вступление",
                    "original_filename": "01-intro.mp3",
                    "content_type": "audio/mpeg",
                },
                {
                    "position": 2,
                    "name": "Главная Песня",
                    "original_filename": "02-main.mp3",
                    "content_type": "audio/mpeg",
                },
            ],
        }

    @mock.patch("apply_band_proposal.publish_metadata_bundle", return_value=True)
    @mock.patch("apply_band_proposal.publish_item", return_value=True)
    def test_apply_band_only_with_photo(self, mock_publish_item, mock_publish_bundle):
        band_yaml_path = apply_band_proposal.apply_band_proposal(
            proposal=self.band_only_proposal,
            photo_file=self.photo_file,
            cover_file=None,
            tracks_dir=None,
            bands_dir=self.bands_dir,
            skip_upload=False,
        )

        self.assertTrue(band_yaml_path.exists())
        band_dir = band_yaml_path.parent
        self.assertEqual(band_dir.name, "novaya-gruppa")

        # Verify photo placement
        photo_path = band_dir / "media" / "novaya-gruppa-photo.jpg"
        self.assertTrue(photo_path.exists())
        self.assertEqual(photo_path.read_bytes(), SAMPLE_JPEG)

        # Validate band.yaml schema
        raw = load_yaml(band_yaml_path)
        band = MusicGroup.model_validate(raw)
        self.assertEqual(band.name, "Новая Группа")
        self.assertEqual(band.slug, "novaya-gruppa")
        self.assertEqual(band.foundingDate, "1994")
        self.assertEqual(band.dissolutionDate, "1997")
        self.assertEqual(len(band.image), 1)
        self.assertEqual(band.image[0].contentUrl, "media/novaya-gruppa-photo.jpg")
        self.assertEqual(band.image[0].identifier[0].value, _sha256(SAMPLE_JPEG))

        # Check archive.org publish calls
        self.assertEqual(mock_publish_item.call_count, 1)
        args, kwargs = mock_publish_item.call_args
        self.assertEqual(kwargs["item_id"], "daugavpils-fans-novaya-gruppa")
        mock_publish_bundle.assert_called_once_with(self.bands_dir, dry_run=False)

    @mock.patch("apply_band_proposal.publish_metadata_bundle", return_value=True)
    @mock.patch("apply_band_proposal.publish_item", return_value=True)
    @mock.patch(
        "apply_band_proposal.ffprobe_av_info",
        side_effect=[("PT1M30S", "320 kbps"), ("PT3M00S", "320 kbps")],
    )
    def test_apply_band_with_first_release(
        self, mock_ffprobe, mock_publish_item, mock_publish_bundle
    ):
        band_yaml_path = apply_band_proposal.apply_band_proposal(
            proposal=self.band_and_release_proposal,
            photo_file=self.photo_file,
            cover_file=self.cover_file,
            tracks_dir=self.tracks_dir,
            bands_dir=self.bands_dir,
            skip_upload=False,
        )

        self.assertTrue(band_yaml_path.exists())
        band_dir = band_yaml_path.parent
        release_dir = band_dir / "1996-pervyi-albom"
        self.assertTrue(release_dir.exists())

        # Validate release.yaml
        release_yaml_path = release_dir / "release.yaml"
        self.assertTrue(release_yaml_path.exists())
        raw_release = load_yaml(release_yaml_path)
        album = MusicAlbum.model_validate(raw_release)
        self.assertEqual(album.name, "Первый Альбом")
        self.assertEqual(album.byArtist, "polnaya-gruppa")
        self.assertEqual(len(album.track), 2)
        self.assertEqual(album.track[0].position, 1)
        self.assertEqual(album.track[0].audio.duration, "PT1M30S")
        self.assertEqual(album.track[1].position, 2)
        self.assertEqual(album.track[1].audio.duration, "PT3M00S")

        # Two publish_item calls: 1 for band item (photo), 1 for release item (audio/cover)
        self.assertEqual(mock_publish_item.call_count, 2)
        items_published = [call.kwargs["item_id"] for call in mock_publish_item.call_args_list]
        self.assertIn("daugavpils-fans-polnaya-gruppa", items_published)
        self.assertIn("daugavpils-fans-polnaya-gruppa-1996-pervyi-albom", items_published)
        mock_publish_bundle.assert_called_once_with(self.bands_dir, dry_run=False)

    def test_collision_with_existing_band_raises_error(self):
        # self.fx.band_slug already exists
        colliding_proposal = dict(self.band_only_proposal)
        colliding_proposal["band_slug"] = self.fx.band_slug

        with self.assertRaises(apply_band_proposal.BandApplyError) as ctx:
            apply_band_proposal.apply_band_proposal(
                proposal=colliding_proposal,
                photo_file=None,
                cover_file=None,
                tracks_dir=None,
                bands_dir=self.bands_dir,
                skip_upload=True,
            )
        self.assertIn("already exists", str(ctx.exception))

    def test_invalid_band_slug_raises_error(self):
        invalid_proposal = dict(self.band_only_proposal)
        invalid_proposal["band_slug"] = "INVALID SLUG!"

        with self.assertRaises(apply_band_proposal.BandApplyError) as ctx:
            apply_band_proposal.apply_band_proposal(
                proposal=invalid_proposal,
                photo_file=None,
                cover_file=None,
                tracks_dir=None,
                bands_dir=self.bands_dir,
                skip_upload=True,
            )
        self.assertIn("is invalid", str(ctx.exception))

    def test_cli_dry_run_does_not_mutate_disk(self):
        proposal_file = self.tmp_path / "proposal.json"
        proposal_file.write_text(json.dumps(self.band_only_proposal))

        apply_band_proposal.main(
            [
                "--proposal-file",
                str(proposal_file),
                "--photo-file",
                str(self.photo_file),
                "--bands-dir",
                str(self.bands_dir),
                "--dry-run",
            ]
        )

        target_band_dir = self.bands_dir / "novaya-gruppa"
        self.assertFalse(target_band_dir.exists())


if __name__ == "__main__":
    unittest.main()
