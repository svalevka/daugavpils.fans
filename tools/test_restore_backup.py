#!/usr/bin/env python3
"""
Unit tests for tools/restore_backup.py (GitHub issue #60).
"""
from __future__ import annotations

import io
import shutil
import sqlite3
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import restore_backup


def create_test_db(db_path: Path) -> None:
    """Create a minimal valid review.db for tests."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE approvers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL
        );
    """)
    cur.execute("""
        CREATE TABLE proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            band_slug TEXT NOT NULL,
            status TEXT NOT NULL
        );
    """)
    cur.execute("INSERT INTO approvers (email, display_name) VALUES ('admin@example.com', 'Admin');")
    cur.execute("INSERT INTO proposals (band_slug, status) VALUES ('dvinsk', 'pending');")
    conn.commit()
    conn.close()


def create_test_tarball(
    tarball_path: Path,
    *,
    include_db: bool = True,
    corrupt_db: bool = False,
    include_uploads: bool = True,
) -> None:
    """Create a test backup tarball."""
    with tempfile.TemporaryDirectory() as stage_str:
        stage = Path(stage_str)
        if include_db:
            db_file = stage / "review.db"
            if corrupt_db:
                db_file.write_bytes(b"NOT_A_VALID_SQLITE_DATABASE_HEADER_CORRUPTED_BYTES")
            else:
                create_test_db(db_file)

        if include_uploads:
            uploads = stage / "uploads"
            uploads.mkdir()
            (uploads / "photo.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)
            (uploads / "audio.mp3").write_bytes(b"ID3" + b"\x00" * 200)

        with tarfile.open(tarball_path, "w:gz") as tar:
            for item in stage.iterdir():
                tar.add(item, arcname=item.name)


class RestoreBackupTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)
        self.tarball = self.root / "review-app-backup.tar.gz"
        self.target_dir = self.root / "target"

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_validate_tarball_success(self) -> None:
        create_test_tarball(self.tarball)
        members = restore_backup.validate_tarball(self.tarball)
        self.assertIn("review.db", members)
        self.assertIn("uploads", members)

    def test_validate_tarball_missing_file_raises_error(self) -> None:
        missing_tar = self.root / "nonexistent.tar.gz"
        with self.assertRaises(restore_backup.RestoreError) as ctx:
            restore_backup.validate_tarball(missing_tar)
        self.assertIn("not found", str(ctx.exception))

    def test_validate_tarball_corrupt_archive_raises_error(self) -> None:
        corrupt_tar = self.root / "corrupted.tar.gz"
        corrupt_tar.write_bytes(b"GARBAGE_NOT_GZIP_CONTENT")
        with self.assertRaises(restore_backup.RestoreError) as ctx:
            restore_backup.validate_tarball(corrupt_tar)
        self.assertIn("Corrupted or invalid tarball", str(ctx.exception))

    def test_validate_tarball_missing_review_db_raises_error(self) -> None:
        no_db_tar = self.root / "no_db.tar.gz"
        create_test_tarball(no_db_tar, include_db=False)
        with self.assertRaises(restore_backup.RestoreError) as ctx:
            restore_backup.validate_tarball(no_db_tar)
        self.assertIn("does not contain review.db", str(ctx.exception))

    def test_verify_sqlite_integrity_valid_db(self) -> None:
        db_path = self.root / "review.db"
        create_test_db(db_path)
        table_counts = restore_backup.verify_sqlite_integrity(db_path)
        self.assertEqual(table_counts["approvers"], 1)
        self.assertEqual(table_counts["proposals"], 1)

    def test_verify_sqlite_integrity_corrupted_db_raises_error(self) -> None:
        corrupt_db = self.root / "corrupt.db"
        corrupt_db.write_bytes(b"NOT_A_VALID_SQLITE_DATABASE")
        with self.assertRaises(restore_backup.RestoreError) as ctx:
            restore_backup.verify_sqlite_integrity(corrupt_db)
        self.assertTrue(
            "not a database" in str(ctx.exception) or "integrity check failed" in str(ctx.exception)
        )

    def test_restore_backup_full_execution(self) -> None:
        create_test_tarball(self.tarball)
        result = restore_backup.restore_backup(self.tarball, self.target_dir)

        self.assertTrue(result["database_valid"])
        self.assertEqual(result["table_counts"]["approvers"], 1)
        self.assertEqual(result["table_counts"]["proposals"], 1)
        self.assertEqual(result["upload_count"], 2)

        live_db = self.target_dir / "review.db"
        self.assertTrue(live_db.exists())
        self.assertTrue((self.target_dir / "uploads" / "photo.jpg").exists())
        self.assertTrue((self.target_dir / "uploads" / "audio.mp3").exists())

    def test_restore_backup_atomic_replacement_backs_up_live_db(self) -> None:
        # Seed existing live db in target_dir
        self.target_dir.mkdir(parents=True)
        live_db = self.target_dir / "review.db"
        live_db.write_text("OLD_LIVE_DATABASE_CONTENT")

        create_test_tarball(self.tarball)
        restore_backup.restore_backup(self.tarball, self.target_dir)

        # Pre-restore backup exists
        bak_db = self.target_dir / "review.db.pre_restore.bak"
        self.assertTrue(bak_db.exists())
        self.assertEqual(bak_db.read_text(), "OLD_LIVE_DATABASE_CONTENT")

        # Live DB is now the restored SQLite DB
        counts = restore_backup.verify_sqlite_integrity(live_db)
        self.assertEqual(counts["approvers"], 1)

    def test_restore_backup_dry_run_leaves_target_untouched(self) -> None:
        create_test_tarball(self.tarball)
        result = restore_backup.restore_backup(self.tarball, self.target_dir, dry_run=True)

        self.assertTrue(result["dry_run"])
        self.assertTrue(result["database_valid"])
        self.assertEqual(result["table_counts"]["approvers"], 1)
        self.assertFalse(self.target_dir.exists())

    def test_restore_backup_rejects_corrupted_archive_leaving_target_safe(self) -> None:
        corrupted_tar = self.root / "bad.tar.gz"
        create_test_tarball(corrupted_tar, corrupt_db=True)

        # Seed existing live db in target_dir
        self.target_dir.mkdir(parents=True)
        live_db = self.target_dir / "review.db"
        live_db.write_text("SAFE_ORIGINAL_CONTENT")

        with self.assertRaises(restore_backup.RestoreError):
            restore_backup.restore_backup(corrupted_tar, self.target_dir)

        # Original live_db is unmodified
        self.assertEqual(live_db.read_text(), "SAFE_ORIGINAL_CONTENT")

    @patch("restore_backup.shutil.which", return_value="/usr/bin/rclone")
    @patch("restore_backup.subprocess.run")
    def test_download_latest_from_b2(self, mock_run: MagicMock, mock_which: MagicMock) -> None:
        # Mock rclone lsf returning backup filenames
        lsf_output = "review-app-backup-20260901-033000.tar.gz\nreview-app-backup-20260915-033000.tar.gz\n"
        mock_run.side_effect = [
            MagicMock(stdout=lsf_output, returncode=0),  # rclone lsf
            MagicMock(returncode=0),                     # rclone copyto
        ]

        dest_dir = self.root / "downloads"
        dest_dir.mkdir()
        downloaded = restore_backup.download_latest_from_b2("test-bucket", dest_dir)

        self.assertEqual(downloaded.name, "review-app-backup-20260915-033000.tar.gz")
        self.assertEqual(mock_run.call_count, 2)
        # Check that rclone copyto was called with latest file
        copy_args = mock_run.call_args_list[1][0][0]
        self.assertIn("b2:test-bucket/review-app-backup-20260915-033000.tar.gz", copy_args)

    def test_cli_main_with_tarball_and_dry_run(self) -> None:
        create_test_tarball(self.tarball)
        exit_code = restore_backup.main([
            str(self.tarball),
            "--target-dir", str(self.target_dir),
            "--dry-run",
        ])
        self.assertEqual(exit_code, 0)
        self.assertFalse(self.target_dir.exists())

    def test_cli_main_missing_args(self) -> None:
        with patch("sys.stderr", new_callable=io.StringIO):
            with self.assertRaises(SystemExit) as ctx:
                restore_backup.main([])
            self.assertNotEqual(ctx.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
