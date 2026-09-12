#!/usr/bin/env python3
"""
The approver dashboard's media-proposal routes (GitHub issue #21):
approve/reject/publish and the login-gated preview, exercised end to end
through real HTTP requests - mirrors test_dashboard.py's approach for the
text-proposal routes, with the key behavioral differences this feature's
"curated queue, manual finish" design calls for: approving never
dispatches anything (no CI, no archive.org), and there's a third,
publish-only transition with no equivalent in the text-proposal flow.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_support import JPEG_BYTES, ReviewAppTestCase  # noqa: E402


class DecisionTest(ReviewAppTestCase):
    def _submit_and_get_id(self, **form) -> int:
        self.submit_media(**form)
        return self.fetch_media_proposals()[-1]["id"]

    def test_approving_flips_status_and_records_who_decided_without_dispatching_anything(self):
        approver_id = self.seed_approver("approver@example.com")
        proposal_id = self._submit_and_get_id()
        self.login_as(approver_id)

        response = self.client.post(f"/media-proposals/{proposal_id}/approve")

        self.assertEqual(response.status_code, 302)
        row = self.fetch_media_proposals()[0]
        self.assertEqual(row["status"], "approved")
        self.assertEqual(row["decided_by"], approver_id)
        self.assertIsNotNone(row["decided_at"])
        self.mock_trigger_apply.assert_not_called()

    def test_approving_notifies_a_submitter_who_left_a_contact_email(self):
        approver_id = self.seed_approver("approver@example.com")
        proposal_id = self._submit_and_get_id(submitter_contact="fan@example.com")
        self.login_as(approver_id)

        self.client.post(f"/media-proposals/{proposal_id}/approve")

        self.mock_send_media_approved.assert_called_once()
        args = self.mock_send_media_approved.call_args.args
        self.assertEqual(args[1], "fan@example.com")

    def test_approving_an_anonymous_submission_sends_no_notification(self):
        approver_id = self.seed_approver("approver@example.com")
        proposal_id = self._submit_and_get_id()  # no submitter_contact
        self.login_as(approver_id)

        self.client.post(f"/media-proposals/{proposal_id}/approve")

        self.mock_send_media_approved.assert_not_called()

    def test_rejecting_deletes_the_stored_file(self):
        approver_id = self.seed_approver("approver@example.com")
        proposal_id = self._submit_and_get_id()
        stored_path = (
            self.config.resolved_media_uploads_path() / self.fetch_media_proposals()[0]["stored_filename"]
        )
        self.assertTrue(stored_path.exists())
        self.login_as(approver_id)

        response = self.client.post(f"/media-proposals/{proposal_id}/reject")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.fetch_media_proposals()[0]["status"], "rejected")
        self.assertFalse(stored_path.exists())

    def test_deciding_twice_the_second_time_is_a_no_op(self):
        approver_a = self.seed_approver("a@example.com")
        approver_b = self.seed_approver("b@example.com")
        proposal_id = self._submit_and_get_id()

        self.login_as(approver_a)
        first = self.client.post(f"/media-proposals/{proposal_id}/approve")
        self.assertEqual(first.status_code, 302)

        self.login_as(approver_b)
        second = self.client.post(f"/media-proposals/{proposal_id}/reject")

        self.assertEqual(second.status_code, 409)
        self.assertEqual(self.fetch_media_proposals()[0]["status"], "approved")

    def test_stamped_submitter_cannot_decide_on_their_own_submission(self):
        approver_id = self.seed_approver("ryb@example.com")
        self.login_as(approver_id)
        proposal_id = self._submit_and_get_id()  # submitted while logged in -> self-stamped

        response = self.client.post(f"/media-proposals/{proposal_id}/approve")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.fetch_media_proposals()[0]["status"], "pending")


class PublishTest(ReviewAppTestCase):
    def _approved_proposal_id(self) -> int:
        approver_id = self.seed_approver("approver@example.com")
        self.submit_media()
        proposal_id = self.fetch_media_proposals()[-1]["id"]
        self.login_as(approver_id)
        self.client.post(f"/media-proposals/{proposal_id}/approve")
        return proposal_id

    def test_publish_deletes_the_file_and_closes_out_the_row(self):
        proposal_id = self._approved_proposal_id()
        stored_path = (
            self.config.resolved_media_uploads_path() / self.fetch_media_proposals()[0]["stored_filename"]
        )
        self.assertTrue(stored_path.exists())

        response = self.client.post(f"/media-proposals/{proposal_id}/publish")

        self.assertEqual(response.status_code, 302)
        row = self.fetch_media_proposals()[0]
        self.assertEqual(row["status"], "published")
        self.assertIsNotNone(row["published_at"])
        self.assertFalse(stored_path.exists())

    def test_publish_on_a_still_pending_proposal_is_rejected(self):
        self.seed_approver("approver@example.com")
        self.submit_media()
        proposal_id = self.fetch_media_proposals()[-1]["id"]
        approver_id = self.seed_approver("second@example.com")
        self.login_as(approver_id)

        response = self.client.post(f"/media-proposals/{proposal_id}/publish")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.fetch_media_proposals()[0]["status"], "pending")

    def test_publish_twice_the_second_time_is_rejected(self):
        proposal_id = self._approved_proposal_id()
        first = self.client.post(f"/media-proposals/{proposal_id}/publish")
        self.assertEqual(first.status_code, 302)

        second = self.client.post(f"/media-proposals/{proposal_id}/publish")
        self.assertEqual(second.status_code, 409)


class PreviewFileTest(ReviewAppTestCase):
    def test_logged_in_approver_can_fetch_the_preview(self):
        approver_id = self.seed_approver("approver@example.com")
        self.submit_media()
        proposal_id = self.fetch_media_proposals()[-1]["id"]
        self.login_as(approver_id)

        response = self.client.get(f"/media-proposals/{proposal_id}/file")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, JPEG_BYTES)
        self.assertEqual(response.mimetype, "image/jpeg")

    def test_anonymous_visitor_cannot_fetch_the_preview(self):
        self.submit_media()
        proposal_id = self.fetch_media_proposals()[-1]["id"]

        # A GET, like /dashboard itself, redirects to the login form
        # rather than 401ing (see require_approver) - a POST decision
        # route 401s instead, since there's nowhere sensible to redirect
        # a form submission.
        response = self.client.get(f"/media-proposals/{proposal_id}/file")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])


class DashboardListingTest(ReviewAppTestCase):
    def test_pending_and_awaiting_publish_are_listed_separately(self):
        approver_id = self.seed_approver("approver@example.com")
        self.submit_media(file_tuples=[("pending.jpg", JPEG_BYTES)])
        self.submit_media(file_tuples=[("toapprove.jpg", JPEG_BYTES)])
        rows = self.fetch_media_proposals()
        self.login_as(approver_id)
        self.client.post(f"/media-proposals/{rows[1]['id']}/approve")

        response = self.client.get("/dashboard")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"pending.jpg", response.data)
        self.assertIn(b"toapprove.jpg", response.data)
        self.assertIn(b"Ready to publish", response.data)

    def test_unauthenticated_media_decisions_are_blocked(self):
        self.submit_media()
        proposal_id = self.fetch_media_proposals()[0]["id"]

        approve = self.client.post(f"/media-proposals/{proposal_id}/approve")
        reject = self.client.post(f"/media-proposals/{proposal_id}/reject")

        self.assertEqual(approve.status_code, 401)
        self.assertEqual(reject.status_code, 401)
        self.assertEqual(self.fetch_media_proposals()[0]["status"], "pending")


class UploadMediaTest(ReviewAppTestCase):
    def _approved_proposal_id(self) -> int:
        approver_id = self.seed_approver("approver@example.com")
        self.submit_media()
        proposal_id = self.fetch_media_proposals()[-1]["id"]
        self.login_as(approver_id)
        self.client.post(f"/media-proposals/{proposal_id}/approve")
        return proposal_id

    def test_upload_triggers_dispatch_and_sets_publishing(self):
        proposal_id = self._approved_proposal_id()
        response = self.client.post(f"/media-proposals/{proposal_id}/upload")
        self.assertEqual(response.status_code, 302)

        row = self.fetch_media_proposals()[0]
        self.assertEqual(row["status"], "publishing")
        self.mock_trigger_media_apply.assert_called_once()
        args = self.mock_trigger_media_apply.call_args.args
        self.assertEqual(args[1], proposal_id)

    def test_upload_handles_dispatch_error(self):
        self.mock_trigger_media_apply.side_effect = OSError("network error")
        proposal_id = self._approved_proposal_id()
        response = self.client.post(f"/media-proposals/{proposal_id}/upload")
        self.assertEqual(response.status_code, 302)

        row = self.fetch_media_proposals()[0]
        self.assertEqual(row["status"], "publish_failed")
        self.assertIn("Failed to dispatch", row["publish_error"])

    def test_upload_on_pending_proposal_is_rejected(self):
        approver_id = self.seed_approver("approver@example.com")
        self.submit_media()
        proposal_id = self.fetch_media_proposals()[-1]["id"]
        self.login_as(approver_id)

        response = self.client.post(f"/media-proposals/{proposal_id}/upload")
        self.assertEqual(response.status_code, 409)

    def test_upload_unauthenticated_is_rejected(self):
        proposal_id = self._approved_proposal_id()
        with self.client.session_transaction() as sess:
            sess.clear()
        response = self.client.post(f"/media-proposals/{proposal_id}/upload")
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
