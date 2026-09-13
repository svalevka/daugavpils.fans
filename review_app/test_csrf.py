#!/usr/bin/env python3
"""
Tests for review_app's CSRF protection mechanism (review_app/csrf.py).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_support import ReviewAppTestCase  # noqa: E402


class CsrfProtectionTest(ReviewAppTestCase):
    def setUp(self):
        super().setUp()
        self.app.config["CSRF_ENABLED"] = True

    def test_get_request_renders_csrf_token_in_form(self):
        res = self.client.get("/login")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b'name="csrf_token"', res.data)

    def test_post_without_csrf_token_is_rejected(self):
        res = self.client.post("/login", data={"email": "nobody@example.com"})
        self.assertEqual(res.status_code, 400)
        self.assertIn(b"CSRF validation failed", res.data)

    def test_post_with_invalid_csrf_token_is_rejected(self):
        with self.client.session_transaction() as sess:
            sess["_csrf_token"] = "correct-token-value-12345"
        res = self.client.post(
            "/login",
            data={"email": "nobody@example.com", "csrf_token": "wrong-token-value-99999"},
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn(b"CSRF validation failed", res.data)

    def test_post_with_valid_form_csrf_token_succeeds(self):
        token = "test-token-valid-abcde12345"
        with self.client.session_transaction() as sess:
            sess["_csrf_token"] = token
        res = self.client.post(
            "/login",
            data={"email": "nobody@example.com", "csrf_token": token},
        )
        self.assertEqual(res.status_code, 200)

    def test_post_with_valid_header_csrf_token_succeeds(self):
        token = "test-token-valid-abcde12345"
        with self.client.session_transaction() as sess:
            sess["_csrf_token"] = token
        res = self.client.post(
            "/login",
            data={"email": "nobody@example.com"},
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(res.status_code, 200)

    def test_api_routes_exempt_from_csrf(self):
        # API endpoints authenticate via callback bearer key, not session cookies
        res = self.client.get(
            "/api/proposals/approved",
            headers={"Authorization": f"Bearer {self.config.callback_key}"},
        )
        self.assertEqual(res.status_code, 200)

    def test_analytics_beacon_exempt_from_csrf(self):
        # /api/event is an unauthenticated client beacon (navigator.sendBeacon)
        res = self.client.post("/api/event", json={"type": "pageview", "path": "/"})
        self.assertEqual(res.status_code, 204)


if __name__ == "__main__":
    unittest.main()
