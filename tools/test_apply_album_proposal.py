#!/usr/bin/env python3
"""
Tests for tools/apply_album_proposal.py (GitHub issue #20).
Verifies:
- Creating release directory under target band
- Copying and renaming staged audio tracks and cover image
- Computing sha256 checksums and probing duration/bitrate
- Generating valid release.yaml adhering to MusicAlbum schema
- Archive.org upload and metadata bundle sync dispatch
- Failure modes: invalid band, collision with existing release, missing track files, upload failures
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
from models import MusicAlbum  # noqa: E402
from validate import load_yaml  # noqa: E402

import apply_album_proposal  # noqa: E402

SAMPLE_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
SAMPLE_AUDIO_1 = b"ID3\x03\x00\x00\x00\x00\x00\x00track1"
SAMPLE_AUDIO_2 = b"ID3\x03\x00\x00\x00\x00\x00\x00track2"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ApplyAlbumProposalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)
        self.bands_dir = self.tmp_path / "bands"
        self.fx = build_archive_with_nested_fields(self.bands_dir)

        self.tracks_dir = self.tmp_path / "staged_tracks"
        self.tracks_dir.mkdir()
        (self.tracks_dir / "track_1.bin").write_bytes(SAMPLE_AUDIO_1)
        (self.tracks_dir / "track_2.bin").write_bytes(SAMPLE_AUDIO_2)

        self.cover_file = self.tmp_path / "cover.jpg"
        self.cover_file.write_bytes(SAMPLE_JPEG)

        self.proposal = {
            "id": 10,
            "band_slug": self.fx.band_slug,
            "release_slug": "1997-novyj-albom",
            "name": "Новый альбом",
            "date_published": "1997",
            "genre": ["Punk Rock", "Garage"],
            "license": "https://creativecommons.org/licenses/by-nc-sa/4.0/",
            "description": "Записано в Даугавпилсе в 1997 году.",
            "description_en": "Recorded in Daugavpils in 1997.",
            "tracks": [
                {
                    "position": 1,
                    "name": "Вступление",
                    "original_filename": "01-intro.mp3",
                    "content_type": "audio/mpeg",
                },
                {
                    "position": 2,
                    "name": "Песня о городе",
                    "original_filename": "02-city.mp3",
                    "content_type": "audio/mpeg",
                },
            ],
        }

    @mock.patch("apply_album_proposal.publish_metadata_bundle", return_value=True)
    @mock.patch("apply_album_proposal.publish_item", return_value=True)
    @mock.patch("apply_album_proposal.ffprobe_av_info", side_effect=[("PT2M10S", "320 kbps"), ("PT3M45S", "320 kbps")])
    def test_apply_album_happy_path_with_cover_and_upload(
        self, mock_ffprobe, mock_publish_item, mock_publish_bundle
    ):
        release_yaml_path = apply_album_proposal.apply_album_proposal(
            proposal=self.proposal,
            tracks_dir=self.tracks_dir,
            cover_file=self.cover_file,
            bands_dir=self.bands_dir,
            skip_upload=False,
        )

        self.assertTrue(release_yaml_path.exists())
        release_dir = release_yaml_path.parent
        self.assertEqual(release_dir.name, "1997-novyj-albom")

        # Verify cover placement and checksum
        dest_cover = release_dir / "cover.jpg"
        self.assertTrue(dest_cover.exists())
        self.assertEqual(dest_cover.read_bytes(), SAMPLE_JPEG)

        # Verify audio track placement and renaming
        track1 = release_dir / "01-vstuplenie.mp3"
        track2 = release_dir / "02-pesnia-o-gorode.mp3"
        self.assertTrue(track1.exists())
        self.assertTrue(track2.exists())
        self.assertEqual(track1.read_bytes(), SAMPLE_AUDIO_1)
        self.assertEqual(track2.read_bytes(), SAMPLE_AUDIO_2)

        # Verify release.yaml metadata conforms to MusicAlbum model
        album = MusicAlbum.model_validate(load_yaml(release_yaml_path))
        self.assertEqual(album.name, "Новый альбом")
        self.assertEqual(album.slug, "1997-novyj-albom")
        self.assertEqual(album.datePublished, "1997")
        self.assertEqual(album.byArtist, self.fx.band_slug)
        self.assertEqual(album.genre, ["Punk Rock", "Garage"])
        self.assertEqual(album.description, "Записано в Даугавпилсе в 1997 году.")
        self.assertEqual(album.description_en, "Recorded in Daugavpils in 1997.")

        self.assertEqual(len(album.image), 1)
        self.assertEqual(album.image[0].contentUrl, "cover.jpg")
        self.assertEqual(album.image[0].identifier[0].value, _sha256(SAMPLE_JPEG))

        self.assertEqual(len(album.track), 2)
        self.assertEqual(album.track[0].position, 1)
        self.assertEqual(album.track[0].name, "Вступление")
        self.assertEqual(album.track[0].audio.contentUrl, "01-vstuplenie.mp3")
        self.assertEqual(album.track[0].audio.duration, "PT2M10S")
        self.assertEqual(album.track[0].audio.bitrate, "320 kbps")
        self.assertEqual(album.track[0].audio.identifier[0].value, _sha256(SAMPLE_AUDIO_1))

        self.assertEqual(album.track[1].position, 2)
        self.assertEqual(album.track[1].name, "Песня о городе")
        self.assertEqual(album.track[1].audio.contentUrl, "02-pesnia-o-gorode.mp3")
        self.assertEqual(album.track[1].audio.duration, "PT3M45S")

        # Verify archive.org publishing called
        mock_publish_item.assert_called_once()
        call_kwargs = mock_publish_item.call_args[1]
        self.assertEqual(call_kwargs["item_id"], f"daugavpils-fans-{self.fx.band_slug}-1997-novyj-albom")
        self.assertIn("cover.jpg", call_kwargs["files"])
        self.assertIn("01-vstuplenie.mp3", call_kwargs["files"])
        self.assertIn("02-pesnia-o-gorode.mp3", call_kwargs["files"])

        mock_publish_bundle.assert_called_once_with(self.bands_dir, dry_run=False)

    @mock.patch("apply_album_proposal.ffprobe_av_info", return_value=("PT3M00S", "320 kbps"))
    def test_apply_album_without_cover(self, mock_ffprobe):
        release_yaml_path = apply_album_proposal.apply_album_proposal(
            proposal=self.proposal,
            tracks_dir=self.tracks_dir,
            cover_file=None,
            bands_dir=self.bands_dir,
            skip_upload=True,
        )

        album = MusicAlbum.model_validate(load_yaml(release_yaml_path))
        self.assertEqual(album.image, [])
        self.assertFalse((release_yaml_path.parent / "cover.jpg").exists())

    def test_apply_album_fails_if_band_not_found(self):
        prop = dict(self.proposal)
        prop["band_slug"] = "non-existent-band"

        with self.assertRaises(apply_album_proposal.AlbumApplyError) as ctx:
            apply_album_proposal.apply_album_proposal(
                proposal=prop,
                tracks_dir=self.tracks_dir,
                cover_file=self.cover_file,
                bands_dir=self.bands_dir,
                skip_upload=True,
            )
        self.assertIn("has no band.yaml", str(ctx.exception))

    def test_apply_album_fails_on_slug_collision(self):
        prop = dict(self.proposal)
        prop["release_slug"] = self.fx.release_slug  # Already exists in fixture

        with self.assertRaises(apply_album_proposal.AlbumApplyError) as ctx:
            apply_album_proposal.apply_album_proposal(
                proposal=prop,
                tracks_dir=self.tracks_dir,
                cover_file=self.cover_file,
                bands_dir=self.bands_dir,
                skip_upload=True,
            )
        self.assertIn("already exists", str(ctx.exception))

    def test_apply_album_fails_if_track_audio_missing(self):
        empty_dir = self.tmp_path / "empty_tracks"
        empty_dir.mkdir()

        with self.assertRaises(apply_album_proposal.AlbumApplyError) as ctx:
            apply_album_proposal.apply_album_proposal(
                proposal=self.proposal,
                tracks_dir=empty_dir,
                cover_file=self.cover_file,
                bands_dir=self.bands_dir,
                skip_upload=True,
            )
        self.assertIn("Audio file for track 1", str(ctx.exception))

    @mock.patch("apply_album_proposal.publish_item", return_value=False)
    @mock.patch("apply_album_proposal.ffprobe_av_info", return_value=("PT3M00S", "320 kbps"))
    def test_apply_album_fails_if_upload_fails(self, mock_ffprobe, mock_publish_item):
        with self.assertRaises(apply_album_proposal.AlbumApplyError) as ctx:
            apply_album_proposal.apply_album_proposal(
                proposal=self.proposal,
                tracks_dir=self.tracks_dir,
                cover_file=self.cover_file,
                bands_dir=self.bands_dir,
                skip_upload=False,
            )
        self.assertIn("archive.org upload failed", str(ctx.exception))

    def test_dry_run_does_not_create_files(self):
        release_yaml_path = apply_album_proposal.apply_album_proposal(
            proposal=self.proposal,
            tracks_dir=self.tracks_dir,
            cover_file=self.cover_file,
            bands_dir=self.bands_dir,
            dry_run=True,
            skip_upload=True,
        )
        self.assertFalse(release_yaml_path.exists())
        self.assertFalse(release_yaml_path.parent.exists())

    @mock.patch("apply_album_proposal.ffprobe_av_info", return_value=("PT3M00S", "320 kbps"))
    def test_cli_main_with_skip_upload(self, mock_ffprobe):
        prop_file = self.tmp_path / "proposal.json"
        prop_file.write_text(json.dumps(self.proposal))

        apply_album_proposal.main(
            [
                "--proposal-file",
                str(prop_file),
                "--tracks-dir",
                str(self.tracks_dir),
                "--cover-file",
                str(self.cover_file),
                "--bands-dir",
                str(self.bands_dir),
                "--skip-upload",
            ]
        )

        expected_yaml = self.bands_dir / self.fx.band_slug / "1997-novyj-albom" / "release.yaml"
        self.assertTrue(expected_yaml.exists())


if __name__ == "__main__":
    unittest.main()
