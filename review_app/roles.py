"""
Role-based access control (RBAC) definitions and database helpers (GitHub issue #35).
Roles:
  - 'admin': superuser; can manage users and roles, view stats, and approve proposals.
  - 'changes-approver': can review/approve community proposals on review.daugavpils.fans.
  - 'viewer-stats': can view site statistics on daugavpils.fans/admin/.
"""
from __future__ import annotations

import sqlite3
from typing import Sequence

ROLE_ADMIN = "admin"
ROLE_APPROVER = "changes-approver"
ROLE_STATS = "viewer-stats"

ALL_ROLES = (ROLE_ADMIN, ROLE_APPROVER, ROLE_STATS)

ROLE_LABELS = {
    ROLE_ADMIN: "Admin",
    ROLE_APPROVER: "Changes Approver",
    ROLE_STATS: "Viewer Stats",
}


def get_user_roles(conn: sqlite3.Connection, user_id: int) -> set[str]:
    cur = conn.execute("SELECT role FROM user_roles WHERE user_id = ?", (user_id,))
    return {row[0] for row in cur.fetchall()}


def user_has_role(conn: sqlite3.Connection, user_id: int, role: str) -> bool:
    """Returns True if the user has the specified role, or if they have ROLE_ADMIN
    (since admin automatically includes all permissions)."""
    roles = get_user_roles(conn, user_id)
    return ROLE_ADMIN in roles or role in roles


def set_user_roles(conn: sqlite3.Connection, user_id: int, roles: Sequence[str] | set[str]) -> None:
    valid_roles = {r for r in roles if r in ALL_ROLES}
    conn.execute("DELETE FROM user_roles WHERE user_id = ?", (user_id,))
    for r in valid_roles:
        conn.execute("INSERT INTO user_roles (user_id, role) VALUES (?, ?)", (user_id, r))


def get_user_by_email(conn: sqlite3.Connection, email: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM approvers WHERE LOWER(email) = LOWER(?)", (email.strip(),)
    ).fetchone()


def get_all_users_with_roles(conn: sqlite3.Connection) -> list[dict]:
    query = """
        SELECT a.id, a.email, a.display_name, a.is_active, a.created_at,
               GROUP_CONCAT(r.role, ',') as roles_csv
        FROM approvers a
        LEFT JOIN user_roles r ON a.id = r.user_id
        GROUP BY a.id
        ORDER BY a.id ASC
    """
    rows = conn.execute(query).fetchall()
    result = []
    for row in rows:
        roles_set = set(row["roles_csv"].split(",")) if row["roles_csv"] else set()
        result.append(
            {
                "id": row["id"],
                "email": row["email"],
                "display_name": row["display_name"],
                "is_active": bool(row["is_active"]),
                "created_at": row["created_at"],
                "roles": roles_set,
            }
        )
    return result


def ensure_default_roles(conn: sqlite3.Connection, maintainer_email: str | None) -> None:
    """Idempotent startup migration:
    1. Any existing approvers with no role entries receive ROLE_APPROVER.
    2. The maintainer email (from MAINTAINER_EMAIL env) is ensured to exist and have ROLE_ADMIN.
    """
    # 1. Existing approvers without roles get changes-approver
    unassigned = conn.execute(
        """SELECT a.id FROM approvers a
           WHERE NOT EXISTS (SELECT 1 FROM user_roles r WHERE r.user_id = a.id)"""
    ).fetchall()
    for row in unassigned:
        conn.execute(
            "INSERT OR IGNORE INTO user_roles (user_id, role) VALUES (?, ?)",
            (row[0], ROLE_APPROVER),
        )

    # 2. Ensure maintainer has admin role
    if maintainer_email and maintainer_email.strip():
        m_email = maintainer_email.strip()
        user = conn.execute(
            "SELECT id FROM approvers WHERE LOWER(email) = LOWER(?)", (m_email,)
        ).fetchone()
        if user is None:
            cur = conn.execute(
                "INSERT INTO approvers (email, display_name, is_active) VALUES (?, ?, 1)",
                (m_email, "Site Maintainer"),
            )
            user_id = cur.lastrowid
        else:
            user_id = user[0]

        conn.execute(
            "INSERT OR IGNORE INTO user_roles (user_id, role) VALUES (?, ?)",
            (user_id, ROLE_ADMIN),
        )
    conn.commit()


def get_approver_recipients(conn: sqlite3.Connection, maintainer_email: str | None = None) -> list[str]:
    """Returns a deduplicated list of emails of all active users holding
    ROLE_APPROVER or ROLE_ADMIN, plus maintainer_email if configured."""
    query = """
        SELECT DISTINCT a.email
        FROM approvers a
        JOIN user_roles r ON a.id = r.user_id
        WHERE a.is_active = 1 AND r.role IN (?, ?)
    """
    rows = conn.execute(query, (ROLE_APPROVER, ROLE_ADMIN)).fetchall()
    recipients = {row[0] for row in rows}
    if maintainer_email and maintainer_email.strip():
        recipients.add(maintainer_email.strip())
    return sorted(list(recipients))
