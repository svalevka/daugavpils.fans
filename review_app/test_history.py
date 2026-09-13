#!/usr/bin/env python3
"""
Unit and integration tests for the decision history log and 90-day retention rotation (/dashboard/history).
"""
from __future__ import annotations

import json
import sqlite3
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
import roles  # noqa: E402
from test_support import ReviewAppTestCase  # noqa: E402


class DecisionHistoryAuthTest(ReviewAppTestCase):
    def test_unauthenticated_redirects_to_login(self):
        resp = self.client.get("/dashboard/history")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.headers["Location"])

    def test_history_alias_redirects_to_dashboard_history(self):
        self.login_as(1)
        resp = self.client.get("/history?days=14&status=approved")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/dashboard/history?days=14&status=approved", resp.headers["Location"])


class DecisionHistoryContentTest(ReviewAppTestCase):
    def setUp(self):
        super().setUp()
        self.approver_id = 1
        self.login_as(self.approver_id)

    def _insert_edit(self, band_slug="test-band", status="applied", days_ago=2, decider_id=1, ai=False):
        conn = sqlite3.connect(self.database_path)
        decided_at = (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")
        ai_dec = "approve" if status in ("applied", "approved") else "reject"
        conn.execute(
            """
            INSERT INTO proposals (
                band_slug, target, field, original_value, proposed_value,
                submitter_name, submitter_contact, submitter_ip, status,
                decided_by, decided_at, ai_decision, ai_confidence, ai_reasoning
            ) VALUES (?, 'band', 'location', '"Riga"', '"Daugavpils"', 'Alice', 'alice@example.com', '127.0.0.1', ?, ?, ?, ?, 0.96, 'Verified correct location')
            """,
            (band_slug, status, decider_id, decided_at, ai_dec if ai else None),
        )
        conn.commit()
        conn.close()

    def _insert_media(self, band_slug="test-band", status="published", days_ago=3, decider_id=1):
        conn = sqlite3.connect(self.database_path)
        decided_at = (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            """
            INSERT INTO media_proposals (
                band_slug, media_type, original_filename, stored_filename, content_type,
                size_bytes, caption, submitter_name, submitter_contact, submitter_ip,
                status, decided_by, decided_at
            ) VALUES (?, 'image', 'photo.jpg', 'stored.jpg', 'image/jpeg', 102400, 'Live 1998', 'Bob', 'bob@example.com', '127.0.0.1', ?, ?, ?)
            """,
            (band_slug, status, decider_id, decided_at),
        )
        conn.commit()
        conn.close()

    def _insert_album(self, band_slug="test-band", name="Old Demo", status="published", days_ago=4, decider_id=1):
        conn = sqlite3.connect(self.database_path)
        decided_at = (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")
        tracks_json = json.dumps([{"position": 1, "name": "Track 1", "duration": "03:20"}])
        conn.execute(
            """
            INSERT INTO album_proposals (
                band_slug, release_slug, name, date_published, genre, license,
                tracks_json, submitter_name, submitter_contact, submitter_ip,
                status, decided_by, decided_at
            ) VALUES (?, '1998-old-demo', ?, '1998', '["Punk"]', 'CC BY', ?, 'Charlie', 'c@example.com', '127.0.0.1', ?, ?, ?)
            """,
            (band_slug, name, tracks_json, status, decider_id, decided_at),
        )
        conn.commit()
        conn.close()

    def _insert_band(self, name="Fresh Band", band_slug="fresh-band", status="published", days_ago=5, decider_id=1):
        conn = sqlite3.connect(self.database_path)
        decided_at = (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            """
            INSERT INTO band_proposals (
                name, band_slug, founding_date, location, genre,
                submitter_name, submitter_contact, submitter_ip,
                status, decided_by, decided_at
            ) VALUES (?, ?, '1995', 'Daugavpils', '["Rock"]', 'Dave', 'd@example.com', '127.0.0.1', ?, ?, ?)
            """,
            (name, band_slug, status, decider_id, decided_at),
        )
        conn.commit()
        conn.close()

    def test_decision_history_shows_all_types(self):
        self._insert_edit(status="applied", days_ago=1)
        self._insert_media(status="published", days_ago=2)
        self._insert_album(status="published", days_ago=3)
        self._insert_band(status="rejected", days_ago=4)

        resp = self.client.get("/dashboard/history")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        self.assertIn("Decision History", html)
        self.assertIn("Field Edit", html)
        self.assertIn("Media (Image)", html)
        self.assertIn("New Album", html)
        self.assertIn("New Band", html)
        self.assertIn("Showing <strong>4</strong> decisions from the last <strong>7</strong> days", html)
        self.assertIn("(3 approved, 1 rejected)", html)

    def test_filter_by_status(self):
        self._insert_edit(status="applied", days_ago=1)
        self._insert_media(status="rejected", days_ago=2)

        # Filter approved
        resp = self.client.get("/dashboard/history?status=approved")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("Field Edit", html)
        self.assertNotIn("Media (Image)", html)
        self.assertIn("Showing <strong>1</strong> decisions", html)

        # Filter rejected
        resp = self.client.get("/dashboard/history?status=rejected")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertNotIn("Field Edit", html)
        self.assertIn("Media (Image)", html)
        self.assertIn("Showing <strong>1</strong> decisions", html)

    def test_filter_by_type(self):
        self._insert_edit(status="applied", days_ago=1)
        self._insert_media(status="published", days_ago=2)
        self._insert_album(status="published", days_ago=3)
        self._insert_band(status="published", days_ago=4)

        resp = self.client.get("/dashboard/history?type=album")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("New Album", html)
        self.assertNotIn("Field Edit", html)
        self.assertNotIn("Media (Image)", html)
        self.assertNotIn("New Band", html)

    def test_filter_by_timeframe(self):
        self._insert_edit(band_slug="recent-band", status="applied", days_ago=3)
        self._insert_edit(band_slug="older-band", status="applied", days_ago=10)

        # 7 days window (default)
        resp = self.client.get("/dashboard/history?days=7")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("recent-band", html)
        self.assertNotIn("older-band", html)

        # 14 days window
        resp = self.client.get("/dashboard/history?days=14")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("recent-band", html)
        self.assertIn("older-band", html)

    def test_ai_decision_badge_rendered(self):
        conn = sqlite3.connect(self.database_path)
        ai_id = roles.ensure_ai_approver(conn)
        conn.close()

        self._insert_edit(status="applied", days_ago=1, decider_id=ai_id, ai=True)
        resp = self.client.get("/dashboard/history")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("🤖 AI Approval Agent", html)
        self.assertIn("Verified correct location", html)


class RetentionPruningTest(ReviewAppTestCase):
    def test_90_day_retention_prunes_decided_proposals(self):
        conn = sqlite3.connect(self.database_path)
        decided_old = (datetime.now(timezone.utc) - timedelta(days=95)).strftime("%Y-%m-%d %H:%M:%S")
        decided_recent = (datetime.now(timezone.utc) - timedelta(days=20)).strftime("%Y-%m-%d %H:%M:%S")
        created_old = (datetime.now(timezone.utc) - timedelta(days=100)).strftime("%Y-%m-%d %H:%M:%S")

        # 1. Old decided proposals (> 90 days) -> SHOULD BE PRUNED
        conn.execute(
            "INSERT INTO proposals (band_slug, target, field, original_value, proposed_value, submitter_ip, status, decided_at) "
            "VALUES ('band-a', 'band', 'loc', 'old', 'new', '127.0.0.1', 'applied', ?)",
            (decided_old,),
        )
        conn.execute(
            "INSERT INTO media_proposals (band_slug, media_type, original_filename, stored_filename, content_type, size_bytes, submitter_ip, status, decided_at) "
            "VALUES ('band-a', 'image', 'a.jpg', 'stored.jpg', 'image/jpeg', 100, '127.0.0.1', 'rejected', ?)",
            (decided_old,),
        )
        conn.execute(
            "INSERT INTO album_proposals (band_slug, release_slug, name, date_published, license, tracks_json, submitter_ip, status, decided_at) "
            "VALUES ('band-a', '1990-old', 'Old', '1990', 'CC', '[]', '127.0.0.1', 'published', ?)",
            (decided_old,),
        )
        conn.execute(
            "INSERT INTO band_proposals (name, band_slug, submitter_ip, status, decided_at) "
            "VALUES ('Band A', 'band-a', '127.0.0.1', 'rejected', ?)",
            (decided_old,),
        )

        # 2. Recent decided proposals (<= 90 days) -> MUST BE PRESERVED
        conn.execute(
            "INSERT INTO proposals (band_slug, target, field, original_value, proposed_value, submitter_ip, status, decided_at) "
            "VALUES ('band-b', 'band', 'loc', 'old', 'new', '127.0.0.1', 'applied', ?)",
            (decided_recent,),
        )

        # 3. Old pending proposals (> 90 days) -> NEVER PRUNED (pending items must never be dropped!)
        conn.execute(
            "INSERT INTO proposals (band_slug, target, field, original_value, proposed_value, submitter_ip, status, created_at) "
            "VALUES ('band-c', 'band', 'loc', 'old', 'new', '127.0.0.1', 'pending', ?)",
            (created_old,),
        )
        conn.commit()

        # Run pruning
        deleted = db.prune_old_decided_proposals(conn, retention_days=90)
        self.assertEqual(deleted["proposals"], 1)
        self.assertEqual(deleted["media_proposals"], 1)
        self.assertEqual(deleted["album_proposals"], 1)
        self.assertEqual(deleted["band_proposals"], 1)

        # Verify recent decided item is still present
        remaining = conn.execute("SELECT band_slug FROM proposals WHERE status = 'applied'").fetchall()
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0][0], "band-b")

        # Verify pending item is still present
        pending = conn.execute("SELECT band_slug FROM proposals WHERE status = 'pending'").fetchall()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0][0], "band-c")

        conn.close()


if __name__ == "__main__":
    unittest.main()
