#!/usr/bin/env python3
"""
The public /submit-media flow (GitHub issue #21), exercised end to end
through real HTTP requests, mirroring test_submissions.py's approach for
the text-proposal flow.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_support import JPEG_BYTES, MP4_BYTES, UNRECOGNIZED_BYTES, ReviewAppTestCase  # noqa: E402


class ValidSubmissionTest(ReviewAppTestCase):
    def test_creates_a_pending_row_per_file(self):
        response = self.submit_media()
        self.assertEqual(response.status_code, 201)

        rows = self.fetch_media_proposals()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["status"], "pending")
        self.assertEqual(row["band_slug"], self.fx.band_slug)
        self.assertIsNone(row["release_slug"])
        self.assertEqual(row["media_type"], "image")
        self.assertEqual(row["content_type"], "image/jpeg")
        self.assertEqual(row["original_filename"], "photo.jpg")
        self.assertIsNone(row["submitted_by_approver_id"])

    def test_stores_the_uploaded_bytes_on_disk(self):
        self.submit_media()
        row = self.fetch_media_proposals()[0]
        stored_path = self.config.resolved_media_uploads_path() / row["stored_filename"]
        self.assertEqual(stored_path.read_bytes(), JPEG_BYTES)

    def test_release_scoped_submission(self):
        response = self.submit_media(release_slug=self.fx.release_slug)
        self.assertEqual(response.status_code, 201)
        row = self.fetch_media_proposals()[0]
        self.assertEqual(row["release_slug"], self.fx.release_slug)

    def test_batch_upload_creates_one_independent_row_per_file_with_its_own_caption(self):
        response = self.submit_media(
            file_tuples=[("a.jpg", JPEG_BYTES), ("b.mp4", MP4_BYTES)],
            caption_0="From the 1998 festival",
            caption_1="Live footage, different gig entirely",
        )
        self.assertEqual(response.status_code, 201)

        rows = self.fetch_media_proposals()
        self.assertEqual(len(rows), 2)
        by_name = {row["original_filename"]: row for row in rows}
        self.assertEqual(by_name["a.jpg"]["caption"], "From the 1998 festival")
        self.assertEqual(by_name["a.jpg"]["media_type"], "image")
        self.assertEqual(by_name["b.mp4"]["caption"], "Live footage, different gig entirely")
        self.assertEqual(by_name["b.mp4"]["media_type"], "video")

    def test_triggers_a_maintainer_notification_email(self):
        self.submit_media()
        self.mock_send_media_notification.assert_called_once()

    def test_stamps_submission_from_a_logged_in_approver(self):
        approver_id = self.seed_approver("ryb@example.com")
        self.login_as(approver_id)

        self.submit_media()

        row = self.fetch_media_proposals()[0]
        self.assertEqual(row["submitted_by_approver_id"], approver_id)


class UnrecognizedAndOversizedFileTest(ReviewAppTestCase):
    def test_unrecognized_file_is_skipped_not_saved(self):
        response = self.submit_media(file_tuples=[("mystery.bin", UNRECOGNIZED_BYTES)])
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.fetch_media_proposals(), [])
        self.assertIn(b"mystery.bin", response.data)

    def test_one_bad_file_in_a_batch_does_not_sink_the_others(self):
        response = self.submit_media(
            file_tuples=[("good.jpg", JPEG_BYTES), ("bad.bin", UNRECOGNIZED_BYTES)]
        )
        self.assertEqual(response.status_code, 201)

        rows = self.fetch_media_proposals()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["original_filename"], "good.jpg")
        self.assertIn(b"bad.bin", response.data)

    def test_oversized_photo_is_rejected(self):
        oversized = JPEG_BYTES + b"\x00" * self.config.max_photo_upload_bytes
        response = self.submit_media(file_tuples=[("huge.jpg", oversized)])
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.fetch_media_proposals(), [])
        self.assertIn(b"exceeds", response.data)


class HoneypotAndRateLimitTest(ReviewAppTestCase):
    def test_filled_honeypot_produces_no_row(self):
        response = self.submit_media(website="I am a bot")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.fetch_media_proposals(), [])
        self.mock_send_media_notification.assert_not_called()

    def test_exceeding_rate_limit_rejects_further_submissions(self):
        for _ in range(self.config.rate_limit_per_ip_per_hour):
            response = self.submit_media()
            self.assertEqual(response.status_code, 201)

        response = self.submit_media()
        self.assertEqual(response.status_code, 429)

    def test_a_batch_of_several_files_counts_once_against_the_rate_limit(self):
        response = self.submit_media(file_tuples=[("a.jpg", JPEG_BYTES), ("b.jpg", JPEG_BYTES)])
        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(self.fetch_media_proposals()), 2)

        # One batch submission = one hit against the hourly limit, same
        # as a single-file submission would be - not one hit per file.
        for _ in range(self.config.rate_limit_per_ip_per_hour - 1):
            self.submit_media()
        response = self.submit_media()
        self.assertEqual(response.status_code, 429)


class UnknownScopeTest(ReviewAppTestCase):
    def test_unknown_band_is_rejected(self):
        response = self.submit_media(band_slug="does-not-exist")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.fetch_media_proposals(), [])

    def test_unknown_release_is_rejected(self):
        response = self.submit_media(release_slug="does-not-exist")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.fetch_media_proposals(), [])

    def test_no_files_at_all_is_rejected(self):
        response = self.submit_media(file_tuples=[])
        self.assertEqual(response.status_code, 400)


class NavigationPagesTest(ReviewAppTestCase):
    def test_band_media_form_shows_for_a_real_band(self):
        response = self.client.get(f"/submit/{self.fx.band_slug}/media")
        self.assertEqual(response.status_code, 200)

    def test_band_media_form_404s_for_an_unknown_band(self):
        response = self.client.get("/submit/does-not-exist/media")
        self.assertEqual(response.status_code, 404)

    def test_release_media_form_shows_for_a_real_release(self):
        response = self.client.get(f"/submit/{self.fx.band_slug}/{self.fx.release_slug}/media")
        self.assertEqual(response.status_code, 200)

    def test_pick_target_page_links_to_the_media_form(self):
        response = self.client.get(f"/submit/{self.fx.band_slug}")
        self.assertIn(f"/submit/{self.fx.band_slug}/media".encode(), response.data)


if __name__ == "__main__":
    unittest.main()
