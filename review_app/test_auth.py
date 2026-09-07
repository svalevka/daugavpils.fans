#!/usr/bin/env python3
"""
Passwordless magic-link login (GitHub issue #12), exercised end to end
through real HTTP requests. Real SMTP is mocked (see test_support.py) -
tests recover the "emailed" link from the mock's call arguments, exactly
as a real approver would get it from their inbox.
"""
from __future__ import annotations

import hashlib
import sqlite3
import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_support import ReviewAppTestCase  # noqa: E402


def _extract_token(link_url: str) -> str:
    query = parse_qs(urlparse(link_url).query)
    assert "token" in query, f"no token in {link_url!r}"
    return query["token"][0]


class LoginRequestTest(ReviewAppTestCase):
    def test_registered_and_unregistered_email_get_the_identical_response(self):
        self.seed_approver("ryb@example.com")

        registered = self.client.post("/login", data={"email": "ryb@example.com"})
        unregistered = self.client.post("/login", data={"email": "nobody@example.com"})

        self.assertEqual(registered.status_code, unregistered.status_code)
        self.assertEqual(registered.data, unregistered.data)

    def test_registered_email_sends_a_link_but_unregistered_does_not(self):
        # The point of the criterion above is that responses can't be
        # used to enumerate approvers - so this asserts on the mock, not
        # on any difference in what the client sees.
        self.seed_approver("ryb@example.com")

        self.client.post("/login", data={"email": "ryb@example.com"})
        self.assertEqual(self.mock_send_magic_link.call_count, 1)

        self.client.post("/login", data={"email": "nobody@example.com"})
        self.assertEqual(self.mock_send_magic_link.call_count, 1)  # unchanged

    def test_inactive_approver_email_does_not_send_a_link(self):
        self.seed_approver("retired@example.com", is_active=False)

        self.client.post("/login", data={"email": "retired@example.com"})

        self.mock_send_magic_link.assert_not_called()


class MagicLinkVerificationTest(ReviewAppTestCase):
    def _request_link(self, email: str) -> str:
        self.client.post("/login", data={"email": email})
        _smtp_config, _to_addr, link_url = self.mock_send_magic_link.call_args.args
        return _extract_token(link_url)

    def test_valid_token_logs_in_and_redirects_to_the_dashboard(self):
        self.seed_approver("ryb@example.com")
        token = self._request_link("ryb@example.com")

        response = self.client.get(f"/login/verify?token={token}")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/dashboard", response.headers["Location"])
        with self.client.session_transaction() as sess:
            self.assertIn("approver_id", sess)

    def test_token_is_single_use(self):
        self.seed_approver("ryb@example.com")
        token = self._request_link("ryb@example.com")

        first = self.client.get(f"/login/verify?token={token}")
        self.assertEqual(first.status_code, 302)

        with self.client.session_transaction() as sess:
            sess.clear()  # simulate a fresh browser, not just re-using the still-logged-in session

        second = self.client.get(f"/login/verify?token={token}")
        self.assertEqual(second.status_code, 400)

    def test_unknown_token_is_rejected(self):
        response = self.client.get("/login/verify?token=not-a-real-token")
        self.assertEqual(response.status_code, 400)

    def test_expired_token_is_rejected(self):
        approver_id = self.seed_approver("ryb@example.com")
        token = "expired-token-for-test"

        conn = sqlite3.connect(self.database_path)
        conn.execute(
            "INSERT INTO magic_links (approver_id, token_hash, expires_at) "
            "VALUES (?, ?, datetime('now', '-1 minute'))",
            (approver_id, hashlib.sha256(token.encode()).hexdigest()),
        )
        conn.commit()
        conn.close()

        response = self.client.get(f"/login/verify?token={token}")
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
