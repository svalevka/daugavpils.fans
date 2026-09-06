#!/usr/bin/env python3
"""
--write computation and idempotency coverage: running tools/validate.py
--write against a fixture missing checksum/duration/bitrate computes
correct values (verified independently, not by trusting validate.py's own
math) and persists them, and a subsequent plain run then passes cleanly
with no further changes.
"""
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from archive_fixture import build_archive_missing_av_info, run_validate


def independently_computed_av_info(audio_path: Path) -> tuple[str, str]:
    """Compute expected (iso8601_duration, bitrate_str) the same way a
    human double-checking validate.py's output would: a separate ffprobe
    invocation, not validate.py's own code."""
    probe = subprocess.run(
        [
            "ffprobe",
            "-v", "quiet",
            "-show_entries", "format=duration,bit_rate",
            "-of", "json",
            str(audio_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(probe.stdout)["format"]
    seconds = float(data["duration"])
    minutes, secs = divmod(round(seconds), 60)
    duration = f"PT{minutes}M{secs}S" if minutes else f"PT{secs}S"
    bitrate_kbps = round(int(data["bit_rate"]) / 1000)
    return duration, f"{bitrate_kbps} kbps"


class WriteComputationAndIdempotencyTest(unittest.TestCase):
    def test_write_computes_correct_values_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            bands_dir = Path(tmp) / "bands"
            fx = build_archive_missing_av_info(bands_dir)

            expected_sha256 = hashlib.sha256(fx.audio_path.read_bytes()).hexdigest()
            expected_duration, expected_bitrate = independently_computed_av_info(fx.audio_path)

            write_result = run_validate(bands_dir, "--write")
            self.assertEqual(write_result.returncode, 0, msg=write_result.stdout + write_result.stderr)

            written = yaml.safe_load(fx.release_yaml.read_text())
            audio = written["track"][0]["audio"]
            recorded_sha256 = next(
                pv["value"] for pv in audio["identifier"] if pv["propertyID"] == "sha256"
            )

            self.assertEqual(recorded_sha256, expected_sha256)
            self.assertEqual(audio["duration"], expected_duration)
            self.assertEqual(audio["bitrate"], expected_bitrate)

            # A subsequent plain run (no --write) passes cleanly, and the
            # YAML is left exactly as --write persisted it.
            plain_result = run_validate(bands_dir)
            self.assertEqual(plain_result.returncode, 0, msg=plain_result.stdout + plain_result.stderr)
            self.assertIn("OK", plain_result.stdout)
            self.assertEqual(yaml.safe_load(fx.release_yaml.read_text()), written)


if __name__ == "__main__":
    unittest.main()
