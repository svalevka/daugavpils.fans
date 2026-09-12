#!/usr/bin/env python3
"""
Tests for role-based access control (RBAC) helpers (GitHub issue #35).
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
import roles  # noqa: E402


class RolesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.database_path = Path(self._tmp.name) / "test.db"
        db.init_schema(self.database_path, maintainer_email="maint@example.com")
        self.conn = sqlite3.connect(self.database_path)
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)

    def test_ensure_default_roles_seeds_maintainer(self):
        maint = roles.get_user_by_email(self.conn, "maint@example.com")
        self.assertIsNotNone(maint)
        maint_roles = roles.get_user_roles(self.conn, maint["id"])
        self.assertIn(roles.ROLE_ADMIN, maint_roles)
        self.assertTrue(roles.user_has_role(self.conn, maint["id"], roles.ROLE_ADMIN))
        self.assertTrue(roles.user_has_role(self.conn, maint["id"], roles.ROLE_APPROVER))
        self.assertTrue(roles.user_has_role(self.conn, maint["id"], roles.ROLE_STATS))

    def test_set_and_get_user_roles(self):
        cur = self.conn.execute(
            "INSERT INTO approvers (email, display_name, is_active) VALUES ('test@example.com', 'Tester', 1)"
        )
        user_id = cur.lastrowid
        roles.set_user_roles(self.conn, user_id, [roles.ROLE_APPROVER, roles.ROLE_STATS])
        self.conn.commit()

        user_roles = roles.get_user_roles(self.conn, user_id)
        self.assertEqual(user_roles, {roles.ROLE_APPROVER, roles.ROLE_STATS})
        self.assertTrue(roles.user_has_role(self.conn, user_id, roles.ROLE_APPROVER))
        self.assertTrue(roles.user_has_role(self.conn, user_id, roles.ROLE_STATS))
        self.assertFalse(roles.user_has_role(self.conn, user_id, roles.ROLE_ADMIN))

        # Admin role implicitly grants all permissions
        roles.set_user_roles(self.conn, user_id, [roles.ROLE_ADMIN])
        self.conn.commit()
        self.assertTrue(roles.user_has_role(self.conn, user_id, roles.ROLE_APPROVER))
        self.assertTrue(roles.user_has_role(self.conn, user_id, roles.ROLE_STATS))
        self.assertTrue(roles.user_has_role(self.conn, user_id, roles.ROLE_ADMIN))

    def test_get_all_users_with_roles(self):
        self.conn.execute(
            "INSERT INTO approvers (email, display_name, is_active) VALUES ('u1@example.com', 'User One', 1)"
        )
        u1_id = self.conn.execute("SELECT id FROM approvers WHERE email = 'u1@example.com'").fetchone()[0]
        roles.set_user_roles(self.conn, u1_id, [roles.ROLE_STATS])
        self.conn.commit()

        users = roles.get_all_users_with_roles(self.conn)
        emails = {u["email"] for u in users}
        self.assertIn("maint@example.com", emails)
        self.assertIn("u1@example.com", emails)

        u1_info = next(u for u in users if u["email"] == "u1@example.com")
        self.assertEqual(u1_info["roles"], {roles.ROLE_STATS})
        self.assertTrue(u1_info["is_active"])

    def test_get_approver_recipients(self):
        # Insert approver
        cur = self.conn.execute(
            "INSERT INTO approvers (email, display_name, is_active) VALUES ('appr@example.com', 'Appr', 1)"
        )
        roles.set_user_roles(self.conn, cur.lastrowid, [roles.ROLE_APPROVER])

        # Insert stats viewer only
        cur = self.conn.execute(
            "INSERT INTO approvers (email, display_name, is_active) VALUES ('stats@example.com', 'Stats', 1)"
        )
        roles.set_user_roles(self.conn, cur.lastrowid, [roles.ROLE_STATS])

        # Insert inactive approver
        cur = self.conn.execute(
            "INSERT INTO approvers (email, display_name, is_active) VALUES ('inactive@example.com', 'Inactive', 0)"
        )
        roles.set_user_roles(self.conn, cur.lastrowid, [roles.ROLE_APPROVER])
        self.conn.commit()

        recipients = roles.get_approver_recipients(self.conn, "maint@example.com")
        self.assertIn("appr@example.com", recipients)
        self.assertIn("maint@example.com", recipients)
        self.assertNotIn("stats@example.com", recipients)
        self.assertNotIn("inactive@example.com", recipients)


if __name__ == "__main__":
    unittest.main()
