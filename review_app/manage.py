#!/usr/bin/env python3
"""
Maintainer-only CLI to manage the curated approver group directly against
review_app's database (see GitHub issue #12) - no self-registration, no
admin UI in this version. Run this on the server itself.

Usage:
    python manage.py add-approver <email> <display_name> [--database-path PATH]
    python manage.py list-approvers [--database-path PATH]
    python manage.py deactivate-approver <email> [--database-path PATH]

--database-path defaults to $DATABASE_PATH if set (the same variable the
app itself reads via Config.from_env()).
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402


def _connect(database_path: Path) -> sqlite3.Connection:
    db.init_schema(database_path)
    conn = sqlite3.connect(database_path)
    conn.row_factory = sqlite3.Row
    return conn


def add_approver(conn: sqlite3.Connection, email: str, display_name: str) -> int:
    try:
        cur = conn.execute(
            "INSERT INTO approvers (email, display_name) VALUES (?, ?)", (email, display_name)
        )
    except sqlite3.IntegrityError:
        print(f"error: an approver with email {email!r} already exists", file=sys.stderr)
        return 1
    conn.commit()
    print(f"added approver {email!r} (id {cur.lastrowid})")
    return 0


def list_approvers(conn: sqlite3.Connection) -> int:
    rows = conn.execute("SELECT id, email, display_name, is_active FROM approvers ORDER BY id").fetchall()
    if not rows:
        print("no approvers")
        return 0
    for row in rows:
        status = "active" if row["is_active"] else "inactive"
        print(f"{row['id']}\t{row['email']}\t{row['display_name']}\t{status}")
    return 0


def deactivate_approver(conn: sqlite3.Connection, email: str) -> int:
    cur = conn.execute("UPDATE approvers SET is_active = 0 WHERE LOWER(email) = LOWER(?)", (email,))
    conn.commit()
    if cur.rowcount == 0:
        print(f"error: no approver with email {email!r}", file=sys.stderr)
        return 1
    print(f"deactivated {email!r}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    default_db_path = Path(os.environ["DATABASE_PATH"]) if "DATABASE_PATH" in os.environ else None
    parser.add_argument(
        "--database-path",
        type=Path,
        default=default_db_path,
        required=default_db_path is None,
        help="path to review_app's SQLite database (default: $DATABASE_PATH)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add-approver", help="add a new active approver")
    add_parser.add_argument("email", help="the approver's email - what they'll log in with")
    add_parser.add_argument("display_name", help="shown to other approvers, e.g. on the dashboard")

    subparsers.add_parser("list-approvers", help="list every approver and whether they're active")

    deactivate_parser = subparsers.add_parser(
        "deactivate-approver", help="revoke an approver's access without deleting their history"
    )
    deactivate_parser.add_argument("email", help="the approver to deactivate")

    args = parser.parse_args()

    conn = _connect(args.database_path)
    try:
        if args.command == "add-approver":
            return add_approver(conn, args.email, args.display_name)
        if args.command == "list-approvers":
            return list_approvers(conn)
        if args.command == "deactivate-approver":
            return deactivate_approver(conn, args.email)
        raise AssertionError(f"unreachable: unknown command {args.command!r}")
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
