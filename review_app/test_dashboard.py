#!/usr/bin/env python3
"""
The approver dashboard - approve/reject a pending proposal (GitHub issue
#12), exercised end to end through real HTTP requests.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_support import ReviewAppTestCase  # noqa: E402


class UnauthenticatedAccessTest(ReviewAppTestCase):
    def test_dashboard_redirects_anonymous_visitors_to_login(self):
        response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_approve_and_reject_are_blocked_when_not_logged_in(self):
        self.submit()
        proposal_id = self.fetch_proposals()[0]["id"]

        approve = self.client.post(f"/proposals/{proposal_id}/approve")
        reject = self.client.post(f"/proposals/{proposal_id}/reject")

        self.assertEqual(approve.status_code, 401)
        self.assertEqual(reject.status_code, 401)
        self.assertEqual(self.fetch_proposals()[0]["status"], "pending")


class DashboardListingTest(ReviewAppTestCase):
    def test_lists_pending_proposals_with_old_and_new_value(self):
        approver_id = self.seed_approver("approver@example.com")
        self.submit(proposed_value="Corrected biography text.")
        self.login_as(approver_id)

        response = self.client.get("/dashboard")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Original band description.", response.data)
        self.assertIn(b"Corrected biography text.", response.data)


class DecisionTest(ReviewAppTestCase):
    def _submit_and_get_id(self, **form) -> int:
        self.submit(**form)
        return self.fetch_proposals()[-1]["id"]

    def test_approving_flips_status_and_records_who_decided(self):
        approver_id = self.seed_approver("approver@example.com")
        proposal_id = self._submit_and_get_id()
        self.login_as(approver_id)

        response = self.client.post(f"/proposals/{proposal_id}/approve")

        self.assertEqual(response.status_code, 302)
        row = self.fetch_proposals()[0]
        self.assertEqual(row["status"], "approved")
        self.assertEqual(row["decided_by"], approver_id)
        self.assertIsNotNone(row["decided_at"])

    def test_approving_dispatches_the_action_with_only_the_proposal_id(self):
        approver_id = self.seed_approver("approver@example.com")
        proposal_id = self._submit_and_get_id(proposed_value="something a bot must never see logged")
        self.login_as(approver_id)

        self.client.post(f"/proposals/{proposal_id}/approve")

        self.mock_trigger_apply.assert_called_once_with(self.config.github, proposal_id)

    def test_rejecting_does_not_dispatch_anything(self):
        approver_id = self.seed_approver("approver@example.com")
        proposal_id = self._submit_and_get_id()
        self.login_as(approver_id)

        self.client.post(f"/proposals/{proposal_id}/reject")

        self.mock_trigger_apply.assert_not_called()

    def test_rejecting_silently_drops_it_with_no_further_action(self):
        approver_id = self.seed_approver("approver@example.com")
        proposal_id = self._submit_and_get_id()
        self.login_as(approver_id)

        response = self.client.post(f"/proposals/{proposal_id}/reject")

        self.assertEqual(response.status_code, 302)
        row = self.fetch_proposals()[0]
        self.assertEqual(row["status"], "rejected")
        # Rejection is silent: nothing beyond the status/decided_by/
        # decided_at fields changes, and nothing paged/emailed the
        # submitter (review_app has no such code path at all).
        self.assertEqual(row["decided_by"], approver_id)

    def test_deciding_twice_the_second_time_is_a_no_op(self):
        approver_a = self.seed_approver("a@example.com")
        approver_b = self.seed_approver("b@example.com")
        proposal_id = self._submit_and_get_id()

        self.login_as(approver_a)
        first = self.client.post(f"/proposals/{proposal_id}/approve")
        self.assertEqual(first.status_code, 302)

        self.login_as(approver_b)
        second = self.client.post(f"/proposals/{proposal_id}/reject")

        self.assertEqual(second.status_code, 409)
        row = self.fetch_proposals()[0]
        self.assertEqual(row["status"], "approved")  # unchanged by the second attempt
        self.assertEqual(row["decided_by"], approver_a)

    def test_stamped_submitter_cannot_decide_on_their_own_proposal(self):
        approver_id = self.seed_approver("ryb@example.com")
        self.login_as(approver_id)
        proposal_id = self._submit_and_get_id()  # submitted while logged in -> self-stamped

        response = self.client.post(f"/proposals/{proposal_id}/approve")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.fetch_proposals()[0]["status"], "pending")

    def test_a_different_approver_can_decide_on_it_normally(self):
        submitter_id = self.seed_approver("ryb@example.com")
        other_id = self.seed_approver("other@example.com")
        self.login_as(submitter_id)
        proposal_id = self._submit_and_get_id()

        self.login_as(other_id)
        response = self.client.post(f"/proposals/{proposal_id}/approve")

        self.assertEqual(response.status_code, 302)
        row = self.fetch_proposals()[0]
        self.assertEqual(row["status"], "approved")
        self.assertEqual(row["decided_by"], other_id)

    def test_deciding_on_a_nonexistent_proposal_is_not_found(self):
        approver_id = self.seed_approver("approver@example.com")
        self.login_as(approver_id)

        response = self.client.post("/proposals/999999/approve")

        self.assertEqual(response.status_code, 404)

    def test_dashboard_disables_decision_controls_for_ones_own_submission(self):
        approver_id = self.seed_approver("ryb@example.com")
        self.login_as(approver_id)
        proposal_id = self._submit_and_get_id()

        response = self.client.get("/dashboard")

        body = response.data
        self.assertNotIn(f'/proposals/{proposal_id}/approve"'.encode(), body)
        self.assertIn(b"another approver must decide", body)


class ProposalDecisionNotificationTest(ReviewAppTestCase):
    def test_approving_edit_proposal_with_contact_notifies_submitter(self):
        approver_id = self.seed_approver("approver@example.com")
        self.submit(submitter_contact="fan@example.com")
        proposal_id = self.fetch_proposals()[-1]["id"]
        self.login_as(approver_id)

        response = self.client.post(f"/proposals/{proposal_id}/approve")
        self.assertEqual(response.status_code, 302)

        self.mock_send_proposal_decision.assert_called_once()
        kwargs = self.mock_send_proposal_decision.call_args.kwargs
        args = self.mock_send_proposal_decision.call_args.args
        to_addr = kwargs.get("to_addr") or (args[1] if len(args) > 1 else None)
        self.assertEqual(to_addr, "fan@example.com")
        self.assertEqual(kwargs.get("decision"), "approved")
        self.assertEqual(kwargs.get("proposal_type"), "edit")
        self.assertIn("https://daugavpils.fans/bands/", kwargs.get("live_url", ""))

    def test_approving_edit_proposal_without_contact_proceeds_silently(self):
        approver_id = self.seed_approver("approver@example.com")
        self.submit()  # anonymous
        proposal_id = self.fetch_proposals()[-1]["id"]
        self.login_as(approver_id)

        response = self.client.post(f"/proposals/{proposal_id}/approve")
        self.assertEqual(response.status_code, 302)
        self.mock_send_proposal_decision.assert_not_called()

    def test_rejecting_edit_proposal_with_contact_and_notes(self):
        approver_id = self.seed_approver("approver@example.com")
        self.submit(submitter_contact="contributor@example.com")
        proposal_id = self.fetch_proposals()[-1]["id"]
        self.login_as(approver_id)

        response = self.client.post(f"/proposals/{proposal_id}/reject", data={"reason": "incorrect lineup info"})
        self.assertEqual(response.status_code, 302)

        self.mock_send_proposal_decision.assert_called_once()
        kwargs = self.mock_send_proposal_decision.call_args.kwargs
        self.assertEqual(kwargs.get("decision"), "rejected")
        self.assertEqual(kwargs.get("review_notes"), "incorrect lineup info")
        self.assertEqual(kwargs.get("proposal_type"), "edit")

        row = self.fetch_proposals()[-1]
        self.assertEqual(row["status"], "rejected")
        self.assertEqual(row["review_notes"], "incorrect lineup info")

    def test_rejecting_edit_proposal_without_contact_proceeds_silently(self):
        approver_id = self.seed_approver("approver@example.com")
        self.submit()
        proposal_id = self.fetch_proposals()[-1]["id"]
        self.login_as(approver_id)

        response = self.client.post(f"/proposals/{proposal_id}/reject", data={"reason": "spam"})
        self.assertEqual(response.status_code, 302)
        self.mock_send_proposal_decision.assert_not_called()

        row = self.fetch_proposals()[-1]
        self.assertEqual(row["status"], "rejected")
        self.assertEqual(row["review_notes"], "spam")

    def test_rejecting_media_proposal_notifies_submitter_with_curator_notes(self):
        approver_id = self.seed_approver("approver@example.com")
        self.submit_media(submitter_contact="photographer@example.com")
        proposal_id = self.fetch_media_proposals()[-1]["id"]
        self.login_as(approver_id)

        response = self.client.post(
            f"/media-proposals/{proposal_id}/reject",
            data={"reason": "duplicate recording from another concert"},
        )
        self.assertEqual(response.status_code, 302)

        self.mock_send_proposal_decision.assert_called_once()
        kwargs = self.mock_send_proposal_decision.call_args.kwargs
        self.assertEqual(kwargs.get("decision"), "rejected")
        self.assertEqual(kwargs.get("proposal_type"), "media")
        self.assertEqual(kwargs.get("review_notes"), "duplicate recording from another concert")

        row = self.fetch_media_proposals()[-1]
        self.assertEqual(row["status"], "rejected")
        self.assertEqual(row["review_notes"], "duplicate recording from another concert")

    def test_album_proposal_approval_and_rejection_notifications(self):
        # 1. Approval
        self.submit_album(submitter_contact="band@example.com", name="New Album 1")
        proposal_id1 = self.fetch_album_proposals()[-1]["id"]

        approver_id = self.seed_approver("approver@example.com")
        self.login_as(approver_id)
        resp1 = self.client.post(f"/album-proposals/{proposal_id1}/approve")
        self.assertEqual(resp1.status_code, 302)
        self.mock_send_proposal_decision.assert_called_once()
        kw1 = self.mock_send_proposal_decision.call_args.kwargs
        self.assertEqual(kw1.get("decision"), "approved")
        self.assertEqual(kw1.get("proposal_type"), "album")
        self.assertIn("/bands/", kw1.get("live_url", ""))

        self.mock_send_proposal_decision.reset_mock()

        # 2. Rejection with notes
        with self.client.session_transaction() as sess:
            sess.clear()
        self.submit_album(submitter_contact="band@example.com", name="New Album 2")
        proposal_id2 = self.fetch_album_proposals()[-1]["id"]
        self.login_as(approver_id)
        resp2 = self.client.post(
            f"/album-proposals/{proposal_id2}/reject",
            data={"reason": "missing track titles and dates"},
        )
        self.assertEqual(resp2.status_code, 302)
        self.mock_send_proposal_decision.assert_called_once()
        kw2 = self.mock_send_proposal_decision.call_args.kwargs
        self.assertEqual(kw2.get("decision"), "rejected")
        self.assertEqual(kw2.get("review_notes"), "missing track titles and dates")

        row2 = self.fetch_album_proposals()[-1]
        self.assertEqual(row2["status"], "rejected")
        self.assertEqual(row2["review_notes"], "missing track titles and dates")

    def test_band_proposal_approval_and_rejection_notifications(self):
        # 1. Approval
        self.submit_band(submitter_contact="musician@example.com", name="Band Alpha")
        proposal_id1 = self.fetch_band_proposals()[-1]["id"]

        approver_id = self.seed_approver("approver@example.com")
        self.login_as(approver_id)
        resp1 = self.client.post(f"/band-proposals/{proposal_id1}/approve", data={"band_slug": "band-alpha"})
        self.assertEqual(resp1.status_code, 302)
        self.mock_send_proposal_decision.assert_called_once()
        kw1 = self.mock_send_proposal_decision.call_args.kwargs
        self.assertEqual(kw1.get("decision"), "approved")
        self.assertEqual(kw1.get("proposal_type"), "band")
        self.assertIn("/bands/band-alpha/", kw1.get("live_url", ""))

        self.mock_send_proposal_decision.reset_mock()

        # 2. Rejection with notes
        with self.client.session_transaction() as sess:
            sess.clear()
        self.submit_band(submitter_contact="musician@example.com", name="Band Beta")
        proposal_id2 = self.fetch_band_proposals()[-1]["id"]
        self.login_as(approver_id)
        resp2 = self.client.post(
            f"/band-proposals/{proposal_id2}/reject",
            data={"reason": "insufficient connection to Daugavpils scene"},
        )
        self.assertEqual(resp2.status_code, 302)
        self.mock_send_proposal_decision.assert_called_once()
        kw2 = self.mock_send_proposal_decision.call_args.kwargs
        self.assertEqual(kw2.get("decision"), "rejected")
        self.assertEqual(kw2.get("review_notes"), "insufficient connection to Daugavpils scene")

        row2 = self.fetch_band_proposals()[-1]
        self.assertEqual(row2["status"], "rejected")
        self.assertEqual(row2["review_notes"], "insufficient connection to Daugavpils scene")


if __name__ == "__main__":
    unittest.main()
