#!/usr/bin/env python3
"""
Tests for site maintainer admin authentication and statistics dashboard (GitHub issue #34).
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


class AdminAuthTest(ReviewAppTestCase):
    def setUp(self):
        super().setUp()
        self.mock_send_admin_link = self._patch("admin.mail.send_admin_magic_link")

    def test_unauthenticated_access_redirects_to_login(self):
        response = self.client.get("/admin/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login", response.headers["Location"])

    def test_login_form_renders(self):
        response = self.client.get("/admin/login")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Maintainer login", response.data)

    def test_maintainer_email_sends_link(self):
        response = self.client.post("/admin/login", data={"email": self.config.maintainer_email})
        self.assertEqual(response.status_code, 200)
        self.mock_send_admin_link.assert_called_once()
        _smtp_config, to_addr, link_url = self.mock_send_admin_link.call_args.args
        self.assertEqual(to_addr, self.config.maintainer_email)
        self.assertIn("/admin/verify?token=", link_url)

    def test_non_maintainer_email_does_not_send_link_but_returns_identical_status(self):
        valid = self.client.post("/admin/login", data={"email": self.config.maintainer_email})
        self.mock_send_admin_link.reset_mock()

        invalid = self.client.post("/admin/login", data={"email": "intruder@example.com"})
        self.assertEqual(invalid.status_code, valid.status_code)
        self.assertEqual(invalid.data, valid.data)
        self.mock_send_admin_link.assert_not_called()

    def test_viewer_stats_user_can_request_admin_login(self):
        user_id = self.seed_approver("stats@example.com", roles=["viewer-stats"])
        res = self.client.post("/admin/login", data={"email": "stats@example.com"})
        self.assertEqual(res.status_code, 200)
        self.mock_send_admin_link.assert_called_once()
        _smtp, to_addr, link_url = self.mock_send_admin_link.call_args.args
        self.assertEqual(to_addr, "stats@example.com")
        token = _extract_token(link_url)

        verify_res = self.client.get(f"/admin/verify?token={token}")
        self.assertEqual(verify_res.status_code, 302)
        self.assertIn("/admin/", verify_res.headers["Location"])
        with self.client.session_transaction() as sess:
            self.assertIn("viewer-stats", sess.get("roles", []))

    def test_changes_approver_only_cannot_request_admin_login(self):
        self.seed_approver("approver@example.com", roles=["changes-approver"])
        res = self.client.post("/admin/login", data={"email": "approver@example.com"})
        self.assertEqual(res.status_code, 200)
        self.mock_send_admin_link.assert_not_called()

    def test_rate_limiting(self):
        for _ in range(self.config.login_rate_limit_per_ip_per_hour):
            res = self.client.post("/admin/login", data={"email": "someone@example.com"})
            self.assertEqual(res.status_code, 200)
        res = self.client.post("/admin/login", data={"email": "someone@example.com"})
        self.assertEqual(res.status_code, 429)

    def test_successful_verification(self):
        self.client.post("/admin/login", data={"email": self.config.maintainer_email})
        _smtp, _to, link_url = self.mock_send_admin_link.call_args.args
        token = _extract_token(link_url)

        res = self.client.get(f"/admin/verify?token={token}")
        self.assertEqual(res.status_code, 302)
        self.assertIn("/admin/", res.headers["Location"])

        with self.client.session_transaction() as sess:
            self.assertTrue(sess.get("is_admin"))

    def test_token_is_single_use(self):
        self.client.post("/admin/login", data={"email": self.config.maintainer_email})
        _smtp, _to, link_url = self.mock_send_admin_link.call_args.args
        token = _extract_token(link_url)

        first = self.client.get(f"/admin/verify?token={token}")
        self.assertEqual(first.status_code, 302)

        with self.client.session_transaction() as sess:
            sess.clear()

        second = self.client.get(f"/admin/verify?token={token}")
        self.assertEqual(second.status_code, 400)

    def test_expired_or_invalid_token(self):
        res = self.client.get("/admin/verify?token=fake-token")
        self.assertEqual(res.status_code, 400)

    def test_logout(self):
        with self.client.session_transaction() as sess:
            sess["is_admin"] = True

        res = self.client.post("/admin/logout")
        self.assertEqual(res.status_code, 302)
        self.assertIn("/admin/login", res.headers["Location"])

        with self.client.session_transaction() as sess:
            self.assertNotIn("is_admin", sess)


class AdminDashboardTest(ReviewAppTestCase):
    def setUp(self):
        super().setUp()
        self.login_as_admin()

    def login_as_admin(self):
        with self.client.session_transaction() as sess:
            sess["is_admin"] = True

    def _seed_event(self, event_type, path, **kwargs):
        conn = sqlite3.connect(self.database_path)
        conn.execute(
            """INSERT INTO analytics_events (
                event_type, path, band_slug, release_slug, track_name, video_name,
                referrer_domain, country_code, device_type, visitor_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event_type,
                path,
                kwargs.get("band_slug"),
                kwargs.get("release_slug"),
                kwargs.get("track_name"),
                kwargs.get("video_name"),
                kwargs.get("referrer_domain", "Direct"),
                kwargs.get("country_code", "LV"),
                kwargs.get("device_type", "desktop"),
                kwargs.get("visitor_hash", "hash-1"),
                kwargs.get("created_at", "datetime('now')"),
            ),
        )
        conn.commit()
        conn.close()

    def test_dashboard_renders_stats(self):
        self._seed_event("pageview", "/bands/dvinsk/", band_slug="dvinsk", visitor_hash="v1")
        self._seed_event("pageview", "/en/bands/dvinsk/", band_slug="dvinsk", visitor_hash="v2")
        self._seed_event("track_play", "/bands/dvinsk/1993-karmannyi-mir/", band_slug="dvinsk", release_slug="1993-karmannyi-mir", track_name="01-prosnites-liudi.flac", visitor_hash="v1")
        self._seed_event("video_play", "/bands/khoriniye-bega/", band_slug="khoriniye-bega", video_name="Га-га-га", visitor_hash="v1")
        self._seed_event("media_error", "/bands/glazki-stekolshika/chernovyaki/", band_slug="glazki-stekolshika", release_slug="chernovyaki", track_name="05-ivanovo.mp3", visitor_hash="v1")

        response = self.client.get("/admin/?period=all")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Unique Visitors", response.data)
        self.assertIn(b"Media Errors (archive.org)", response.data)
        self.assertIn(b"01-prosnites-liudi.flac", response.data)
        self.assertIn("Га-га-га".encode("utf-8"), response.data)
        self.assertIn(b"/bands/dvinsk/", response.data)

    def test_period_filters(self):
        for p in ("today", "7d", "30d", "all"):
            response = self.client.get(f"/admin/?period={p}")
            self.assertEqual(response.status_code, 200)


class AdminUserManagementTest(ReviewAppTestCase):
    def setUp(self):
        super().setUp()
        self.mock_send_welcome = self._patch("admin.mail.send_welcome_invitation")
        self.mock_send_admin_link = self._patch("admin.mail.send_admin_magic_link")

    def login_as_admin(self, user_id: int | None = None):
        with self.client.session_transaction() as sess:
            sess["is_admin"] = True
            sess["roles"] = ["admin"]
            if user_id:
                sess["user_id"] = user_id
                sess["approver_id"] = user_id

    def login_as_stats_viewer(self, user_id: int | None = None):
        with self.client.session_transaction() as sess:
            sess["is_admin"] = False
            sess["roles"] = ["viewer-stats"]
            if user_id:
                sess["user_id"] = user_id
                sess["approver_id"] = user_id

    def test_unauthenticated_access_to_users_redirects_to_login(self):
        res = self.client.get("/admin/users/")
        self.assertEqual(res.status_code, 302)
        self.assertIn("/admin/login", res.headers["Location"])

    def test_stats_viewer_cannot_access_user_management(self):
        uid = self.seed_approver("stats@example.com", roles=["viewer-stats"])
        self.login_as_stats_viewer(uid)
        res = self.client.get("/admin/users/")
        self.assertEqual(res.status_code, 403)

    def test_admin_can_access_user_management(self):
        uid = self.seed_approver("admin@example.com", roles=["admin"])
        self.login_as_admin(uid)
        res = self.client.get("/admin/users/")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Users &amp; Roles", res.data)
        self.assertIn(b"Add New User", res.data)

    def test_add_user_creates_account_and_sends_welcome_email(self):
        uid = self.seed_approver("admin@example.com", roles=["admin"])
        self.login_as_admin(uid)

        res = self.client.post(
            "/admin/users/add",
            data={
                "email": "newuser@example.com",
                "display_name": "New User",
                "roles": ["changes-approver", "viewer-stats"],
            },
        )
        self.assertEqual(res.status_code, 302)
        self.assertIn("/admin/users/", res.headers["Location"])

        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        user = conn.execute("SELECT * FROM approvers WHERE email = 'newuser@example.com'").fetchone()
        self.assertIsNotNone(user)
        self.assertEqual(user["display_name"], "New User")
        self.assertEqual(user["is_active"], 1)

        user_roles = {r[0] for r in conn.execute("SELECT role FROM user_roles WHERE user_id = ?", (user["id"],)).fetchall()}
        conn.close()
        self.assertEqual(user_roles, {"changes-approver", "viewer-stats"})

        self.mock_send_welcome.assert_called_once()
        _smtp, to_addr, name, role_labels, login_url = self.mock_send_welcome.call_args.args
        self.assertEqual(to_addr, "newuser@example.com")
        self.assertEqual(name, "New User")
        self.assertIn("/admin/verify?token=", login_url)

    def test_update_user_roles(self):
        admin_id = self.seed_approver("admin@example.com", roles=["admin"])
        target_id = self.seed_approver("target@example.com", roles=["changes-approver"])
        self.login_as_admin(admin_id)

        res = self.client.post(
            f"/admin/users/{target_id}/roles",
            data={"roles": ["viewer-stats"]},
        )
        self.assertEqual(res.status_code, 302)

        conn = sqlite3.connect(self.database_path)
        user_roles = {r[0] for r in conn.execute("SELECT role FROM user_roles WHERE user_id = ?", (target_id,)).fetchall()}
        conn.close()
        self.assertEqual(user_roles, {"viewer-stats"})

    def test_cannot_remove_admin_role_from_maintainer(self):
        admin_id = self.seed_approver("admin@example.com", roles=["admin"])
        conn = sqlite3.connect(self.database_path)
        maint_id = conn.execute("SELECT id FROM approvers WHERE email = ?", (self.config.maintainer_email,)).fetchone()[0]
        conn.close()
        self.login_as_admin(admin_id)

        self.client.post(
            f"/admin/users/{maint_id}/roles",
            data={"roles": ["viewer-stats"]},
        )

        conn = sqlite3.connect(self.database_path)
        user_roles = {r[0] for r in conn.execute("SELECT role FROM user_roles WHERE user_id = ?", (maint_id,)).fetchall()}
        conn.close()
        self.assertIn("admin", user_roles)

    def test_cannot_remove_admin_role_from_self(self):
        admin_id = self.seed_approver("admin@example.com", roles=["admin"])
        self.login_as_admin(admin_id)

        self.client.post(
            f"/admin/users/{admin_id}/roles",
            data={"roles": ["viewer-stats"]},
        )

        conn = sqlite3.connect(self.database_path)
        user_roles = {r[0] for r in conn.execute("SELECT role FROM user_roles WHERE user_id = ?", (admin_id,)).fetchall()}
        conn.close()
        self.assertIn("admin", user_roles)

    def test_toggle_active_status(self):
        admin_id = self.seed_approver("admin@example.com", roles=["admin"])
        target_id = self.seed_approver("target@example.com", roles=["changes-approver"])
        self.login_as_admin(admin_id)

        # Deactivate
        res = self.client.post(f"/admin/users/{target_id}/toggle-active")
        self.assertEqual(res.status_code, 302)
        conn = sqlite3.connect(self.database_path)
        status = conn.execute("SELECT is_active FROM approvers WHERE id = ?", (target_id,)).fetchone()[0]
        self.assertEqual(status, 0)

        # Reactivate
        res = self.client.post(f"/admin/users/{target_id}/toggle-active")
        self.assertEqual(res.status_code, 302)
        status = conn.execute("SELECT is_active FROM approvers WHERE id = ?", (target_id,)).fetchone()[0]
        self.assertEqual(status, 1)
        conn.close()

    def test_cannot_deactivate_maintainer_or_self(self):
        admin_id = self.seed_approver("admin@example.com", roles=["admin"])
        conn = sqlite3.connect(self.database_path)
        maint_id = conn.execute("SELECT id FROM approvers WHERE email = ?", (self.config.maintainer_email,)).fetchone()[0]
        conn.close()
        self.login_as_admin(admin_id)

        res_maint = self.client.post(f"/admin/users/{maint_id}/toggle-active")
        self.assertEqual(res_maint.status_code, 400)

        res_self = self.client.post(f"/admin/users/{admin_id}/toggle-active")
        self.assertEqual(res_self.status_code, 400)

    def test_deactivated_user_loses_access_immediately(self):
        uid = self.seed_approver("stats@example.com", roles=["viewer-stats"])
        self.login_as_stats_viewer(uid)

        # Initially can view
        res = self.client.get("/admin/")
        self.assertEqual(res.status_code, 200)

        # Deactivate in DB
        conn = sqlite3.connect(self.database_path)
        conn.execute("UPDATE approvers SET is_active = 0 WHERE id = ?", (uid,))
        conn.commit()
        conn.close()

        # Immediate revocation on next request
        res2 = self.client.get("/admin/")
        self.assertEqual(res2.status_code, 302)
        self.assertIn("/admin/login", res2.headers["Location"])


if __name__ == "__main__":
    unittest.main()
