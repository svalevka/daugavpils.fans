#!/usr/bin/env python3
"""
Tests for tools/exif_cleanup.py (GitHub issue #53).
Verifies that sensitive EXIF metadata (GPS coordinates, device serial numbers,
make, model, maker notes) is completely stripped while preserving image
display orientation and visual dimensions.
"""
from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))

import exif_cleanup  # noqa: E402


def _make_test_image_with_exif(
    orientation: int = 6,
    with_gps: bool = True,
    make: str = "TestCamera",
    model: str = "TestModel-123",
) -> bytes:
    img = Image.new("RGB", (64, 48), color="teal")
    exif = img.getexif()
    exif[0x0112] = orientation
    if make:
        exif[0x010F] = make
    if model:
        exif[0x0110] = model

    if with_gps:
        gps_ifd = exif.get_ifd(0x8825)
        gps_ifd[1] = "N"
        gps_ifd[2] = (55.0, 52.0, 12.0)
        gps_ifd[3] = "E"
        gps_ifd[4] = (26.0, 31.0, 45.0)

    # Exif sub-IFD with maker note
    sub_ifd = exif.get_ifd(0x8769)
    sub_ifd[0x927C] = b"secret_maker_note_serial_xyz"
    sub_ifd[0x9003] = "2024:05:01 10:00:00"

    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif)
    return buf.getvalue()


class ExifCleanupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)

    def test_strip_sensitive_exif_removes_gps_and_device_info(self):
        img_path = self.tmp_path / "gps_photo.jpg"
        img_path.write_bytes(_make_test_image_with_exif(orientation=6, with_gps=True))

        stripped = exif_cleanup.strip_sensitive_exif(img_path)
        self.assertTrue(stripped)

        with Image.open(img_path) as sanitized:
            ex = sanitized.getexif()
            # Orientation is preserved
            self.assertEqual(ex.get(0x0112), 6)
            # GPS IFD is stripped
            self.assertFalse(bool(ex.get_ifd(0x8825)))
            self.assertNotIn(0x8825, ex)
            # Device identifiers stripped
            self.assertNotIn(0x010F, ex)  # Make
            self.assertNotIn(0x0110, ex)  # Model
            # MakerNote in sub-IFD stripped
            if 0x8769 in ex:
                self.assertNotIn(0x927C, ex.get_ifd(0x8769))
            # Dimensions intact
            self.assertEqual(sanitized.size, (64, 48))

    def test_idempotence_does_not_remodify_clean_file(self):
        img_path = self.tmp_path / "photo.jpg"
        img_path.write_bytes(_make_test_image_with_exif(orientation=1, with_gps=True))

        # First pass strips
        self.assertTrue(exif_cleanup.strip_sensitive_exif(img_path))
        mtime_after_first = img_path.stat().st_mtime_ns

        # Second pass does nothing and leaves file untouched
        self.assertFalse(exif_cleanup.strip_sensitive_exif(img_path))
        self.assertEqual(img_path.stat().st_mtime_ns, mtime_after_first)

    def test_clean_image_without_sensitive_tags_is_untouched(self):
        img_path = self.tmp_path / "plain.jpg"
        img = Image.new("RGB", (32, 32), color="purple")
        img.save(img_path, format="JPEG")
        mtime = img_path.stat().st_mtime_ns

        stripped = exif_cleanup.strip_sensitive_exif(img_path)
        self.assertFalse(stripped)
        self.assertEqual(img_path.stat().st_mtime_ns, mtime)

    def test_unreadable_or_missing_file_returns_false(self):
        self.assertFalse(exif_cleanup.strip_sensitive_exif(self.tmp_path / "nonexistent.jpg"))

        corrupt_path = self.tmp_path / "corrupt.jpg"
        corrupt_path.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 30)
        self.assertFalse(exif_cleanup.strip_sensitive_exif(corrupt_path))


if __name__ == "__main__":
    unittest.main()
