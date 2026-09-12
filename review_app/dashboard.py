"""
The approver dashboard - list pending proposals, approve/reject them (see
GitHub issue #12). Session-gated: session["approver_id"] must name an
active approver, re-checked on every request rather than trusted as-is -
an approver deactivated after logging in must lose access immediately,
not just on their next login (same defensive stance submissions.py
already takes toward a stale session when stamping a submission).

Approving triggers the actual git-write pipeline (see GitHub issue #13):
once the status transition below lands, it calls github_dispatch to start
the apply-proposal.yml Action - passing only the proposal id, never the
proposed text.
"""
from __future__ import annotations

import functools
import json
import sqlite3
import sys
from pathlib import Path

from flask import Blueprint, abort, current_app, g, redirect, render_template, request, send_file, session, url_for

sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
import github_dispatch  # noqa: E402
import mail  # noqa: E402
import roles  # noqa: E402

bp = Blueprint("dashboard", __name__)


def _current_approver() -> sqlite3.Row | None:
    approver_id = session.get("approver_id")
    if approver_id is None:
        return None
    conn = db.get_connection()
    row = conn.execute(
        "SELECT id, email, display_name FROM approvers WHERE id = ? AND is_active = 1", (approver_id,)
    ).fetchone()
    if row is None:
        return None
    maintainer_email = current_app.config.get("MAINTAINER_EMAIL", "").strip().lower()
    is_maintainer = bool(maintainer_email and row["email"].lower() == maintainer_email)
    if not is_maintainer and not roles.user_has_role(conn, approver_id, roles.ROLE_APPROVER):
        return None
    return row


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

    # media_proposals never reaches 'applying'/'applied' (see GitHub
    # issue #21 - approving one never dispatches anything); 'approved'
    # here means "waiting for the maintainer to actually publish it by
    # hand", so it belongs on this dashboard as its own list, not treated
    # as done the way an approved text proposal is.
    media_rows = conn.execute(
        "SELECT * FROM media_proposals WHERE status IN ('pending', 'approved', 'publishing', 'publish_failed') ORDER BY created_at"
    ).fetchall()
    media_pending = []
    media_awaiting_publish = []
    for row in media_rows:
        item = {
            "id": row["id"],
            "band_slug": row["band_slug"],
            "release_slug": row["release_slug"],
            "media_type": row["media_type"],
            "original_filename": row["original_filename"],
            "content_type": row["content_type"],
            "size_bytes": row["size_bytes"],
            "caption": row["caption"],
            "submitter_name": row["submitter_name"],
            "submitter_contact": row["submitter_contact"],
            "is_own_submission": row["submitted_by_approver_id"] == g.approver["id"],
            "status": row["status"],
            "github_run_id": row["github_run_id"] if "github_run_id" in row.keys() else None,
            "publish_error": row["publish_error"] if "publish_error" in row.keys() else None,
        }
        (media_pending if row["status"] == "pending" else media_awaiting_publish).append(item)

    return render_template(
        "dashboard.html",
        proposals=proposals,
        media_pending=media_pending,
        media_awaiting_publish=media_awaiting_publish,
        approver=g.approver,
        can_view_stats=roles.user_has_role(conn, g.approver["id"], roles.ROLE_STATS),
    )


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

    # The decision itself already succeeded and is durably recorded - a
    # dispatch failure (network blip, GitHub outage) shouldn't turn into
    # a 500 for the approver. The proposal stays 'approved' either way;
    # worst case it needs a manual re-trigger, which is not this ticket's
    # concern to automate. Narrow on purpose (matches submissions.py's
    # and auth.py's mail-sending guards): trigger_apply only ever raises
    # via `requests` (a subclass of OSError), so this catches exactly
    # network/HTTP failures, not e.g. a misconfigured GITHUB_CONFIG.
    try:
        github_dispatch.trigger_apply(current_app.config["GITHUB_CONFIG"], proposal_id)
    except OSError:
        current_app.logger.exception("failed to dispatch apply-proposal.yml for proposal %s", proposal_id)

    return redirect(url_for("dashboard.view_pending"))


@bp.post("/proposals/<int:proposal_id>/reject")
@require_approver
def reject(proposal_id: int):
    # Silent drop: no submitter notification, nothing further happens.
    ok, reason = _decide(proposal_id, "rejected")
    if not ok:
        _abort_for_reason(reason)
    return redirect(url_for("dashboard.view_pending"))


def _decide_media(proposal_id: int, new_status: str) -> tuple[bool, str]:
    """Same atomic check-and-set as _decide() above, against
    media_proposals instead of proposals - see that function's
    docstring for the full reasoning. No github_dispatch call anywhere
    in this file's media routes: approving a media proposal never
    triggers anything (GitHub issue #21's "curated queue, manual
    finish")."""
    conn = db.get_connection()
    approver_id = g.approver["id"]
    cur = conn.execute(
        """
        UPDATE media_proposals
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
        "SELECT status, submitted_by_approver_id FROM media_proposals WHERE id = ?", (proposal_id,)
    ).fetchone()
    if row is None:
        return False, "not_found"
    if row["submitted_by_approver_id"] == approver_id:
        return False, "self_approval"
    return False, "already_decided"


def _media_file_path(stored_filename: str) -> Path:
    return Path(current_app.config["MEDIA_UPLOADS_PATH"]) / stored_filename


@bp.post("/media-proposals/<int:proposal_id>/approve")
@require_approver
def approve_media(proposal_id: int):
    ok, reason = _decide_media(proposal_id, "approved")
    if not ok:
        _abort_for_reason(reason)

    conn = db.get_connection()
    row = conn.execute(
        "SELECT band_slug, release_slug, media_type, submitter_contact FROM media_proposals WHERE id = ?",
        (proposal_id,),
    ).fetchone()
    if row["submitter_contact"]:
        scope = f"{row['band_slug']}/{row['release_slug']}" if row["release_slug"] else row["band_slug"]
        description = f"your {row['media_type']} for {scope}"
        # Same stance as every other mail-sending call in this app: the
        # decision itself already succeeded and is durably recorded - a
        # failed notification (SMTP down, bad address) shouldn't turn
        # into a 500 for the approver.
        try:
            mail.send_media_approved_notification(
                current_app.config["SMTP_CONFIG"], row["submitter_contact"], description
            )
        except OSError:
            current_app.logger.exception(
                "failed to send media-approved notification for proposal %s", proposal_id
            )

    return redirect(url_for("dashboard.view_pending"))


@bp.post("/media-proposals/<int:proposal_id>/reject")
@require_approver
def reject_media(proposal_id: int):
    conn = db.get_connection()
    row = conn.execute("SELECT stored_filename FROM media_proposals WHERE id = ?", (proposal_id,)).fetchone()
    ok, reason = _decide_media(proposal_id, "rejected")
    if not ok:
        _abort_for_reason(reason)
    # Nothing else keeps this file (see GitHub issue #21's cleanup
    # rules) - review-app-data/ isn't backed up, so a rejected upload
    # shouldn't linger.
    if row is not None:
        _media_file_path(row["stored_filename"]).unlink(missing_ok=True)
    return redirect(url_for("dashboard.view_pending"))


@bp.post("/media-proposals/<int:proposal_id>/upload")
@require_approver
def upload_media(proposal_id: int):
    conn = db.get_connection()
    row = conn.execute(
        "SELECT id, status FROM media_proposals WHERE id = ?", (proposal_id,)
    ).fetchone()
    if row is None:
        abort(404)
    if row["status"] not in ("approved", "publishing", "publish_failed"):
        abort(409)

    conn.execute(
        "UPDATE media_proposals SET status = 'publishing', publish_error = NULL WHERE id = ?",
        (proposal_id,),
    )
    conn.commit()

    try:
        github_dispatch.trigger_media_apply(current_app.config["GITHUB_CONFIG"], proposal_id)
    except OSError:
        current_app.logger.exception(
            "failed to dispatch apply-media-proposal.yml for proposal %s", proposal_id
        )
        conn.execute(
            "UPDATE media_proposals SET status = 'publish_failed', publish_error = ? WHERE id = ?",
            ("Failed to dispatch upload workflow to GitHub Actions", proposal_id),
        )
        conn.commit()

    return redirect(url_for("dashboard.view_pending"))


@bp.post("/media-proposals/<int:proposal_id>/publish")
@require_approver
def publish_media(proposal_id: int):
    """The maintainer's own bookkeeping step, run after they've actually
    scp'd the file off, published it to archive.org, and committed the
    YAML entry by hand - not a curation decision, so it's not gated by
    self-approval the way approve/reject are."""
    conn = db.get_connection()
    row = conn.execute(
        "SELECT stored_filename FROM media_proposals WHERE id = ? AND status IN ('approved', 'publish_failed')",
        (proposal_id,),
    ).fetchone()
    if row is None:
        existing = conn.execute("SELECT id FROM media_proposals WHERE id = ?", (proposal_id,)).fetchone()
        abort(404 if existing is None else 409)

    cur = conn.execute(
        "UPDATE media_proposals SET status = 'published', published_at = datetime('now') "
        "WHERE id = ? AND status IN ('approved', 'publish_failed')",
        (proposal_id,),
    )
    conn.commit()
    if cur.rowcount != 1:
        abort(409)  # raced with something else between the SELECT above and here

    _media_file_path(row["stored_filename"]).unlink(missing_ok=True)
    return redirect(url_for("dashboard.view_pending"))


@bp.get("/media-proposals/<int:proposal_id>/file")
@require_approver
def media_file(proposal_id: int):
    """Login-gated preview so an approver can actually see what they're
    deciding on - distinct in purpose from the "how the maintainer
    retrieves an approved file" question (that's scp/rsync off the
    server directly, per GitHub issue #21; this route exists only so the
    dashboard can render an <img>/<video> for review, not as a general
    download path)."""
    conn = db.get_connection()
    row = conn.execute(
        "SELECT stored_filename, content_type FROM media_proposals WHERE id = ?", (proposal_id,)
    ).fetchone()
    if row is None:
        abort(404)
    path = _media_file_path(row["stored_filename"])
    if not path.exists():
        abort(404)
    return send_file(path, mimetype=row["content_type"])
