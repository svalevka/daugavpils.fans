#!/usr/bin/env python3
"""
The authenticated callback API the GitHub Action calls (GitHub issue
#13), exercised end to end through real HTTP requests.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_support import ReviewAppTestCase  # noqa: E402


class CallbackAuthTest(ReviewAppTestCase):
    def _approved_proposal_id(self) -> int:
        approver_id = self.seed_approver("approver@example.com")
        self.submit()
        proposal_id = self.fetch_proposals()[0]["id"]
        self.login_as(approver_id)
        self.client.post(f"/proposals/{proposal_id}/approve")
        return proposal_id

    def test_missing_bearer_key_is_rejected(self):
        proposal_id = self._approved_proposal_id()
        response = self.client.get(f"/api/proposals/{proposal_id}")
        self.assertEqual(response.status_code, 401)

    def test_wrong_bearer_key_is_rejected(self):
        proposal_id = self._approved_proposal_id()
        response = self.client.get(
            f"/api/proposals/{proposal_id}", headers={"Authorization": "Bearer wrong-key"}
        )
        self.assertEqual(response.status_code, 401)


class GetProposalTest(ReviewAppTestCase):
    def test_returns_the_approved_proposals_content(self):
        approver_id = self.seed_approver("approver@example.com")
        self.submit(proposed_value="Corrected biography text.")
        proposal_id = self.fetch_proposals()[0]["id"]
        self.login_as(approver_id)
        self.client.post(f"/proposals/{proposal_id}/approve")

        response = self.client.get(f"/api/proposals/{proposal_id}", headers=self.callback_headers())

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["band_slug"], self.fx.band_slug)
        self.assertEqual(body["target"], "band")
        self.assertEqual(body["field"], "description")
        self.assertEqual(body["proposed_value"], "Corrected biography text.")

    def test_fetching_flips_status_to_applying(self):
        approver_id = self.seed_approver("approver@example.com")
        self.submit()
        proposal_id = self.fetch_proposals()[0]["id"]
        self.login_as(approver_id)
        self.client.post(f"/proposals/{proposal_id}/approve")

        self.client.get(f"/api/proposals/{proposal_id}", headers=self.callback_headers())

        self.assertEqual(self.fetch_proposals()[0]["status"], "applying")

    def test_a_pending_proposal_cannot_be_fetched(self):
        self.submit()
        proposal_id = self.fetch_proposals()[0]["id"]

        response = self.client.get(f"/api/proposals/{proposal_id}", headers=self.callback_headers())

        self.assertEqual(response.status_code, 409)

    def test_a_rejected_proposal_cannot_be_fetched(self):
        approver_id = self.seed_approver("approver@example.com")
        self.submit()
        proposal_id = self.fetch_proposals()[0]["id"]
        self.login_as(approver_id)
        self.client.post(f"/proposals/{proposal_id}/reject")

        response = self.client.get(f"/api/proposals/{proposal_id}", headers=self.callback_headers())

        self.assertEqual(response.status_code, 409)

    def test_fetching_twice_the_second_time_is_refused(self):
        proposal_id = self._approve_and_fetch_once()

        second = self.client.get(f"/api/proposals/{proposal_id}", headers=self.callback_headers())

        self.assertEqual(second.status_code, 409)

    def test_unknown_proposal_is_not_found(self):
        response = self.client.get("/api/proposals/999999", headers=self.callback_headers())
        self.assertEqual(response.status_code, 404)

    def _approve_and_fetch_once(self) -> int:
        approver_id = self.seed_approver("approver@example.com")
        self.submit()
        proposal_id = self.fetch_proposals()[0]["id"]
        self.login_as(approver_id)
        self.client.post(f"/proposals/{proposal_id}/approve")
        self.client.get(f"/api/proposals/{proposal_id}", headers=self.callback_headers())
        return proposal_id


class ApplyResultTest(ReviewAppTestCase):
    def _applying_proposal_id(self) -> int:
        approver_id = self.seed_approver("approver@example.com")
        self.submit()
        proposal_id = self.fetch_proposals()[0]["id"]
        self.login_as(approver_id)
        self.client.post(f"/proposals/{proposal_id}/approve")
        self.client.get(f"/api/proposals/{proposal_id}", headers=self.callback_headers())
        return proposal_id

    def test_success_marks_the_proposal_applied(self):
        proposal_id = self._applying_proposal_id()

        response = self.client.post(
            f"/api/proposals/{proposal_id}/apply-result",
            json={"success": True, "run_id": "12345"},
            headers=self.callback_headers(),
        )

        self.assertEqual(response.status_code, 200)
        row = self.fetch_proposals()[0]
        self.assertEqual(row["status"], "applied")
        self.assertEqual(row["github_run_id"], "12345")
        self.assertIsNotNone(row["applied_at"])

    def test_failure_marks_the_proposal_apply_failed_not_stuck_applying(self):
        proposal_id = self._applying_proposal_id()

        response = self.client.post(
            f"/api/proposals/{proposal_id}/apply-result",
            json={"success": False, "error": "validate.py failed"},
            headers=self.callback_headers(),
        )

        self.assertEqual(response.status_code, 200)
        row = self.fetch_proposals()[0]
        self.assertEqual(row["status"], "apply_failed")
        self.assertEqual(row["apply_error"], "validate.py failed")

    def test_a_success_report_for_a_proposal_that_was_never_fetched_is_refused(self):
        # A success report can only be truthful about a proposal whose
        # content was actually fetched (GET /api/proposals/<id> flips it
        # to 'applying') - nobody can honestly report success without
        # that having happened first.
        approver_id = self.seed_approver("approver@example.com")
        self.submit()
        proposal_id = self.fetch_proposals()[0]["id"]
        self.login_as(approver_id)
        self.client.post(f"/proposals/{proposal_id}/approve")
        # Note: never GET /api/proposals/<id> - still 'approved', not 'applying'.

        response = self.client.post(
            f"/api/proposals/{proposal_id}/apply-result",
            json={"success": True},
            headers=self.callback_headers(),
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.fetch_proposals()[0]["status"], "approved")

    def test_a_failure_report_for_a_proposal_that_was_never_fetched_is_accepted(self):
        # Unlike success, a *failure* report must be accepted straight
        # from 'approved': the Action can fail before it ever reaches the
        # fetch step (checkout, dependency install, or the fetch itself
        # failing on a network blip) - in which case the proposal was
        # never flipped to 'applying' at all. Without this, that failure
        # would be silently dropped and the proposal would sit at
        # 'approved' forever with no record anything went wrong.
        approver_id = self.seed_approver("approver@example.com")
        self.submit()
        proposal_id = self.fetch_proposals()[0]["id"]
        self.login_as(approver_id)
        self.client.post(f"/proposals/{proposal_id}/approve")
        # Note: never GET /api/proposals/<id> - still 'approved', not 'applying'.

        response = self.client.post(
            f"/api/proposals/{proposal_id}/apply-result",
            json={"success": False, "error": "checkout failed"},
            headers=self.callback_headers(),
        )

        self.assertEqual(response.status_code, 200)
        row = self.fetch_proposals()[0]
        self.assertEqual(row["status"], "apply_failed")
        self.assertEqual(row["apply_error"], "checkout failed")

    def test_a_second_result_report_is_refused(self):
        proposal_id = self._applying_proposal_id()
        self.client.post(
            f"/api/proposals/{proposal_id}/apply-result",
            json={"success": True},
            headers=self.callback_headers(),
        )

        second = self.client.post(
            f"/api/proposals/{proposal_id}/apply-result",
            json={"success": False},
            headers=self.callback_headers(),
        )

        self.assertEqual(second.status_code, 409)
        self.assertEqual(self.fetch_proposals()[0]["status"], "applied")  # unchanged by the second report


if __name__ == "__main__":
    unittest.main()
