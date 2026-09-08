"""
Shared test harness for review_app's test suites: a real Flask app
against a real temp-file SQLite database and a fixture archive checkout,
with outbound SMTP mocked (the agreed seam - see the parent PRD's Testing
Decisions). Mirrors tools/archive_fixture.py's role for tools/'s own test
suites - extend this rather than re-deriving app/db/fixture setup in each
test file. Not itself a test file (no test_ prefix), so `unittest
discover -p 'test_*.py'` skips it, same as archive_fixture.py.
"""
from __future__ import annotations

import io
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from app import create_app  # noqa: E402
from archive_fixture import build_archive_with_nested_fields  # noqa: E402
from config import Config, GithubConfig, SmtpConfig  # noqa: E402

# Minimal byte fixtures for media_uploads.py's header-sniffing - only the
# leading signature bytes matter (see media_uploads.py's _sniff()), so
# these are padded with filler, not real decodable images/video.
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 128
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 128
MP4_BYTES = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 128
UNRECOGNIZED_BYTES = b"this is not an image or video file" * 4


class ReviewAppTestCase(unittest.TestCase):
    """Base class for review_app test suites."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp_path = Path(self._tmp.name)

        checkout_path = self._build_checkout(tmp_path)
        self.database_path = tmp_path / "review.db"
        self._build_app(checkout_path)

    def _build_checkout(self, tmp_path: Path) -> Path:
        """Hook for subclasses that need a differently-shaped checkout -
        e.g. test_apply_pipeline.py's git-backed one, needed to exercise
        the commit-and-push half of the apply pipeline that a plain
        directory can't. Default: a plain fixture directory, no git."""
        checkout_path = tmp_path / "checkout"
        self.fx = build_archive_with_nested_fields(checkout_path / "bands")
        return checkout_path

    def _build_app(self, checkout_path: Path) -> None:
        self.config = Config(
            database_path=self.database_path,
            archive_checkout_path=checkout_path,
            secret_key="test-secret",
            maintainer_email="maintainer@example.com",
            smtp=SmtpConfig(host="localhost", port=25, from_addr="noreply@example.com"),
            github=GithubConfig(token="test-github-token", repo="svalevka/daugavpils.fans"),
            callback_key="test-callback-key",
            rate_limit_per_ip_per_hour=5,
            login_rate_limit_per_ip_per_hour=5,
        )
        self.app = create_app(self.config)
        self.app.testing = True
        self.client = self.app.test_client()

        # No test ever touches real SMTP or the real GitHub API - both
        # mocked at the exact boundaries mail.py/github_dispatch.py
        # expose (the agreed seam - see the parent PRD's Testing
        # Decisions).
        # submissions.mail and media_submissions.mail are the *same*
        # cached module object (both just `import mail`), so patching
        # send_submission_notification under one import path also
        # affects the other - one patch, one mock, shared by both flows'
        # tests (patching it a second time under the other path would
        # silently clobber this one instead of adding independent
        # coverage).
        self.mock_send_notification = self._patch("submissions.mail.send_submission_notification")
        self.mock_send_media_notification = self.mock_send_notification
        self.mock_send_magic_link = self._patch("auth.mail.send_magic_link")
        self.mock_trigger_apply = self._patch("dashboard.github_dispatch.trigger_apply")
        self.mock_send_media_approved = self._patch("dashboard.mail.send_media_approved_notification")

    def _patch(self, target: str) -> mock.MagicMock:
        patcher = mock.patch(target)
        mocked = patcher.start()
        self.addCleanup(patcher.stop)
        return mocked

    def seed_approver(self, email: str, *, is_active: bool = True, display_name: str = "Test Approver") -> int:
        conn = sqlite3.connect(self.database_path)
        try:
            cur = conn.execute(
                "INSERT INTO approvers (email, display_name, is_active) VALUES (?, ?, ?)",
                (email, display_name, 1 if is_active else 0),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def login_as(self, approver_id: int) -> None:
        """Simulate an already-completed login - sets the session key
        auth.py's real /login/verify would set, without needing to send
        and click a real magic link for tests that aren't exercising the
        login flow itself."""
        with self.client.session_transaction() as sess:
            sess["approver_id"] = approver_id

    def fetch_proposals(self) -> list[sqlite3.Row]:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute("SELECT * FROM proposals ORDER BY id").fetchall()
        finally:
            conn.close()

    def fetch_media_proposals(self) -> list[sqlite3.Row]:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute("SELECT * FROM media_proposals ORDER BY id").fetchall()
        finally:
            conn.close()

    def callback_headers(self) -> dict[str, str]:
        """Auth header the GitHub Action uses against /api/* - see
        api.py's _require_callback_key()."""
        return {"Authorization": f"Bearer {self.config.callback_key}"}

    def submit(self, **form):
        base = {
            "band_slug": self.fx.band_slug,
            "release_slug": "",
            "target": "band",
            "field": "description",
            "list_index": "",
            "proposed_value": "Corrected biography text.",
            "submitter_name": "",
            "submitter_contact": "",
            "website": "",
        }
        base.update(form)
        return self.client.post("/submit", data=base)

    def submit_media(self, file_tuples: list[tuple[str, bytes]] | None = None, **form):
        """file_tuples: list of (filename, bytes) pairs - the bytes'
        actual leading signature is what media_uploads.py sniffs, not
        the filename or any guessed Content-Type (see JPEG_BYTES etc.
        above). Defaults to a single JPEG."""
        if file_tuples is None:
            file_tuples = [("photo.jpg", JPEG_BYTES)]
        base = {
            "band_slug": self.fx.band_slug,
            "release_slug": "",
            "submitter_name": "",
            "submitter_contact": "",
            "website": "",
        }
        base.update(form)
        base["files"] = [(io.BytesIO(data), filename) for filename, data in file_tuples]
        return self.client.post("/submit-media", data=base, content_type="multipart/form-data")
