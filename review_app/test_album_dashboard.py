#!/usr/bin/env python3
"""
Unit and integration tests for album proposal dashboard management
(GitHub issue #20):
- Listing pending and approved album proposals
- Approving an album proposal (with self-approval prevention)
- Rejecting an album proposal (with cleanup of staged files)
- Triggering GitHub action upload dispatch
- Marking as manually published (with cleanup of staged files)
- Serving cover and track audio previews to logged-in approvers
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_support import JPEG_BYTES, MP3_BYTES, ReviewAppTestCase  # noqa: E402

MOCK_PROBE_RESULT = {
    "duration_iso": "PT3M15S",
    "duration_seconds": 195.0,
    "bitrate": "320 kbps",
    "tags": {"title": "Track Title"},
    "ai_flags": [],
}


class AlbumDashboardAuthTest(ReviewAppTestCase):
    @patch("audio_validation.probe_audio_file", return_value=MOCK_PROBE_RESULT)
    def test_anonymous_access_blocked_for_album_actions(self, _mock_probe):
        self.submit_album(cover_tuple=("cover.jpg", JPEG_BYTES))
        prop_id = self.fetch_album_proposals()[0]["id"]

        self.assertEqual(self.client.post(f"/album-proposals/{prop_id}/approve").status_code, 401)
        self.assertEqual(self.client.post(f"/album-proposals/{prop_id}/reject").status_code, 401)
        self.assertEqual(self.client.post(f"/album-proposals/{prop_id}/upload").status_code, 401)
        self.assertEqual(self.client.post(f"/album-proposals/{prop_id}/publish").status_code, 401)
        self.assertEqual(self.client.get(f"/album-proposals/{prop_id}/cover").status_code, 302)
        self.assertEqual(self.client.get(f"/album-proposals/{prop_id}/tracks/1/file").status_code, 302)


class AlbumDashboardListingTest(ReviewAppTestCase):
    @patch("audio_validation.probe_audio_file", return_value=MOCK_PROBE_RESULT)
    def test_dashboard_lists_pending_and_awaiting_publish_albums(self, _mock_probe):
        approver_id = self.seed_approver("curator@example.com")
        self.submit_album(name="Pending Release 1995", cover_tuple=("cover.jpg", JPEG_BYTES))

        self.login_as(approver_id)
        resp = self.client.get("/dashboard")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Pending Release 1995", resp.data)
        self.assertIn(b"/album-proposals/", resp.data)

        # Now approve it
        prop_id = self.fetch_album_proposals()[0]["id"]
        approve_resp = self.client.post(f"/album-proposals/{prop_id}/approve")
        self.assertEqual(approve_resp.status_code, 302)

        resp2 = self.client.get("/dashboard")
        self.assertEqual(resp2.status_code, 200)
        self.assertIn(b"Pending Release 1995", resp2.data)
        # Verify "Upload & Publish" button is shown for approved proposal
        self.assertIn(b"/upload", resp2.data)


class AlbumDecisionTest(ReviewAppTestCase):
    @patch("audio_validation.probe_audio_file", return_value=MOCK_PROBE_RESULT)
    def test_approver_can_approve_proposal(self, _mock_probe):
        approver_id = self.seed_approver("curator@example.com")
        self.submit_album(name="Album To Approve")
        prop_id = self.fetch_album_proposals()[0]["id"]

        self.login_as(approver_id)
        resp = self.client.post(f"/album-proposals/{prop_id}/approve")
        self.assertEqual(resp.status_code, 302)

        prop = self.fetch_album_proposals()[0]
        self.assertEqual(prop["status"], "approved")
        self.assertEqual(prop["decided_by"], approver_id)
        self.assertIsNotNone(prop["decided_at"])

    @patch("audio_validation.probe_audio_file", return_value=MOCK_PROBE_RESULT)
    def test_self_approval_is_forbidden(self, _mock_probe):
        approver_id = self.seed_approver("submitter_approver@example.com")
        self.login_as(approver_id)
        self.submit_album(name="My Own Album")
        prop_id = self.fetch_album_proposals()[0]["id"]

        # Attempt to self-approve
        resp = self.client.post(f"/album-proposals/{prop_id}/approve")
        self.assertEqual(resp.status_code, 403)

        prop = self.fetch_album_proposals()[0]
        self.assertEqual(prop["status"], "pending")
        self.assertIsNone(prop["decided_by"])

    @patch("audio_validation.probe_audio_file", return_value=MOCK_PROBE_RESULT)
    def test_rejecting_unlinks_staged_audio_and_cover_files(self, _mock_probe):
        approver_id = self.seed_approver("curator@example.com")
        self.submit_album(name="Album To Reject", cover_tuple=("cover.jpg", JPEG_BYTES))
        prop = self.fetch_album_proposals()[0]
        prop_id = prop["id"]

        cover_path = Path(self.config.resolved_media_uploads_path()) / prop["cover_stored_filename"]
        self.assertTrue(cover_path.exists())

        tracks = json.loads(prop["tracks_json"])
        track_path = Path(self.config.resolved_media_uploads_path()) / tracks[0]["stored_filename"]
        self.assertTrue(track_path.exists())

        self.login_as(approver_id)
        resp = self.client.post(f"/album-proposals/{prop_id}/reject")
        self.assertEqual(resp.status_code, 302)

        prop_after = self.fetch_album_proposals()[0]
        self.assertEqual(prop_after["status"], "rejected")
        self.assertEqual(prop_after["decided_by"], approver_id)

        # Confirm files were unlinked
        self.assertFalse(cover_path.exists())
        self.assertFalse(track_path.exists())
        self.mock_trigger_album_apply.assert_not_called()

    @patch("audio_validation.probe_audio_file", return_value=MOCK_PROBE_RESULT)
    def test_deciding_twice_fails_with_conflict(self, _mock_probe):
        approver_a = self.seed_approver("a@example.com")
        approver_b = self.seed_approver("b@example.com")
        self.submit_album()
        prop_id = self.fetch_album_proposals()[0]["id"]

        self.login_as(approver_a)
        resp1 = self.client.post(f"/album-proposals/{prop_id}/approve")
        self.assertEqual(resp1.status_code, 302)

        self.login_as(approver_b)
        resp2 = self.client.post(f"/album-proposals/{prop_id}/approve")
        self.assertEqual(resp2.status_code, 409)


class AlbumUploadAndPublishTest(ReviewAppTestCase):
    @patch("audio_validation.probe_audio_file", return_value=MOCK_PROBE_RESULT)
    def test_upload_dispatches_workflow(self, _mock_probe):
        approver_id = self.seed_approver("curator@example.com")
        self.submit_album()
        prop_id = self.fetch_album_proposals()[0]["id"]

        self.login_as(approver_id)
        self.client.post(f"/album-proposals/{prop_id}/approve")

        upload_resp = self.client.post(f"/album-proposals/{prop_id}/upload")
        self.assertEqual(upload_resp.status_code, 302)

        prop = self.fetch_album_proposals()[0]
        self.assertEqual(prop["status"], "publishing")
        self.mock_trigger_album_apply.assert_called_once_with(self.config.github, prop_id)

    @patch("audio_validation.probe_audio_file", return_value=MOCK_PROBE_RESULT)
    def test_upload_handles_dispatch_oserror(self, _mock_probe):
        approver_id = self.seed_approver("curator@example.com")
        self.submit_album()
        prop_id = self.fetch_album_proposals()[0]["id"]

        self.login_as(approver_id)
        self.client.post(f"/album-proposals/{prop_id}/approve")

        self.mock_trigger_album_apply.side_effect = OSError("GitHub unreachable")
        upload_resp = self.client.post(f"/album-proposals/{prop_id}/upload")
        self.assertEqual(upload_resp.status_code, 302)

        prop = self.fetch_album_proposals()[0]
        self.assertEqual(prop["status"], "publish_failed")
        self.assertIn("Failed to dispatch", prop["publish_error"])

    @patch("audio_validation.probe_audio_file", return_value=MOCK_PROBE_RESULT)
    def test_manual_publish_unlinks_files(self, _mock_probe):
        approver_id = self.seed_approver("curator@example.com")
        self.submit_album(cover_tuple=("cover.jpg", JPEG_BYTES))
        prop = self.fetch_album_proposals()[0]
        prop_id = prop["id"]

        cover_path = Path(self.config.resolved_media_uploads_path()) / prop["cover_stored_filename"]
        tracks = json.loads(prop["tracks_json"])
        track_path = Path(self.config.resolved_media_uploads_path()) / tracks[0]["stored_filename"]

        self.login_as(approver_id)
        self.client.post(f"/album-proposals/{prop_id}/approve")

        pub_resp = self.client.post(f"/album-proposals/{prop_id}/publish")
        self.assertEqual(pub_resp.status_code, 302)

        prop_after = self.fetch_album_proposals()[0]
        self.assertEqual(prop_after["status"], "published")
        self.assertFalse(cover_path.exists())
        self.assertFalse(track_path.exists())


class AlbumFileServingTest(ReviewAppTestCase):
    @patch("audio_validation.probe_audio_file", return_value=MOCK_PROBE_RESULT)
    def test_serving_cover_and_audio_to_approver(self, _mock_probe):
        approver_id = self.seed_approver("curator@example.com")
        self.submit_album(
            track_tuples=[("01-first.mp3", MP3_BYTES)],
            cover_tuple=("cover.jpg", JPEG_BYTES),
        )
        prop_id = self.fetch_album_proposals()[0]["id"]

        self.login_as(approver_id)

        cover_resp = self.client.get(f"/album-proposals/{prop_id}/cover")
        self.assertEqual(cover_resp.status_code, 200)
        self.assertEqual(cover_resp.data, JPEG_BYTES)

        audio_resp = self.client.get(f"/album-proposals/{prop_id}/tracks/1/file")
        self.assertEqual(audio_resp.status_code, 200)
        self.assertEqual(audio_resp.data, MP3_BYTES)
        self.assertIn("audio/mpeg", audio_resp.headers.get("Content-Type", ""))

    @patch("audio_validation.probe_audio_file", return_value=MOCK_PROBE_RESULT)
    def test_serving_missing_track_or_cover_returns_404(self, _mock_probe):
        approver_id = self.seed_approver("curator@example.com")
        self.submit_album(cover_tuple=None)  # no cover
        prop_id = self.fetch_album_proposals()[0]["id"]

        self.login_as(approver_id)
        self.assertEqual(self.client.get(f"/album-proposals/{prop_id}/cover").status_code, 404)
        self.assertEqual(self.client.get(f"/album-proposals/{prop_id}/tracks/99/file").status_code, 404)


if __name__ == "__main__":
    unittest.main()
