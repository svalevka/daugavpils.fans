"""
The approver dashboard - list pending proposals, approve/reject them (see
GitHub issue #12). Session-gated: session["approver_id"] must name an
active approver, re-checked on every request rather than trusted as-is -
an approver deactivated after logging in must lose access immediately,
not just on their next login (same defensive stance submissions.py
already takes toward a stale session when stamping a submission).

Approving/rejecting is triggered from here, but does not itself talk to
GitHub - that (the actual git-write pipeline) is #13's job, built on top
of the status transition this file makes.
"""
from __future__ import annotations

import functools
import json
import sqlite3
import sys
from pathlib import Path

from flask import Blueprint, abort, g, redirect, render_template, request, session, url_for

sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402

bp = Blueprint("dashboard", __name__)


def _current_approver() -> sqlite3.Row | None:
    approver_id = session.get("approver_id")
    if approver_id is None:
        return None
    conn = db.get_connection()
    return conn.execute(
        "SELECT id, email, display_name FROM approvers WHERE id = ? AND is_active = 1", (approver_id,)
    ).fetchone()


def require_approver(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        approver = _current_approver()
        if approver is None:
            if request.method == "GET":
                return redirect(url_for("auth.login_form"))
            abort(401)
        g.approver = approver
        return view(*args, **kwargs)

    return wrapped


@bp.get("/dashboard")
@require_approver
def view_pending():
    conn = db.get_connection()
    rows = conn.execute("SELECT * FROM proposals WHERE status = 'pending' ORDER BY created_at").fetchall()
    proposals = [
        {
            "id": row["id"],
            "band_slug": row["band_slug"],
            "release_slug": row["release_slug"],
            "target": row["target"],
            "field": row["field"],
            "list_index": row["list_index"],
            "original_value": json.loads(row["original_value"]),
            "proposed_value": json.loads(row["proposed_value"]),
            "submitter_name": row["submitter_name"],
            "submitter_contact": row["submitter_contact"],
            "is_own_submission": row["submitted_by_approver_id"] == g.approver["id"],
        }
        for row in rows
    ]
    return render_template("dashboard.html", proposals=proposals, approver=g.approver)


def _decide(proposal_id: int, new_status: str) -> tuple[bool, str]:
    """Atomic check-and-set: a proposal only flips out of `pending` once,
    to whoever's UPDATE actually matches the WHERE clause first - two
    simultaneous decisions can't both win, and the proposal's own stamped
    submitter can never match the WHERE clause at all. Returns (True, "")
    on success; on failure, a reason ("not_found"/"self_approval"/
    "already_decided") from a follow-up SELECT purely to give the caller
    a specific, honest response - that SELECT plays no role in the actual
    correctness guarantee, which the atomic UPDATE alone provides."""
    conn = db.get_connection()
    approver_id = g.approver["id"]
    cur = conn.execute(
        """
        UPDATE proposals
        SET status = ?, decided_by = ?, decided_at = datetime('now')
        WHERE id = ? AND status = 'pending'
          AND (submitted_by_approver_id IS NULL OR submitted_by_approver_id != ?)
        """,
        (new_status, approver_id, proposal_id, approver_id),
    )
    conn.commit()
    if cur.rowcount == 1:
        return True, ""

    row = conn.execute(
        "SELECT status, submitted_by_approver_id FROM proposals WHERE id = ?", (proposal_id,)
    ).fetchone()
    if row is None:
        return False, "not_found"
    if row["submitted_by_approver_id"] == approver_id:
        return False, "self_approval"
    return False, "already_decided"


def _abort_for_reason(reason: str) -> None:
    if reason == "not_found":
        abort(404)
    if reason == "self_approval":
        abort(403)
    abort(409)  # already decided by someone else


@bp.post("/proposals/<int:proposal_id>/approve")
@require_approver
def approve(proposal_id: int):
    ok, reason = _decide(proposal_id, "approved")
    if not ok:
        _abort_for_reason(reason)
    return redirect(url_for("dashboard.view_pending"))


@bp.post("/proposals/<int:proposal_id>/reject")
@require_approver
def reject(proposal_id: int):
    # Silent drop: no submitter notification, nothing further happens.
    ok, reason = _decide(proposal_id, "rejected")
    if not ok:
        _abort_for_reason(reason)
    return redirect(url_for("dashboard.view_pending"))
