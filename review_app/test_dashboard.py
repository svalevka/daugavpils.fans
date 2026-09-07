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


if __name__ == "__main__":
    unittest.main()
