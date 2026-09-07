#!/usr/bin/env python3
"""
review_app/manage.py, the maintainer-only CLI for seeding/listing/
deactivating approvers (GitHub issue #12). Invoked as a real subprocess
against a scratch database, matching tools/'s own "invoke the real CLI,
assert on exit code and stdout" idiom (see e.g. tools/test_apply_proposal.py).
"""
from __future__ import annotations

import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

MANAGE_PY = Path(__file__).resolve().parent / "manage.py"


def run_manage(database_path: Path, *args: str) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(
        [sys.executable, str(MANAGE_PY), "--database-path", str(database_path), *args],
        capture_output=True,
        text=True,
    )


class ManageApproverCliTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.database_path = Path(self._tmp.name) / "review.db"

    def test_add_list_and_deactivate_approver(self):
        add_result = run_manage(self.database_path, "add-approver", "ryb@example.com", "Ryb")
        self.assertEqual(add_result.returncode, 0, msg=add_result.stdout + add_result.stderr)

        list_result = run_manage(self.database_path, "list-approvers")
        self.assertEqual(list_result.returncode, 0)
        self.assertIn("ryb@example.com", list_result.stdout)
        self.assertIn("active", list_result.stdout)

        deactivate_result = run_manage(self.database_path, "deactivate-approver", "ryb@example.com")
        self.assertEqual(deactivate_result.returncode, 0, msg=deactivate_result.stdout + deactivate_result.stderr)

        conn = sqlite3.connect(self.database_path)
        try:
            row = conn.execute(
                "SELECT is_active FROM approvers WHERE email = ?", ("ryb@example.com",)
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(row[0], 0)

    def test_list_approvers_with_none_seeded(self):
        result = run_manage(self.database_path, "list-approvers")
        self.assertEqual(result.returncode, 0)
        self.assertIn("no approvers", result.stdout)

    def test_adding_a_duplicate_email_fails(self):
        run_manage(self.database_path, "add-approver", "ryb@example.com", "Ryb")

        result = run_manage(self.database_path, "add-approver", "ryb@example.com", "Someone Else")

        self.assertNotEqual(result.returncode, 0)

    def test_deactivating_an_unknown_email_fails(self):
        result = run_manage(self.database_path, "deactivate-approver", "nobody@example.com")
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
