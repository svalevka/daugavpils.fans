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
from download_archive import download_release, load_band, load_release, main


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


if __name__ == "__main__":
    unittest.main()
