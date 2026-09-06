#!/usr/bin/env python3
"""
Validator failure-mode reporting coverage: each defect scenario below
gets its own test proving tools/validate.py exits nonzero and reports a
specific, actionable message naming the actual problem -- not a generic
failure or a crash.
"""
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from archive_fixture import build_valid_archive, run_validate


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def dump_yaml(path: Path, data: dict) -> None:
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False))


class ValidatorFailureModeTest(unittest.TestCase):
    def test_missing_audio_file_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            bands_dir = Path(tmp) / "bands"
            fx = build_valid_archive(bands_dir)
            fx.audio_path.unlink()

            result = run_validate(bands_dir)

            self.assertEqual(result.returncode, 1)
            self.assertIn("references missing file", result.stdout)
            self.assertIn(fx.audio_path.name, result.stdout)

    def test_checksum_mismatch_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            bands_dir = Path(tmp) / "bands"
            fx = build_valid_archive(bands_dir)
            fx.audio_path.write_bytes(b"different content entirely")

            result = run_validate(bands_dir)

            self.assertEqual(result.returncode, 1)
            self.assertIn("checksum mismatch", result.stdout)
            self.assertIn(fx.audio_sha256, result.stdout)

    def test_missing_checksum_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            bands_dir = Path(tmp) / "bands"
            fx = build_valid_archive(bands_dir)
            data = load_yaml(fx.release_yaml)
            data["track"][0]["audio"]["identifier"] = []
            dump_yaml(fx.release_yaml, data)

            result = run_validate(bands_dir)

            self.assertEqual(result.returncode, 1)
            self.assertIn("no sha256 checksum recorded", result.stdout)

    def test_missing_duration_or_bitrate_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            bands_dir = Path(tmp) / "bands"
            fx = build_valid_archive(bands_dir)
            data = load_yaml(fx.release_yaml)
            del data["track"][0]["audio"]["duration"]
            del data["track"][0]["audio"]["bitrate"]
            dump_yaml(fx.release_yaml, data)

            result = run_validate(bands_dir)

            self.assertEqual(result.returncode, 1)
            self.assertIn("missing duration/bitrate", result.stdout)

    def test_band_slug_mismatch_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            bands_dir = Path(tmp) / "bands"
            fx = build_valid_archive(bands_dir)
            data = load_yaml(fx.band_yaml)
            data["slug"] = "not-the-folder-name"
            dump_yaml(fx.band_yaml, data)

            result = run_validate(bands_dir)

            self.assertEqual(result.returncode, 1)
            self.assertIn("does not match folder name", result.stdout)
            self.assertIn(fx.band_slug, result.stdout)

    def test_release_byartist_mismatch_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            bands_dir = Path(tmp) / "bands"
            fx = build_valid_archive(bands_dir)
            data = load_yaml(fx.release_yaml)
            data["byArtist"] = "some-other-band"
            dump_yaml(fx.release_yaml, data)

            result = run_validate(bands_dir)

            self.assertEqual(result.returncode, 1)
            self.assertIn("does not match parent band slug", result.stdout)
            self.assertIn(fx.band_slug, result.stdout)

    def test_schema_validation_failure_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            bands_dir = Path(tmp) / "bands"
            fx = build_valid_archive(bands_dir)
            data = load_yaml(fx.release_yaml)
            del data["license"]  # required field, no default
            dump_yaml(fx.release_yaml, data)

            result = run_validate(bands_dir)

            self.assertEqual(result.returncode, 1)
            self.assertIn("schema validation failed", result.stdout)
            self.assertIn("license", result.stdout)


if __name__ == "__main__":
    unittest.main()
