#!/usr/bin/env python3
"""
tools/restore_backup.py

Disaster recovery tool for restoring the review app database (review.db)
and uploaded media files (/data/uploads) from Backblaze B2 or local backup
tarballs (GitHub issue #60).

Features:
- Validates backup tarball integrity (.tar.gz).
- Verifies SQLite database consistency via `PRAGMA integrity_check;`.
- Audits row counts across core tables (approvers, proposals, roles).
- Performs safe atomic replacement to target directory (defaulting to
  /opt/daugavpils-fans/review-app-data or custom target).
- Supports downloading the latest backup snapshot from Backblaze B2 using rclone.
- Supports --dry-run for non-destructive inspection and verification.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any

DEFAULT_PRODUCTION_DATA_DIR = Path("/opt/daugavpils-fans/review-app-data")
DEFAULT_B2_BUCKET = "daugavpils.fans"


class RestoreError(Exception):
    """Raised when backup verification or restoration fails."""


def validate_tarball(tarball_path: Path) -> list[str]:
    """Verify tarball integrity and return list of member filenames."""
    if not tarball_path.exists():
        raise RestoreError(f"Backup tarball not found: {tarball_path}")
    if not tarball_path.is_file():
        raise RestoreError(f"Specified path is not a file: {tarball_path}")

    try:
        with tarfile.open(tarball_path, "r:gz") as tar:
            members = tar.getnames()
    except (tarfile.TarError, OSError) as exc:
        raise RestoreError(f"Corrupted or invalid tarball archive: {exc}") from exc

    if "review.db" not in members:
        raise RestoreError("Backup tarball does not contain review.db")

    return members


def verify_sqlite_integrity(db_path: Path) -> dict[str, int]:
    """Execute PRAGMA integrity_check and collect row counts for core tables."""
    if not db_path.exists():
        raise RestoreError(f"Database file not found at {db_path}")

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise RestoreError(f"Failed to open SQLite database {db_path}: {exc}") from exc

    try:
        cur = conn.cursor()
        cur.execute("PRAGMA integrity_check;")
        check_result = cur.fetchall()
        if not check_result or check_result[0][0] != "ok":
            errors = "; ".join(str(r[0]) for r in check_result)
            raise RestoreError(f"SQLite database integrity check failed: {errors}")

        # Query all user tables and count rows
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
        tables = [row[0] for row in cur.fetchall()]
        table_counts: dict[str, int] = {}
        for table in tables:
            try:
                cur.execute(f"SELECT COUNT(*) FROM [{table}];")  # noqa: S608
                table_counts[table] = cur.fetchone()[0]
            except sqlite3.Error:
                table_counts[table] = -1

        return table_counts
    except sqlite3.Error as exc:
        raise RestoreError(f"Database query error during verification: {exc}") from exc
    finally:
        conn.close()


def download_latest_from_b2(bucket: str, dest_dir: Path) -> Path:
    """Download the newest review-app-backup-*.tar.gz from Backblaze B2 using rclone."""
    rclone_bin = shutil.which("rclone")
    if not rclone_bin:
        raise RestoreError("rclone binary not found on PATH. Required for Backblaze B2 downloads.")

    remote = f"b2:{bucket}"
    cmd = [rclone_bin, "lsf", remote]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as exc:
        raise RestoreError(f"Failed to list B2 bucket {remote}: {exc.stderr.strip() or exc}") from exc

    files = [f.strip() for f in res.stdout.splitlines() if f.strip().startswith("review-app-backup-") and f.strip().endswith(".tar.gz")]
    if not files:
        raise RestoreError(f"No review-app-backup-*.tar.gz files found in {remote}")

    latest_file = sorted(files)[-1]
    dest_path = dest_dir / latest_file
    copy_cmd = [rclone_bin, "copyto", f"{remote}/{latest_file}", str(dest_path)]
    try:
        subprocess.run(copy_cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as exc:
        raise RestoreError(f"Failed to download {latest_file} from {remote}: {exc.stderr.strip() or exc}") from exc

    return dest_path


def restore_backup(
    tarball_path: Path,
    target_dir: Path,
    *,
    dry_run: bool = False,
    chown_user_group: str | None = None,
) -> dict[str, Any]:
    """Safely verify and extract backup archive to target_dir with atomic replacement."""
    validate_tarball(tarball_path)

    with tempfile.TemporaryDirectory(prefix="daugavpils_restore_") as tmp_str:
        tmp_dir = Path(tmp_str)
        try:
            with tarfile.open(tarball_path, "r:gz") as tar:
                if hasattr(tarfile, "data_filter"):
                    tar.extractall(path=tmp_dir, filter="data")
                else:
                    tar.extractall(path=tmp_dir)
        except Exception as exc:
            raise RestoreError(f"Failed to unpack tarball archive: {exc}") from exc

        staging_db = tmp_dir / "review.db"
        table_counts = verify_sqlite_integrity(staging_db)

        staging_uploads = tmp_dir / "uploads"
        upload_count = 0
        upload_bytes = 0
        if staging_uploads.is_dir():
            for f in staging_uploads.rglob("*"):
                if f.is_file():
                    upload_count += 1
                    upload_bytes += f.stat().st_size

        result = {
            "tarball": str(tarball_path),
            "target_dir": str(target_dir),
            "dry_run": dry_run,
            "database_valid": True,
            "table_counts": table_counts,
            "upload_count": upload_count,
            "upload_bytes": upload_bytes,
        }

        if dry_run:
            return result

        # Execute live restoration
        target_dir.mkdir(parents=True, exist_ok=True)
        live_db = target_dir / "review.db"

        # Backup current live DB if it exists
        if live_db.exists():
            bak_db = target_dir / "review.db.pre_restore.bak"
            shutil.copy2(live_db, bak_db)

        # Atomic replacement of database
        temp_dest_db = target_dir / "review.db.incoming"
        shutil.copy2(staging_db, temp_dest_db)
        os.replace(temp_dest_db, live_db)

        # Restore uploads
        target_uploads = target_dir / "uploads"
        target_uploads.mkdir(parents=True, exist_ok=True)
        if staging_uploads.is_dir():
            for item in staging_uploads.iterdir():
                dest_item = target_uploads / item.name
                if item.is_dir():
                    shutil.copytree(item, dest_item, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, dest_item)

        # Optional chown
        if chown_user_group:
            _apply_chown(target_dir, chown_user_group)

        return result


def _apply_chown(path: Path, user_group: str) -> None:
    parts = user_group.split(":")
    user_str = parts[0]
    group_str = parts[1] if len(parts) > 1 else parts[0]
    import shutil as _sh
    chown_bin = _sh.which("chown")
    if chown_bin:
        try:
            subprocess.run([chown_bin, "-R", f"{user_str}:{group_str}", str(path)], check=True)
        except subprocess.CalledProcessError as exc:
            raise RestoreError(f"Failed to chown {path} to {user_group}: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify and restore review_app database and uploads from backup tarball or Backblaze B2."
    )
    parser.add_argument(
        "tarball",
        nargs="?",
        type=Path,
        help="Path to local backup tarball (.tar.gz). Optional if --download-b2 is used.",
    )
    parser.add_argument(
        "--target-dir",
        type=Path,
        default=None,
        help=f"Target directory to restore into (default: {DEFAULT_PRODUCTION_DATA_DIR} if present, else ./review-app-data)",
    )
    parser.add_argument(
        "--download-b2",
        action="store_true",
        help="Download latest backup from Backblaze B2 via rclone before restoring.",
    )
    parser.add_argument(
        "--bucket",
        default=os.environ.get("B2_BUCKET", DEFAULT_B2_BUCKET),
        help=f"Backblaze B2 bucket name (default: {DEFAULT_B2_BUCKET} or $B2_BUCKET).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate archive and test SQLite integrity without replacing live data.",
    )
    parser.add_argument(
        "--chown",
        dest="chown_user_group",
        help="Apply user:group permissions to target directory after restore (e.g. 'appuser:appuser' or '1000:1000').",
    )

    args = parser.parse_args(argv)

    # Determine target directory
    if args.target_dir:
        target_dir = args.target_dir
    elif DEFAULT_PRODUCTION_DATA_DIR.exists():
        target_dir = DEFAULT_PRODUCTION_DATA_DIR
    else:
        target_dir = Path.cwd() / "review-app-data"

    tmp_download_dir: tempfile.TemporaryDirectory | None = None
    try:
        if args.download_b2:
            print(f"Connecting to Backblaze B2 bucket '{args.bucket}' to fetch latest backup...")
            tmp_download_dir = tempfile.TemporaryDirectory(prefix="daugavpils_b2_")
            tarball_path = download_latest_from_b2(args.bucket, Path(tmp_download_dir.name))
            print(f"Downloaded latest snapshot: {tarball_path.name}")
        elif args.tarball:
            tarball_path = args.tarball
        else:
            parser.error("Specify either a backup tarball path or --download-b2.")

        print(f"{'[DRY RUN] ' if args.dry_run else ''}Restoring from: {tarball_path}")
        print(f"Target directory: {target_dir}")

        summary = restore_backup(
            tarball_path,
            target_dir,
            dry_run=args.dry_run,
            chown_user_group=args.chown_user_group,
        )

        print("\n=== Restore Verification Summary ===")
        print(f"SQLite Integrity: OK")
        print("Table Row Counts:")
        for tbl, count in sorted(summary["table_counts"].items()):
            print(f"  - {tbl}: {count}")
        print(f"Uploads: {summary['upload_count']} file(s) ({summary['upload_bytes'] / (1024 * 1024):.2f} MB)")

        if args.dry_run:
            print("\n[DRY RUN] All integrity checks passed. Live data was NOT modified.")
        else:
            print(f"\nSuccessfully restored review.db and uploads to {target_dir}.")
            print("To activate on cherry:")
            print("  cd /opt/daugavpils-fans && docker compose restart review-app")
        return 0

    except RestoreError as err:
        print(f"\nERROR: {err}", file=sys.stderr)
        return 1
    finally:
        if tmp_download_dir:
            tmp_download_dir.cleanup()


if __name__ == "__main__":
    sys.exit(main())
