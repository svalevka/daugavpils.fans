#!/usr/bin/env python3
"""
media_uploads.py's content-sniffing and size-capped save (GitHub issue
#21), tested directly against the module rather than through HTTP - it
has no Flask/DB dependency of its own, so a real request isn't needed to
exercise it (contrast with test_media_submissions.py, which does go
through real HTTP for the parts that involve the app/DB/rate limiting).
"""
from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

import media_uploads  # noqa: E402
from test_support import JPEG_BYTES, MP4_BYTES, PNG_BYTES, UNRECOGNIZED_BYTES  # noqa: E402


def _file_storage(data: bytes):
    return SimpleNamespace(stream=io.BytesIO(data), filename="upload.bin")


class SniffingAndSavingTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.uploads_dir = Path(self._tmp.name) / "uploads"
        self.max_bytes = {"image": 1024 * 1024, "video": 10 * 1024 * 1024}

    def test_jpeg_is_recognized_as_image(self):
        stored_filename, content_type, media_type, size = media_uploads.save_upload(
            _file_storage(JPEG_BYTES), self.uploads_dir, self.max_bytes
        )
        self.assertEqual(media_type, "image")
        self.assertEqual(content_type, "image/jpeg")
        self.assertEqual(size, len(JPEG_BYTES))
        self.assertTrue((self.uploads_dir / stored_filename).exists())
        self.assertEqual((self.uploads_dir / stored_filename).read_bytes(), JPEG_BYTES)

    def test_png_is_recognized_as_image(self):
        _stored, content_type, media_type, _size = media_uploads.save_upload(
            _file_storage(PNG_BYTES), self.uploads_dir, self.max_bytes
        )
        self.assertEqual(media_type, "image")
        self.assertEqual(content_type, "image/png")

    def test_mp4_is_recognized_as_video(self):
        _stored, content_type, media_type, _size = media_uploads.save_upload(
            _file_storage(MP4_BYTES), self.uploads_dir, self.max_bytes
        )
        self.assertEqual(media_type, "video")
        self.assertEqual(content_type, "video/mp4")

    def test_two_uploads_get_different_stored_filenames(self):
        stored_a, *_ = media_uploads.save_upload(_file_storage(JPEG_BYTES), self.uploads_dir, self.max_bytes)
        stored_b, *_ = media_uploads.save_upload(_file_storage(JPEG_BYTES), self.uploads_dir, self.max_bytes)
        self.assertNotEqual(stored_a, stored_b)

    def test_unrecognized_content_is_rejected(self):
        with self.assertRaises(media_uploads.UploadRejected):
            media_uploads.save_upload(_file_storage(UNRECOGNIZED_BYTES), self.uploads_dir, self.max_bytes)
        self.assertEqual(list(self.uploads_dir.glob("*")) if self.uploads_dir.exists() else [], [])

    def test_oversized_file_is_rejected_and_leaves_no_partial_file(self):
        oversized = JPEG_BYTES + b"\x00" * (self.max_bytes["image"] * 2)
        with self.assertRaises(media_uploads.UploadRejected):
            media_uploads.save_upload(_file_storage(oversized), self.uploads_dir, self.max_bytes)
        self.assertEqual(list(self.uploads_dir.glob("*")), [])

    def test_video_uses_the_video_size_cap_not_the_image_one(self):
        # Bigger than the (small) image cap, but under the (bigger) video
        # cap - must succeed, proving the right cap was picked from the
        # sniffed type, not applied uniformly.
        big_video = MP4_BYTES + b"\x00" * (self.max_bytes["image"] * 2)
        self.assertLess(len(big_video), self.max_bytes["video"])
        _stored, _ct, media_type, size = media_uploads.save_upload(
            _file_storage(big_video), self.uploads_dir, self.max_bytes
        )
        self.assertEqual(media_type, "video")
        self.assertEqual(size, len(big_video))

    def test_total_storage_quota_rejected(self):
        # Allow one JPEG (len(JPEG_BYTES)), but reject second because quota is exceeded
        quota = len(JPEG_BYTES) + 10
        stored, *_ = media_uploads.save_upload(
            _file_storage(JPEG_BYTES), self.uploads_dir, self.max_bytes, max_total_storage_bytes=quota
        )
        self.assertTrue((self.uploads_dir / stored).exists())

        # Second upload should be rejected because current usage + size > quota
        with self.assertRaises(media_uploads.UploadRejected) as ctx:
            media_uploads.save_upload(
                _file_storage(JPEG_BYTES), self.uploads_dir, self.max_bytes, max_total_storage_bytes=quota
            )
        self.assertIn("quota exceeded", str(ctx.exception))

    def test_low_disk_free_space_rejected(self):
        import shutil
        from unittest.mock import patch

        # Mock shutil.disk_usage to simulate low free space
        fake_usage = shutil._ntuple_diskusage(100 * 1024 * 1024, 99 * 1024 * 1024, 500 * 1024)
        with patch("shutil.disk_usage", return_value=fake_usage):
            with self.assertRaises(media_uploads.UploadRejected) as ctx:
                media_uploads.save_upload(
                    _file_storage(JPEG_BYTES),
                    self.uploads_dir,
                    self.max_bytes,
                    min_disk_free_bytes=10 * 1024 * 1024,
                )
            self.assertIn("storage space is low", str(ctx.exception))

    def test_strips_sensitive_exif_on_image_upload(self):
        from PIL import Image

        img = Image.new("RGB", (64, 48), color="cyan")
        exif = img.getexif()
        exif[0x0112] = 6  # Orientation
        exif[0x010F] = "CameraBrand"
        exif[0x0110] = "CameraModelX"
        gps = exif.get_ifd(0x8825)
        gps[1] = "N"
        gps[2] = (55.0, 52.0, 0.0)

        buf = io.BytesIO()
        img.save(buf, format="JPEG", exif=exif)
        raw_bytes = buf.getvalue()

        stored_filename, content_type, media_type, size = media_uploads.save_upload(
            _file_storage(raw_bytes), self.uploads_dir, self.max_bytes
        )

        saved_path = self.uploads_dir / stored_filename
        self.assertTrue(saved_path.exists())

        with Image.open(saved_path) as saved_img:
            saved_exif = saved_img.getexif()
            self.assertEqual(saved_exif.get(0x0112), 6)
            self.assertNotIn(0x8825, saved_exif)
            self.assertFalse(bool(saved_exif.get_ifd(0x8825)))
            self.assertNotIn(0x010F, saved_exif)
            self.assertNotIn(0x0110, saved_exif)


if __name__ == "__main__":
    unittest.main()
