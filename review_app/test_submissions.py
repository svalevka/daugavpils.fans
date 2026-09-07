#!/usr/bin/env python3
"""
The public /submit flow (GitHub issue #11), exercised end to end through
real HTTP requests against a real Flask app instance and a real temp-file
SQLite database - the agreed seam (see the parent PRD's Testing
Decisions): review_app is tested through its actual routes, not by
calling its internal functions directly.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from app import create_app  # noqa: E402
from config import Config  # noqa: E402

from archive_fixture import build_archive_with_nested_fields  # noqa: E402


def _seed_approver(database_path: Path, email: str, *, is_active: bool = True) -> int:
    conn = sqlite3.connect(database_path)
    try:
        cur = conn.execute(
            "INSERT INTO approvers (email, display_name, is_active) VALUES (?, ?, ?)",
            (email, "Test Approver", 1 if is_active else 0),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _fetch_proposals(database_path: Path) -> list[sqlite3.Row]:
    conn = sqlite3.connect(database_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT * FROM proposals ORDER BY id").fetchall()
    finally:
        conn.close()


class ReviewAppTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp_path = Path(self._tmp.name)

        checkout_path = tmp_path / "checkout"
        self.fx = build_archive_with_nested_fields(checkout_path / "bands")

        self.database_path = tmp_path / "review.db"
        self.config = Config(
            database_path=self.database_path,
            archive_checkout_path=checkout_path,
            secret_key="test-secret",
            rate_limit_per_ip_per_hour=5,
        )
        self.app = create_app(self.config)
        self.app.testing = True
        self.client = self.app.test_client()

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


class ValidSubmissionTest(ReviewAppTestCase):
    def test_creates_pending_row_with_server_read_original_value(self):
        response = self.submit()
        self.assertEqual(response.status_code, 201)

        rows = _fetch_proposals(self.database_path)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["status"], "pending")
        self.assertEqual(row["band_slug"], self.fx.band_slug)
        self.assertEqual(row["target"], "band")
        self.assertEqual(row["field"], "description")
        self.assertEqual(json.loads(row["original_value"]), "Original band description.")
        self.assertEqual(json.loads(row["proposed_value"]), "Corrected biography text.")
        self.assertIsNone(row["submitted_by_approver_id"])

    def test_list_field_round_trips_between_form_lines_and_stored_list(self):
        response = self.submit(
            target="band", field="genre", proposed_value="post-punk\ncoldwave\n\n  \n"
        )
        self.assertEqual(response.status_code, 201)

        row = _fetch_proposals(self.database_path)[0]
        self.assertEqual(json.loads(row["original_value"]), ["post-punk"])
        self.assertEqual(json.loads(row["proposed_value"]), ["post-punk", "coldwave"])

    def test_nested_field_submission_by_index(self):
        response = self.submit(target="member", field="role_en", list_index="0", proposed_value="lead vocals")
        self.assertEqual(response.status_code, 201)

        row = _fetch_proposals(self.database_path)[0]
        self.assertEqual(row["list_index"], 0)
        self.assertEqual(json.loads(row["original_value"]), "vocals (EN)")

    def test_release_scoped_submission(self):
        response = self.submit(
            release_slug=self.fx.release_slug, target="release", field="description_en",
            proposed_value="Corrected note.",
        )
        self.assertEqual(response.status_code, 201)
        row = _fetch_proposals(self.database_path)[0]
        self.assertEqual(row["release_slug"], self.fx.release_slug)


class HoneypotAndRateLimitTest(ReviewAppTestCase):
    def test_filled_honeypot_produces_no_row(self):
        response = self.submit(website="I am a bot")
        # Same status code (201) as a real success - a bot shouldn't be
        # able to tell the honeypot caught it from the response alone.
        self.assertEqual(response.status_code, 201)
        self.assertEqual(_fetch_proposals(self.database_path), [])

    def test_exceeding_rate_limit_rejects_further_submissions(self):
        for _ in range(self.config.rate_limit_per_ip_per_hour):
            response = self.submit()
            self.assertEqual(response.status_code, 201)

        response = self.submit()
        self.assertEqual(response.status_code, 429)
        self.assertEqual(len(_fetch_proposals(self.database_path)), self.config.rate_limit_per_ip_per_hour)

    def test_honeypot_hits_still_count_toward_the_rate_limit(self):
        for _ in range(self.config.rate_limit_per_ip_per_hour):
            response = self.submit(website="bot")
            self.assertEqual(response.status_code, 201)

        response = self.submit()  # a real attempt, but the limit is already used up
        self.assertEqual(response.status_code, 429)


class DisallowedFieldTest(ReviewAppTestCase):
    def test_field_not_on_the_allowlist_is_rejected(self):
        response = self.submit(target="band", field="slug", proposed_value="hijacked")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(_fetch_proposals(self.database_path), [])

    def test_unknown_band_is_rejected(self):
        response = self.submit(band_slug="does-not-exist")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(_fetch_proposals(self.database_path), [])

    def test_unknown_release_is_rejected(self):
        response = self.submit(
            release_slug="does-not-exist", target="release", field="description"
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(_fetch_proposals(self.database_path), [])


class SelfApprovalStampingTest(ReviewAppTestCase):
    def test_logged_in_approver_submission_is_stamped(self):
        approver_id = _seed_approver(self.database_path, "ryb@example.com")
        with self.client.session_transaction() as sess:
            sess["approver_id"] = approver_id

        self.submit()

        row = _fetch_proposals(self.database_path)[0]
        self.assertEqual(row["submitted_by_approver_id"], approver_id)

    def test_anonymous_submission_with_matching_contact_email_is_stamped(self):
        approver_id = _seed_approver(self.database_path, "ryb@example.com")

        # Case-different on purpose - email matching shouldn't be case-sensitive.
        self.submit(submitter_contact="Ryb@Example.com")

        row = _fetch_proposals(self.database_path)[0]
        self.assertEqual(row["submitted_by_approver_id"], approver_id)

    def test_ordinary_submission_is_unstamped(self):
        _seed_approver(self.database_path, "ryb@example.com")

        self.submit(submitter_contact="someone-else@example.com")

        row = _fetch_proposals(self.database_path)[0]
        self.assertIsNone(row["submitted_by_approver_id"])

    def test_inactive_approver_email_does_not_stamp(self):
        _seed_approver(self.database_path, "retired@example.com", is_active=False)

        self.submit(submitter_contact="retired@example.com")

        row = _fetch_proposals(self.database_path)[0]
        self.assertIsNone(row["submitted_by_approver_id"])

    def test_stale_session_referencing_a_deactivated_approver_does_not_stamp(self):
        approver_id = _seed_approver(self.database_path, "ryb@example.com", is_active=False)
        with self.client.session_transaction() as sess:
            sess["approver_id"] = approver_id

        self.submit()

        row = _fetch_proposals(self.database_path)[0]
        self.assertIsNone(row["submitted_by_approver_id"])


class NavigationPagesTest(ReviewAppTestCase):
    def test_band_picker_lists_the_band(self):
        response = self.client.get("/submit")
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.fx.band_slug.encode(), response.data)

    def test_edit_form_shows_the_live_current_value(self):
        response = self.client.get(f"/submit/{self.fx.band_slug}/edit?target=band&field=description")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Original band description.", response.data)

    def test_edit_form_for_unknown_field_is_not_found(self):
        response = self.client.get(f"/submit/{self.fx.band_slug}/edit?target=band&field=slug")
        self.assertEqual(response.status_code, 404)

    def test_band_target_page_lists_only_band_scoped_targets(self):
        # Regression check: band_image/release_image (and band_video/
        # release_video) both back onto the model's `.image`/`.video`
        # attribute name via NESTED_LIST_ATTR, so a band and a release
        # both structurally *have* an `.image` list - it's easy to
        # accidentally list a band's photos under the release_image
        # target (or vice versa) if the picker isn't scoped explicitly.
        response = self.client.get(f"/submit/{self.fx.band_slug}")
        self.assertEqual(response.status_code, 200)
        body = response.data
        self.assertIn(b"target=band_image", body)
        self.assertIn(b"target=member", body)
        self.assertNotIn(b"target=release_image", body)
        self.assertNotIn(b"target=release_video", body)
        self.assertNotIn(b"target=track", body)

    def test_release_target_page_lists_only_release_scoped_targets(self):
        response = self.client.get(f"/submit/{self.fx.band_slug}/{self.fx.release_slug}")
        self.assertEqual(response.status_code, 200)
        body = response.data
        self.assertIn(b"target=release_image", body)
        self.assertIn(b"target=track", body)
        self.assertNotIn(b"target=band_image", body)
        self.assertNotIn(b"target=band_video", body)
        self.assertNotIn(b"target=member", body)


if __name__ == "__main__":
    unittest.main()
