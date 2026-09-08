"""
Passwordless email magic-link login for approvers (see GitHub issue #12).
No self-registration - the curated approver group is seeded only by the
maintainer running review_app/manage.py directly on the server.
"""
from __future__ import annotations

import hashlib
import secrets
import sys
from pathlib import Path

from flask import Blueprint, abort, current_app, redirect, render_template, request, session, url_for

sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
import mail  # noqa: E402

bp = Blueprint("auth", __name__)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@bp.get("/login")
def login_form():
    return render_template("login.html")


@bp.post("/login")
def login_request():
    conn = db.get_connection()
    ip = request.remote_addr or "unknown"

    # Same per-IP-per-hour pattern as submissions.py's create_proposal():
    # the check comes before the log INSERT below, and the INSERT happens
    # unconditionally afterwards (even for an email with no matching
    # approver) - a flood of guesses must be throttled exactly like a
    # flood of real login attempts, not exempted from it.
    limit = current_app.config["LOGIN_RATE_LIMIT_PER_IP_PER_HOUR"]
    recent = conn.execute(
        "SELECT COUNT(*) FROM login_request_log WHERE ip = ? AND requested_at > datetime('now', '-1 hour')",
        (ip,),
    ).fetchone()[0]
    if recent >= limit:
        abort(429)

    conn.execute("INSERT INTO login_request_log (ip) VALUES (?)", (ip,))
    conn.commit()

    email = request.form.get("email", "").strip()
    row = conn.execute(
        "SELECT id FROM approvers WHERE is_active = 1 AND LOWER(email) = LOWER(?)", (email,)
    ).fetchone()

    # The response is identical whether or not `email` matches an active
    # approver - no approver enumeration. Only a genuine match causes any
    # side effect (generating and emailing a token).
    if row is not None:
        token = secrets.token_urlsafe(32)
        conn.execute(
            "INSERT INTO magic_links (approver_id, token_hash, expires_at, requested_ip) "
            "VALUES (?, ?, datetime('now', '+15 minutes'), ?)",
            (row["id"], _hash_token(token), request.remote_addr),
        )
        conn.commit()
        link_url = url_for("auth.verify", token=token, _external=True)
        # The token is already committed - an SMTP failure shouldn't turn
        # into a 500 (which would itself be a signal distinguishing this
        # case from "no match", undermining the point of the identical
        # response below).
        try:
            mail.send_magic_link(current_app.config["SMTP_CONFIG"], email, link_url)
        except OSError:
            current_app.logger.exception("failed to send magic link email")

    return render_template("login_sent.html")


@bp.get("/login/verify")
def verify():
    token = request.args.get("token", "")
    conn = db.get_connection()

    # Atomic check-and-set, the same pattern dashboard.py's _decide() uses
    # for approve/reject: the UPDATE's own WHERE clause is what makes this
    # single-use, not a SELECT followed by a separate UPDATE - two
    # concurrent requests with the same token could both pass a SELECT
    # before either commits an UPDATE, consuming it twice. Folding the
    # check into the UPDATE means only one request's statement can ever
    # match the still-unused row.
    cur = conn.execute(
        "UPDATE magic_links SET used_at = datetime('now') "
        "WHERE token_hash = ? AND used_at IS NULL AND expires_at > datetime('now')",
        (_hash_token(token),),
    )
    conn.commit()
    if cur.rowcount != 1:
        return render_template("login_invalid.html"), 400

    row = conn.execute(
        "SELECT approver_id FROM magic_links WHERE token_hash = ?", (_hash_token(token),)
    ).fetchone()
    session["approver_id"] = row["approver_id"]
    return redirect(url_for("dashboard.view_pending"))
