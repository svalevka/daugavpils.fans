#!/usr/bin/env python3
"""
Maintainer-only CLI to manage authorized users and RBAC roles directly against
review_app's database (GitHub issues #12 and #35). Run this on the server itself.

Usage:
    python manage.py add-approver <email> <display_name> [--database-path PATH]
    python manage.py add-user <email> <display_name> [--roles ROLE ...] [--database-path PATH]
    python manage.py list-users [--database-path PATH]
    python manage.py set-roles <email> <role> [<role> ...] [--database-path PATH]
    python manage.py deactivate-user <email> [--database-path PATH]

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

import anomaly_detector  # noqa: E402
import db  # noqa: E402
import mail  # noqa: E402
import roles  # noqa: E402
from config import SmtpConfig  # noqa: E402


def _connect(database_path: Path) -> sqlite3.Connection:
    db.init_schema(database_path)
    conn = sqlite3.connect(database_path)
    conn.row_factory = sqlite3.Row
    return conn


def add_user(
    conn: sqlite3.Connection,
    email: str,
    display_name: str,
    user_roles: list[str] | None = None,
) -> int:
    assigned_roles = user_roles if user_roles is not None else [roles.ROLE_APPROVER]
    invalid = [r for r in assigned_roles if r not in roles.ALL_ROLES]
    if invalid:
        print(f"error: invalid role(s): {', '.join(invalid)}. Allowed: {', '.join(roles.ALL_ROLES)}", file=sys.stderr)
        return 1

    try:
        cur = conn.execute(
            "INSERT INTO approvers (email, display_name) VALUES (?, ?)", (email.strip(), display_name.strip())
        )
    except sqlite3.IntegrityError:
        print(f"error: a user with email {email!r} already exists", file=sys.stderr)
        return 1

    user_id = cur.lastrowid
    roles.set_user_roles(conn, user_id, assigned_roles)
    conn.commit()
    print(f"added user {email!r} (id {user_id}, roles: {', '.join(assigned_roles)})")
    return 0


def set_user_roles_cmd(conn: sqlite3.Connection, email: str, new_roles: list[str]) -> int:
    user = roles.get_user_by_email(conn, email)
    if user is None:
        print(f"error: no user with email {email!r}", file=sys.stderr)
        return 1

    valid_roles = [r for r in new_roles if r in roles.ALL_ROLES]
    if not valid_roles:
        print(f"error: must specify at least one valid role ({', '.join(roles.ALL_ROLES)})", file=sys.stderr)
        return 1

    roles.set_user_roles(conn, user["id"], valid_roles)
    conn.commit()
    print(f"updated roles for {email!r}: {', '.join(valid_roles)}")
    return 0


def list_users(conn: sqlite3.Connection) -> int:
    users = roles.get_all_users_with_roles(conn)
    if not users:
        print("no approvers")
        return 0
    for u in users:
        status = "active" if u["is_active"] else "inactive"
        roles_str = ",".join(sorted(u["roles"])) if u["roles"] else "no-roles"
        print(f"{u['id']}\t{u['email']}\t{u['display_name']}\t{status}\t{roles_str}")
    return 0


def deactivate_user(conn: sqlite3.Connection, email: str) -> int:
    cur = conn.execute("UPDATE approvers SET is_active = 0 WHERE LOWER(email) = LOWER(?)", (email.strip(),))
    conn.commit()
    if cur.rowcount == 0:
        print(f"error: no approver with email {email!r}", file=sys.stderr)
        return 1
    print(f"deactivated {email!r}")
    return 0


def check_anomalies_cmd(
    conn: sqlite3.Connection,
    database_path: Path,
    uploads_path: Path | None = None,
    notify: bool = False,
    digest: bool = False,
) -> int:
    resolved_uploads = uploads_path or (database_path.parent / "uploads")

    smtp_config: SmtpConfig | None = None
    maintainer_email = os.environ.get("MAINTAINER_EMAIL")
    if notify or digest:
        smtp_host = os.environ.get("SMTP_HOST")
        smtp_port = int(os.environ.get("SMTP_PORT", "587"))
        smtp_from = os.environ.get("SMTP_FROM")
        smtp_user = os.environ.get("SMTP_USER")
        smtp_pass = os.environ.get("SMTP_PASSWORD")
        if smtp_host and smtp_from:
            smtp_config = SmtpConfig(
                host=smtp_host,
                port=smtp_port,
                from_addr=smtp_from,
                user=smtp_user,
                password=smtp_pass,
            )

    dashboard_url = os.environ.get("ADMIN_DASHBOARD_URL", "https://daugavpils.fans/admin/anomalies/")

    new_anomalies = anomaly_detector.run_anomaly_detection(
        conn,
        database_path=database_path,
        uploads_path=resolved_uploads,
        notify=notify,
        smtp_config=smtp_config,
        maintainer_email=maintainer_email,
        dashboard_url=dashboard_url,
    )

    active_anomalies = anomaly_detector.get_anomalies(conn, active_only=True)
    crit_count = sum(1 for a in active_anomalies if a.get("severity") == anomaly_detector.SEVERITY_CRITICAL)
    warn_count = sum(1 for a in active_anomalies if a.get("severity") == anomaly_detector.SEVERITY_WARNING)
    info_count = sum(1 for a in active_anomalies if a.get("severity") == anomaly_detector.SEVERITY_INFO)

    print(f"Anomaly Audit Summary:")
    print(f"  New anomalies recorded in this run: {len(new_anomalies)}")
    print(f"  Total active unacknowledged anomalies: {len(active_anomalies)} ({crit_count} CRITICAL, {warn_count} WARNING, {info_count} INFO)")

    if active_anomalies:
        print("\nActive Anomalies:")
        for a in active_anomalies:
            print(f"  [{a['severity']}] #{a['id']} {a['title']} ({a['created_at']})")
            print(f"      {a['message']}")
    else:
        print("\nAll systems normal. No active anomalies.")

    if digest and smtp_config and maintainer_email:
        summary = anomaly_detector.get_system_health_summary(
            conn, database_path=database_path, uploads_path=resolved_uploads
        )
        mail.send_anomaly_digest(
            smtp_config,
            maintainer_email,
            active_anomalies,
            stats_summary=summary,
            dashboard_url=dashboard_url,
        )
        print(f"\nDispatched daily health digest to {maintainer_email}")

    # Return exit code 1 if critical anomalies exist, otherwise 0
    return 1 if crit_count > 0 else 0


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

    # add-approver (backward compatible alias for add-user with changes-approver role)
    add_approver_parser = subparsers.add_parser("add-approver", help="add a new active approver")
    add_approver_parser.add_argument("email", help="the approver's email - what they'll log in with")
    add_approver_parser.add_argument("display_name", help="shown to other approvers, e.g. on the dashboard")

    # add-user with explicit roles
    add_user_parser = subparsers.add_parser("add-user", help="add a new active user with roles")
    add_user_parser.add_argument("email", help="the user's email")
    add_user_parser.add_argument("display_name", help="user display name")
    add_user_parser.add_argument(
        "--roles",
        nargs="+",
        default=[roles.ROLE_APPROVER],
        choices=roles.ALL_ROLES,
        help="roles to assign (default: changes-approver)",
    )

    # list-approvers & list-users
    subparsers.add_parser("list-approvers", help="list every user and their roles")
    subparsers.add_parser("list-users", help="list every user and their roles")

    # set-roles
    set_roles_parser = subparsers.add_parser("set-roles", help="update a user's roles")
    set_roles_parser.add_argument("email", help="the user's email")
    set_roles_parser.add_argument(
        "roles",
        nargs="+",
        choices=roles.ALL_ROLES,
        help="roles to assign (admin, changes-approver, viewer-stats)",
    )

    # deactivate-approver & deactivate-user
    deactivate_parser = subparsers.add_parser(
        "deactivate-approver", help="revoke a user's access without deleting their history"
    )
    deactivate_parser.add_argument("email", help="the email to deactivate")

    deactivate_user_parser = subparsers.add_parser(
        "deactivate-user", help="revoke a user's access without deleting their history"
    )
    deactivate_user_parser.add_argument("email", help="the email to deactivate")

    # check-anomalies
    check_anomalies_parser = subparsers.add_parser(
        "check-anomalies", help="audit system telemetry and report statistical anomalies"
    )
    check_anomalies_parser.add_argument(
        "--uploads-path",
        type=Path,
        default=(
            Path(os.environ["MEDIA_UPLOADS_PATH"])
            if "MEDIA_UPLOADS_PATH" in os.environ
            else None
        ),
        help="path to media uploads directory (default: sibling 'uploads' of database)",
    )
    check_anomalies_parser.add_argument(
        "--notify",
        action="store_true",
        help="send immediate email alert to maintainer for any CRITICAL anomalies",
    )
    check_anomalies_parser.add_argument(
        "--digest",
        action="store_true",
        help="send system health and anomaly digest email to maintainer",
    )

    args = parser.parse_args()

    conn = _connect(args.database_path)
    try:
        if args.command == "add-approver":
            return add_user(conn, args.email, args.display_name, [roles.ROLE_APPROVER])
        if args.command == "add-user":
            return add_user(conn, args.email, args.display_name, args.roles)
        if args.command in ("list-approvers", "list-users"):
            return list_users(conn)
        if args.command == "set-roles":
            return set_user_roles_cmd(conn, args.email, args.roles)
        if args.command in ("deactivate-approver", "deactivate-user"):
            return deactivate_user(conn, args.email)
        if args.command == "check-anomalies":
            return check_anomalies_cmd(
                conn,
                args.database_path,
                uploads_path=args.uploads_path,
                notify=args.notify,
                digest=args.digest,
            )
        raise AssertionError(f"unreachable: unknown command {args.command!r}")
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
