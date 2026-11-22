#!/usr/bin/env python3
"""
The two callback-API endpoints behind the host review (maintained
outside this repository): requesting a host snapshot (drops a nonce for
the host-side collector) and reading the collector's answer.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_support import ReviewAppTestCase  # noqa: E402


class SecurityReviewApiTest(ReviewAppTestCase):
    def setUp(self):
        super().setUp()
        self.request_path = self.app.config["SECURITY_REQUEST_PATH"]
        self.snapshot_dir = Path(self._tmp.name) / "security-review"
        self.snapshot_dir.mkdir()
        self.app.config["SECURITY_SNAPSHOT_DIR"] = self.snapshot_dir

    def test_request_path_defaults_to_the_database_volume(self):
        self.assertEqual(self.request_path, self.database_path.parent / "security-review-request")

    def test_request_requires_the_callback_key(self):
        self.assertEqual(self.client.post("/api/security-review/request").status_code, 401)
        wrong = self.client.post("/api/security-review/request", headers={"Authorization": "Bearer nope"})
        self.assertEqual(wrong.status_code, 401)
        self.assertFalse(self.request_path.exists())

    def test_request_writes_a_fresh_server_generated_nonce(self):
        response = self.client.post("/api/security-review/request", headers=self.callback_headers(), json={"nonce": "attacker-chosen"})
        self.assertEqual(response.status_code, 202)
        nonce = response.get_json()["nonce"]
        self.assertRegex(nonce, r"^[0-9a-f]{32}$")
        self.assertEqual(self.request_path.read_text().strip(), nonce)

    def test_second_request_within_the_cooldown_is_refused(self):
        first = self.client.post("/api/security-review/request", headers=self.callback_headers())
        second = self.client.post("/api/security-review/request", headers=self.callback_headers())
        self.assertEqual(second.status_code, 429)
        self.assertEqual(self.request_path.read_text().strip(), first.get_json()["nonce"])

    def test_request_is_accepted_again_after_the_cooldown(self):
        first = self.client.post("/api/security-review/request", headers=self.callback_headers()).get_json()["nonce"]
        old = time.time() - 120
        os.utime(self.request_path, (old, old))
        second = self.client.post("/api/security-review/request", headers=self.callback_headers())
        self.assertEqual(second.status_code, 202)
        self.assertNotEqual(second.get_json()["nonce"], first)

    def test_snapshot_requires_the_callback_key(self):
        (self.snapshot_dir / "snapshot.json").write_text('{"nonce": "x"}')
        self.assertEqual(self.client.get("/api/security-review/snapshot").status_code, 401)

    def test_snapshot_is_404_until_the_collector_has_answered(self):
        response = self.client.get("/api/security-review/snapshot", headers=self.callback_headers())
        self.assertEqual(response.status_code, 404)

    def test_snapshot_returns_current_and_previous(self):
        (self.snapshot_dir / "snapshot.json").write_text(json.dumps({"nonce": "new"}))
        (self.snapshot_dir / "previous.json").write_text(json.dumps({"nonce": "old"}))
        body = self.client.get("/api/security-review/snapshot", headers=self.callback_headers()).get_json()
        self.assertEqual(body, {"snapshot": {"nonce": "new"}, "previous": {"nonce": "old"}})

    def test_missing_or_corrupt_previous_is_null_not_an_error(self):
        (self.snapshot_dir / "snapshot.json").write_text(json.dumps({"nonce": "new"}))
        (self.snapshot_dir / "previous.json").write_text("{corrupt")
        response = self.client.get("/api/security-review/snapshot", headers=self.callback_headers())
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.get_json()["previous"])

    def test_corrupt_snapshot_is_404(self):
        (self.snapshot_dir / "snapshot.json").write_text("{corrupt")
        response = self.client.get("/api/security-review/snapshot", headers=self.callback_headers())
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
