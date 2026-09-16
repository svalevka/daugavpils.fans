#!/usr/bin/env python3
"""
Coverage for download_archive.py's core behaviors: it downloads a missing
file and verifies it, skips a file already present with a matching
checksum (without touching the network), and reports - without aborting
the run - a checksum mismatch or a fetch error. Network access itself is
mocked (urllib.request.urlretrieve is patched to write fixed bytes rather
than hitting archive.org), so these tests run offline and deterministically.
"""
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from archive_fixture import DEFAULT_AUDIO_BYTES, build_valid_archive
from download_archive import download_release, load_band, load_release, main, media_items_for_release
from models import MusicAlbum, MusicRecording


def fake_urlretrieve_writing(data: bytes):
    """Returns a stand-in for urllib.request.urlretrieve that writes `data`
    to the destination path instead of making a real HTTP request."""

    def _fake(url, dest):
        Path(dest).write_bytes(data)

    return _fake


class DownloadArchiveTest(unittest.TestCase):
    def test_downloads_missing_file_and_verifies_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            bands_dir = Path(tmp) / "bands"
            fx = build_valid_archive(bands_dir)
            fx.audio_path.unlink()  # simulate a fresh clone: gitignored media not on disk

            band = load_band(fx.band_dir)
            release = load_release(fx.release_dir)

            with patch(
                "download_archive.urllib.request.urlretrieve",
                side_effect=fake_urlretrieve_writing(DEFAULT_AUDIO_BYTES),
            ) as urlretrieve:
                failures = download_release(fx.release_dir, band.slug, release)

            self.assertEqual(failures, [])
            self.assertTrue(fx.audio_path.exists())
            urlretrieve.assert_called_once()

    def test_skips_already_present_file_with_matching_checksum(self):
        with tempfile.TemporaryDirectory() as tmp:
            bands_dir = Path(tmp) / "bands"
            fx = build_valid_archive(bands_dir)  # audio file left in place, checksum already matches

            band = load_band(fx.band_dir)
            release = load_release(fx.release_dir)

            with patch("download_archive.urllib.request.urlretrieve") as urlretrieve:
                failures = download_release(fx.release_dir, band.slug, release)

            self.assertEqual(failures, [])
            urlretrieve.assert_not_called()

    def test_reports_checksum_mismatch_without_aborting(self):
        with tempfile.TemporaryDirectory() as tmp:
            bands_dir = Path(tmp) / "bands"
            fx = build_valid_archive(bands_dir)
            fx.audio_path.unlink()

            band = load_band(fx.band_dir)
            release = load_release(fx.release_dir)

            with patch(
                "download_archive.urllib.request.urlretrieve",
                side_effect=fake_urlretrieve_writing(b"not the right bytes at all"),
            ):
                failures = download_release(fx.release_dir, band.slug, release)

            self.assertEqual(len(failures), 1)
            self.assertIn("checksum mismatch", failures[0].reason)

    def test_reports_fetch_error_without_aborting(self):
        with tempfile.TemporaryDirectory() as tmp:
            bands_dir = Path(tmp) / "bands"
            fx = build_valid_archive(bands_dir)
            fx.audio_path.unlink()

            band = load_band(fx.band_dir)
            release = load_release(fx.release_dir)

            def _raise(url, dest):
                raise urllib.error.HTTPError(url, 404, "Not Found", hdrs=None, fp=None)

            with patch("download_archive.urllib.request.urlretrieve", side_effect=_raise):
                failures = download_release(fx.release_dir, band.slug, release)

            self.assertEqual(len(failures), 1)
            self.assertIn("404", failures[0].reason)

    def test_main_exits_nonzero_but_still_processes_every_band(self):
        """One band's file is already present and verified (no network
        needed); the other's is missing and its simulated fetch fails.
        main() should still walk both, exit non-zero overall, and never
        touch the network for the already-verified one."""
        with tempfile.TemporaryDirectory() as tmp:
            bands_dir = Path(tmp) / "bands"
            build_valid_archive(bands_dir, band_slug="band-a", release_slug="1999-a")
            fx_b = build_valid_archive(bands_dir, band_slug="band-b", release_slug="1999-b")
            fx_b.audio_path.unlink()

            def _raise(url, dest):
                raise urllib.error.URLError("simulated network failure")

            with (
                patch("sys.argv", ["download_archive.py", "--bands-dir", str(bands_dir)]),
                patch("download_archive.urllib.request.urlretrieve", side_effect=_raise) as urlretrieve,
            ):
                exit_code = main()

            self.assertEqual(exit_code, 1)
            urlretrieve.assert_called_once()  # only band-b's missing file triggers a fetch attempt

    def test_media_items_for_release_skips_lost_tracks(self):
        # A track with no audio (a known-but-unpreserved recording, e.g.
        # ko-band's "Репетиционная запись") must not crash or produce a
        # None entry that fetch_one() would later choke on.
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

        items = media_items_for_release(release)

        self.assertEqual([item.contentUrl for item in items], ["02-preserved-track.mp3"])

    def test_main_with_band_flag_downloads_only_selected_band(self):
        with tempfile.TemporaryDirectory() as tmp:
            bands_dir = Path(tmp) / "bands"
            fx_a = build_valid_archive(bands_dir, band_slug="band-a", release_slug="1999-a")
            fx_b = build_valid_archive(bands_dir, band_slug="band-b", release_slug="1999-b")
            fx_a.audio_path.unlink()
            fx_b.audio_path.unlink()

            with (
                patch("sys.argv", ["download_archive.py", "--bands-dir", str(bands_dir), "--band", "band-a"]),
                patch(
                    "download_archive.urllib.request.urlretrieve",
                    side_effect=fake_urlretrieve_writing(DEFAULT_AUDIO_BYTES),
                ) as urlretrieve,
            ):
                exit_code = main()

            self.assertEqual(exit_code, 0)
            self.assertTrue(fx_a.audio_path.exists())
            self.assertFalse(fx_b.audio_path.exists())
            # urlretrieve was called for band-a's release audio (and not band-b)
            self.assertEqual(urlretrieve.call_count, 1)

    def test_main_with_release_flag_downloads_only_selected_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            bands_dir = Path(tmp) / "bands"
            fx_a = build_valid_archive(bands_dir, band_slug="band-a", release_slug="1999-a")
            fx_b = build_valid_archive(bands_dir, band_slug="band-b", release_slug="1999-b")
            fx_a.audio_path.unlink()
            fx_b.audio_path.unlink()

            with (
                patch("sys.argv", ["download_archive.py", "--bands-dir", str(bands_dir), "--release", "band-a/1999-a"]),
                patch(
                    "download_archive.urllib.request.urlretrieve",
                    side_effect=fake_urlretrieve_writing(DEFAULT_AUDIO_BYTES),
                ) as urlretrieve,
            ):
                exit_code = main()

            self.assertEqual(exit_code, 0)
            self.assertTrue(fx_a.audio_path.exists())
            self.assertFalse(fx_b.audio_path.exists())
            self.assertEqual(urlretrieve.call_count, 1)

    def test_main_with_unknown_band_slug_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            bands_dir = Path(tmp) / "bands"
            build_valid_archive(bands_dir, band_slug="band-a", release_slug="1999-a")

            with (
                patch("sys.argv", ["download_archive.py", "--bands-dir", str(bands_dir), "--band", "unknown-band"]),
                patch("sys.stderr") as mock_stderr,
            ):
                exit_code = main()

            self.assertEqual(exit_code, 1)
            mock_stderr.write.assert_called()
            err_msg = "".join(call.args[0] for call in mock_stderr.write.call_args_list)
            self.assertIn("Error: band 'unknown-band' not found", err_msg)

    def test_main_with_unknown_release_slug_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            bands_dir = Path(tmp) / "bands"
            build_valid_archive(bands_dir, band_slug="band-a", release_slug="1999-a")

            with (
                patch("sys.argv", ["download_archive.py", "--bands-dir", str(bands_dir), "--release", "band-a/unknown-rel"]),
                patch("sys.stderr") as mock_stderr,
            ):
                exit_code = main()

            self.assertEqual(exit_code, 1)
            mock_stderr.write.assert_called()
            err_msg = "".join(call.args[0] for call in mock_stderr.write.call_args_list)
            self.assertIn("Error: release 'band-a/unknown-rel' not found", err_msg)

    def test_main_with_malformed_release_slug_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            bands_dir = Path(tmp) / "bands"
            build_valid_archive(bands_dir, band_slug="band-a", release_slug="1999-a")

            with (
                patch("sys.argv", ["download_archive.py", "--bands-dir", str(bands_dir), "--release", "invalid-no-slash"]),
                patch("sys.stderr") as mock_stderr,
            ):
                exit_code = main()

            self.assertEqual(exit_code, 1)
            mock_stderr.write.assert_called()
            err_msg = "".join(call.args[0] for call in mock_stderr.write.call_args_list)
            self.assertIn("Error: --release must be in '<band-slug>/<release-slug>' format", err_msg)


if __name__ == "__main__":
    unittest.main()
