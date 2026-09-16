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
import duplicate_detection  # noqa: E402
import github_dispatch  # noqa: E402
import mail  # noqa: E402
import roles  # noqa: E402
import audio_validation  # noqa: E402

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


def is_band_publishing_throttled(conn) -> tuple[bool, str | None]:
    """Checks if a new band was published within the last 24 hours.
    Returns (True, last_published_at_str) if throttled; (False, None) if publishing is permitted.
    """
    row = conn.execute(
        "SELECT published_at FROM band_proposals WHERE status = 'published' "
        "AND datetime(published_at) >= datetime('now', '-24 hours') "
        "ORDER BY published_at DESC LIMIT 1"
    ).fetchone()
    if row is None or not row["published_at"]:
        return False, None
    return True, row["published_at"]


def is_youtube_video_publishing_throttled(conn) -> tuple[bool, int, str | None]:
    """Checks if publishing a YouTube-sourced video proposal is throttled
    (max 3 per rolling 24 hours) - see GitHub issue #49, decision 14.
    Scoped to media_proposals with source_type = 'youtube' only; direct
    file uploads are never throttled by this. Same shape as
    is_album_publishing_throttled above.
    Returns (True, count, oldest_blocking_published_at) if throttled
    (count >= 3); (False, count, None) if publishing is permitted."""
    rows = conn.execute(
        """
        SELECT published_at FROM media_proposals
        WHERE source_type = 'youtube' AND status = 'published'
          AND datetime(published_at) >= datetime('now', '-24 hours')
        ORDER BY datetime(published_at) ASC
        """
    ).fetchall()
    count = len(rows)
    if count >= 3:
        return True, count, rows[count - 3]["published_at"]
    return False, count, None


def is_album_publishing_throttled(conn) -> tuple[bool, int, str | None]:
    """Checks if album/release publishing is throttled (max 3 releases per rolling 24 hours).
    Counts releases published via album_proposals as well as releases included in band_proposals (has_release=1).
    Returns (True, count, oldest_blocking_published_at) if throttled (count >= 3);
    (False, count, None) if publishing is permitted.
    """
    rows = conn.execute(
        """
        SELECT published_at FROM (
            SELECT published_at FROM album_proposals
            WHERE status = 'published' AND datetime(published_at) >= datetime('now', '-24 hours')
            UNION ALL
            SELECT published_at FROM band_proposals
            WHERE status = 'published' AND has_release = 1 AND datetime(published_at) >= datetime('now', '-24 hours')
        )
        ORDER BY datetime(published_at) ASC
        """
    ).fetchall()
    count = len(rows)
    if count >= 3:
        return True, count, rows[count - 3]["published_at"]
    return False, count, None


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
            "ai_decision": row["ai_decision"] if "ai_decision" in row.keys() else None,
            "ai_confidence": row["ai_confidence"] if "ai_confidence" in row.keys() else None,
            "ai_reasoning": row["ai_reasoning"] if "ai_reasoning" in row.keys() else None,
            "ai_evaluated_at": row["ai_evaluated_at"] if "ai_evaluated_at" in row.keys() else None,
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
    checkout = current_app.config["ARCHIVE_CHECKOUT_PATH"]
    media_pending = []
    media_awaiting_publish = []
    for row in media_rows:
        duplicate_match = None
        if row["media_type"] == "video":
            candidate_duration = (
                row["duration_seconds"]
                if ("duration_seconds" in row.keys() and row["duration_seconds"] is not None)
                else (row["youtube_duration_seconds"] if "youtube_duration_seconds" in row.keys() else None)
            )
            duplicate_match = duplicate_detection.find_duplicate_video(
                checkout, row["band_slug"], candidate_duration
            )
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
            "ai_decision": row["ai_decision"] if "ai_decision" in row.keys() else None,
            "ai_confidence": row["ai_confidence"] if "ai_confidence" in row.keys() else None,
            "ai_reasoning": row["ai_reasoning"] if "ai_reasoning" in row.keys() else None,
            "ai_evaluated_at": row["ai_evaluated_at"] if "ai_evaluated_at" in row.keys() else None,
            "source_type": row["source_type"] if "source_type" in row.keys() else "upload",
            "source_url": row["source_url"] if "source_url" in row.keys() else None,
            "youtube_channel": row["youtube_channel"] if "youtube_channel" in row.keys() else None,
            "youtube_duration_seconds": (
                row["youtube_duration_seconds"] if "youtube_duration_seconds" in row.keys() else None
            ),
            "duration_seconds": (
                row["duration_seconds"]
                if ("duration_seconds" in row.keys() and row["duration_seconds"] is not None)
                else (row["youtube_duration_seconds"] if "youtube_duration_seconds" in row.keys() else None)
            ),
            "duplicate_match": duplicate_match,
        }
        (media_pending if row["status"] == "pending" else media_awaiting_publish).append(item)

    album_rows = conn.execute(
        "SELECT * FROM album_proposals WHERE status IN ('pending', 'approved', 'publishing', 'publish_failed') ORDER BY created_at"
    ).fetchall()
    album_pending = []
    album_awaiting_publish = []
    for row in album_rows:
        item = {
            "id": row["id"],
            "band_slug": row["band_slug"],
            "release_slug": row["release_slug"],
            "name": row["name"],
            "date_published": row["date_published"],
            "genre": json.loads(row["genre"]) if row["genre"] else [],
            "license": row["license"],
            "description": row["description"],
            "description_en": row["description_en"],
            "cover_stored_filename": row["cover_stored_filename"],
            "tracks": json.loads(row["tracks_json"]) if row["tracks_json"] else [],
            "submitter_name": row["submitter_name"],
            "submitter_contact": row["submitter_contact"],
            "is_own_submission": row["submitted_by_approver_id"] == g.approver["id"],
            "status": row["status"],
            "github_run_id": row["github_run_id"] if "github_run_id" in row.keys() else None,
            "publish_error": row["publish_error"] if "publish_error" in row.keys() else None,
            "ai_decision": row["ai_decision"] if "ai_decision" in row.keys() else None,
            "ai_confidence": row["ai_confidence"] if "ai_confidence" in row.keys() else None,
            "ai_reasoning": row["ai_reasoning"] if "ai_reasoning" in row.keys() else None,
            "ai_evaluated_at": row["ai_evaluated_at"] if "ai_evaluated_at" in row.keys() else None,
        }
        (album_pending if row["status"] == "pending" else album_awaiting_publish).append(item)

    band_rows = conn.execute(
        "SELECT * FROM band_proposals WHERE status IN ('pending', 'approved', 'publishing', 'publish_failed') ORDER BY created_at"
    ).fetchall()
    band_pending = []
    band_awaiting_publish = []
    for row in band_rows:
        item = {
            "id": row["id"],
            "name": row["name"],
            "band_slug": row["band_slug"],
            "founding_date": row["founding_date"],
            "dissolution_date": row["dissolution_date"],
            "location": row["location"],
            "genre": json.loads(row["genre"]) if row["genre"] else [],
            "description": row["description"],
            "description_en": row["description_en"],
            "band_photo_stored_filename": row["band_photo_stored_filename"],
            "has_release": bool(row["has_release"]),
            "release_name": row["release_name"],
            "release_slug": row["release_slug"],
            "release_date_published": row["release_date_published"],
            "release_genre": json.loads(row["release_genre"]) if row["release_genre"] else [],
            "release_license": row["release_license"],
            "release_description": row["release_description"],
            "release_description_en": row["release_description_en"],
            "release_cover_stored_filename": row["release_cover_stored_filename"],
            "tracks": json.loads(row["release_tracks_json"]) if row["release_tracks_json"] else [],
            "submitter_name": row["submitter_name"],
            "submitter_contact": row["submitter_contact"],
            "is_own_submission": row["submitted_by_approver_id"] == g.approver["id"],
            "status": row["status"],
            "github_run_id": row["github_run_id"] if "github_run_id" in row.keys() else None,
            "publish_error": row["publish_error"] if "publish_error" in row.keys() else None,
            "ai_decision": row["ai_decision"] if "ai_decision" in row.keys() else None,
            "ai_confidence": row["ai_confidence"] if "ai_confidence" in row.keys() else None,
            "ai_reasoning": row["ai_reasoning"] if "ai_reasoning" in row.keys() else None,
            "ai_evaluated_at": row["ai_evaluated_at"] if "ai_evaluated_at" in row.keys() else None,
        }
        (band_pending if row["status"] == "pending" else band_awaiting_publish).append(item)

    is_band_throttled, last_band_published_at = is_band_publishing_throttled(conn)
    is_album_throttled, album_published_count, last_album_published_at = is_album_publishing_throttled(conn)
    is_youtube_video_throttled, _, _ = is_youtube_video_publishing_throttled(conn)

    return render_template(
        "dashboard.html",
        proposals=proposals,
        media_pending=media_pending,
        media_awaiting_publish=media_awaiting_publish,
        album_pending=album_pending,
        album_awaiting_publish=album_awaiting_publish,
        band_pending=band_pending,
        band_awaiting_publish=band_awaiting_publish,
        is_band_throttled=is_band_throttled,
        last_band_published_at=last_band_published_at,
        is_album_throttled=is_album_throttled,
        album_published_count=album_published_count,
        last_album_published_at=last_album_published_at,
        is_youtube_video_throttled=is_youtube_video_throttled,
        approver=g.approver,
        can_view_stats=roles.user_has_role(conn, g.approver["id"], roles.ROLE_STATS),
    )


def _decide(proposal_id: int, new_status: str, review_notes: str | None = None) -> tuple[bool, str]:
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
        SET status = ?, decided_by = ?, decided_at = datetime('now'), review_notes = ?
        WHERE id = ? AND status = 'pending'
          AND (submitted_by_approver_id IS NULL OR submitted_by_approver_id != ?)
        """,
        (new_status, approver_id, review_notes, proposal_id, approver_id),
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
    if reason.startswith("invalid_"):
        abort(400)
    abort(409)  # already decided by someone else or collision


@bp.post("/proposals/<int:proposal_id>/approve")
@require_approver
def approve(proposal_id: int):
    conn = db.get_connection()
    row = conn.execute(
        "SELECT band_slug, release_slug, target, field, submitter_contact, lang FROM proposals WHERE id = ?",
        (proposal_id,),
    ).fetchone()
    ok, reason = _decide(proposal_id, "approved")
    if not ok:
        _abort_for_reason(reason)

    if row and row["submitter_contact"]:
        scope = f"{row['band_slug']}/{row['release_slug']}" if row["release_slug"] else row["band_slug"]
        target_summary = f"{scope} ({row['target']}.{row['field']})"
        lang = row["lang"] if "lang" in row.keys() and row["lang"] else "ru"
        live_prefix = "https://daugavpils.fans/en" if lang == "en" else "https://daugavpils.fans"
        live_path = f"/bands/{row['band_slug']}/{row['release_slug']}/" if row["release_slug"] else f"/bands/{row['band_slug']}/"
        live_url = f"{live_prefix}{live_path}"
        try:
            mail.send_proposal_decision_notification(
                current_app.config["SMTP_CONFIG"],
                row["submitter_contact"],
                decision="approved",
                proposal_type="edit",
                target_summary=target_summary,
                live_url=live_url,
                lang=lang,
            )
        except OSError:
            current_app.logger.exception(
                "failed to send approval notification for proposal %s", proposal_id
            )

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
    review_notes = request.form.get("review_notes", "").strip() or request.form.get("reason", "").strip() or None
    conn = db.get_connection()
    row = conn.execute(
        "SELECT band_slug, release_slug, target, field, submitter_contact, lang FROM proposals WHERE id = ?",
        (proposal_id,),
    ).fetchone()
    ok, reason = _decide(proposal_id, "rejected", review_notes=review_notes)
    if not ok:
        _abort_for_reason(reason)

    if row and row["submitter_contact"]:
        scope = f"{row['band_slug']}/{row['release_slug']}" if row["release_slug"] else row["band_slug"]
        target_summary = f"{scope} ({row['target']}.{row['field']})"
        lang = row["lang"] if "lang" in row.keys() and row["lang"] else "ru"
        try:
            mail.send_proposal_decision_notification(
                current_app.config["SMTP_CONFIG"],
                row["submitter_contact"],
                decision="rejected",
                proposal_type="edit",
                target_summary=target_summary,
                review_notes=review_notes,
                lang=lang,
            )
        except OSError:
            current_app.logger.exception(
                "failed to send rejection notification for proposal %s", proposal_id
            )

    return redirect(url_for("dashboard.view_pending"))


def _decide_media(proposal_id: int, new_status: str, review_notes: str | None = None) -> tuple[bool, str]:
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
        SET status = ?, decided_by = ?, decided_at = datetime('now'), review_notes = ?
        WHERE id = ? AND status = 'pending'
          AND (submitted_by_approver_id IS NULL OR submitted_by_approver_id != ?)
        """,
        (new_status, approver_id, review_notes, proposal_id, approver_id),
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
    review_notes = request.form.get("review_notes", "").strip() or request.form.get("reason", "").strip() or None
    conn = db.get_connection()
    row = conn.execute(
        "SELECT stored_filename, band_slug, release_slug, media_type, submitter_contact, lang FROM media_proposals WHERE id = ?",
        (proposal_id,),
    ).fetchone()
    ok, reason = _decide_media(proposal_id, "rejected", review_notes=review_notes)
    if not ok:
        _abort_for_reason(reason)
    # Nothing else keeps this file (see GitHub issue #21's cleanup
    # rules) - review-app-data/ isn't backed up, so a rejected upload
    # shouldn't linger.
    if row is not None:
        _media_file_path(row["stored_filename"]).unlink(missing_ok=True)
        if row["submitter_contact"]:
            scope = f"{row['band_slug']}/{row['release_slug']}" if row["release_slug"] else row["band_slug"]
            target_summary = f"{scope} ({row['media_type']})"
            lang = row["lang"] if "lang" in row.keys() and row["lang"] else "ru"
            try:
                mail.send_proposal_decision_notification(
                    current_app.config["SMTP_CONFIG"],
                    row["submitter_contact"],
                    decision="rejected",
                    proposal_type="media",
                    target_summary=target_summary,
                    review_notes=review_notes,
                    lang=lang,
                )
            except OSError:
                current_app.logger.exception(
                    "failed to send rejection notification for media proposal %s", proposal_id
                )

    return redirect(url_for("dashboard.view_pending"))


@bp.post("/media-proposals/<int:proposal_id>/upload")
@require_approver
def upload_media(proposal_id: int):
    conn = db.get_connection()
    row = conn.execute(
        "SELECT id, status, source_type FROM media_proposals WHERE id = ?", (proposal_id,)
    ).fetchone()
    if row is None:
        abort(404)
    if row["status"] not in ("approved", "publishing", "publish_failed"):
        abort(409)

    if row["source_type"] == "youtube" and is_youtube_video_publishing_throttled(conn)[0]:
        abort(429)

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
        "SELECT stored_filename, band_slug, release_slug, media_type, submitter_contact, lang FROM media_proposals WHERE id = ? AND status IN ('approved', 'publish_failed')",
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

    if row and row["submitter_contact"]:
        scope = f"{row['band_slug']}/{row['release_slug']}" if row["release_slug"] else row["band_slug"]
        target_summary = f"{scope} ({row['media_type']})"
        lang = row["lang"] if "lang" in row.keys() and row["lang"] else "ru"
        live_prefix = "https://daugavpils.fans/en" if lang == "en" else "https://daugavpils.fans"
        live_path = f"/bands/{row['band_slug']}/{row['release_slug']}/" if row["release_slug"] else f"/bands/{row['band_slug']}/"
        live_url = f"{live_prefix}{live_path}"
        try:
            mail.send_proposal_decision_notification(
                current_app.config["SMTP_CONFIG"],
                row["submitter_contact"],
                decision="approved",
                proposal_type="media",
                target_summary=target_summary,
                live_url=live_url,
                lang=lang,
            )
        except OSError:
            current_app.logger.exception(
                "failed to send media published notification for proposal %s", proposal_id
            )

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


def _decide_album(proposal_id: int, new_status: str, review_notes: str | None = None) -> tuple[bool, str]:
    conn = db.get_connection()
    approver_id = g.approver["id"]
    cur = conn.execute(
        """
        UPDATE album_proposals
        SET status = ?, decided_by = ?, decided_at = datetime('now'), review_notes = ?
        WHERE id = ? AND status = 'pending'
          AND (submitted_by_approver_id IS NULL OR submitted_by_approver_id != ?)
        """,
        (new_status, approver_id, review_notes, proposal_id, approver_id),
    )
    conn.commit()
    if cur.rowcount == 1:
        return True, ""

    row = conn.execute(
        "SELECT status, submitted_by_approver_id FROM album_proposals WHERE id = ?", (proposal_id,)
    ).fetchone()
    if row is None:
        return False, "not_found"
    if row["submitted_by_approver_id"] == approver_id:
        return False, "self_approval"
    return False, "already_decided"


@bp.post("/album-proposals/<int:proposal_id>/approve")
@require_approver
def approve_album(proposal_id: int):
    conn = db.get_connection()
    row = conn.execute(
        "SELECT band_slug, release_slug, name, submitter_contact, lang FROM album_proposals WHERE id = ?",
        (proposal_id,),
    ).fetchone()
    ok, reason = _decide_album(proposal_id, "approved")
    if not ok:
        _abort_for_reason(reason)

    if row and row["submitter_contact"]:
        target_summary = f"{row['band_slug']} — {row['name']}"
        lang = row["lang"] if "lang" in row.keys() and row["lang"] else "ru"
        live_prefix = "https://daugavpils.fans/en" if lang == "en" else "https://daugavpils.fans"
        live_url = f"{live_prefix}/bands/{row['band_slug']}/{row['release_slug']}/"
        try:
            mail.send_proposal_decision_notification(
                current_app.config["SMTP_CONFIG"],
                row["submitter_contact"],
                decision="approved",
                proposal_type="album",
                target_summary=target_summary,
                live_url=live_url,
                lang=lang,
            )
        except OSError:
            current_app.logger.exception(
                "failed to send approval notification for album proposal %s", proposal_id
            )

    return redirect(url_for("dashboard.view_pending"))


@bp.post("/album-proposals/<int:proposal_id>/reject")
@require_approver
def reject_album(proposal_id: int):
    review_notes = request.form.get("review_notes", "").strip() or request.form.get("reason", "").strip() or None
    conn = db.get_connection()
    row = conn.execute(
        "SELECT cover_stored_filename, tracks_json, band_slug, release_slug, name, submitter_contact, lang FROM album_proposals WHERE id = ?",
        (proposal_id,),
    ).fetchone()
    ok, reason = _decide_album(proposal_id, "rejected", review_notes=review_notes)
    if not ok:
        _abort_for_reason(reason)

    if row is not None:
        if row["cover_stored_filename"]:
            _media_file_path(row["cover_stored_filename"]).unlink(missing_ok=True)
        if row["tracks_json"]:
            tracks = json.loads(row["tracks_json"])
            for t in tracks:
                if "stored_filename" in t:
                    _media_file_path(t["stored_filename"]).unlink(missing_ok=True)

        if row["submitter_contact"]:
            target_summary = f"{row['band_slug']} — {row['name']}"
            lang = row["lang"] if "lang" in row.keys() and row["lang"] else "ru"
            try:
                mail.send_proposal_decision_notification(
                    current_app.config["SMTP_CONFIG"],
                    row["submitter_contact"],
                    decision="rejected",
                    proposal_type="album",
                    target_summary=target_summary,
                    review_notes=review_notes,
                    lang=lang,
                )
            except OSError:
                current_app.logger.exception(
                    "failed to send rejection notification for album proposal %s", proposal_id
                )

    return redirect(url_for("dashboard.view_pending"))


@bp.post("/album-proposals/<int:proposal_id>/upload")
@require_approver
def upload_album(proposal_id: int):
    conn = db.get_connection()
    row = conn.execute(
        "SELECT id, status FROM album_proposals WHERE id = ?", (proposal_id,)
    ).fetchone()
    if row is None:
        abort(404)
    if row["status"] not in ("approved", "publishing", "publish_failed"):
        abort(409)

    is_throttled, _, _ = is_album_publishing_throttled(conn)
    if is_throttled:
        abort(429)

    conn.execute(
        "UPDATE album_proposals SET status = 'publishing', publish_error = NULL WHERE id = ?",
        (proposal_id,),
    )
    conn.commit()

    try:
        github_dispatch.trigger_album_apply(current_app.config["GITHUB_CONFIG"], proposal_id)
    except OSError:
        current_app.logger.exception(
            "failed to dispatch apply-album-proposal.yml for proposal %s", proposal_id
        )
        conn.execute(
            "UPDATE album_proposals SET status = 'publish_failed', publish_error = ? WHERE id = ?",
            ("Failed to dispatch upload workflow to GitHub Actions", proposal_id),
        )
        conn.commit()

    return redirect(url_for("dashboard.view_pending"))


@bp.post("/album-proposals/<int:proposal_id>/publish")
@require_approver
def publish_album(proposal_id: int):
    conn = db.get_connection()
    row = conn.execute(
        "SELECT cover_stored_filename, tracks_json FROM album_proposals WHERE id = ? AND status IN ('approved', 'publish_failed')",
        (proposal_id,),
    ).fetchone()
    if row is None:
        existing = conn.execute("SELECT id FROM album_proposals WHERE id = ?", (proposal_id,)).fetchone()
        abort(404 if existing is None else 409)

    cur = conn.execute(
        "UPDATE album_proposals SET status = 'published', published_at = datetime('now') "
        "WHERE id = ? AND status IN ('approved', 'publish_failed')",
        (proposal_id,),
    )
    conn.commit()
    if cur.rowcount != 1:
        abort(409)

    if row["cover_stored_filename"]:
        _media_file_path(row["cover_stored_filename"]).unlink(missing_ok=True)
    if row["tracks_json"]:
        tracks = json.loads(row["tracks_json"])
        for t in tracks:
            if "stored_filename" in t:
                _media_file_path(t["stored_filename"]).unlink(missing_ok=True)

    return redirect(url_for("dashboard.view_pending"))


@bp.get("/album-proposals/<int:proposal_id>/cover")
@require_approver
def album_cover_file(proposal_id: int):
    conn = db.get_connection()
    row = conn.execute(
        "SELECT cover_stored_filename FROM album_proposals WHERE id = ?", (proposal_id,)
    ).fetchone()
    if row is None or not row["cover_stored_filename"]:
        abort(404)
    path = _media_file_path(row["cover_stored_filename"])
    if not path.exists():
        abort(404)
    return send_file(path)


AUDIO_MIMETYPES = {
    ".mp3": "audio/mpeg",
    ".flac": "audio/flac",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
    ".aac": "audio/aac",
}


def _resolve_audio_mimetype(content_type: str | None, path: Path) -> str:
    if content_type and content_type.startswith("audio/"):
        return content_type
    return AUDIO_MIMETYPES.get(path.suffix.lower(), "audio/mpeg")


@bp.get("/dashboard/proposals/album/<int:proposal_id>/track/<int:position>/audio")
@bp.get("/album-proposals/<int:proposal_id>/tracks/<int:position>/file")
@require_approver
def album_track_audio_file(proposal_id: int, position: int):
    conn = db.get_connection()
    row = conn.execute(
        "SELECT status, tracks_json FROM album_proposals WHERE id = ?", (proposal_id,)
    ).fetchone()
    if row is None or not row["tracks_json"]:
        abort(404)
    if row["status"] in ("published", "applied", "rejected", "purged"):
        abort(404)
    tracks = json.loads(row["tracks_json"])
    track = next((t for t in tracks if t.get("position") == position), None)
    if track is None or not track.get("stored_filename"):
        abort(404)
    path = _media_file_path(track["stored_filename"])
    if not path.exists():
        abort(404)
    mimetype = _resolve_audio_mimetype(track.get("content_type"), path)
    return send_file(
        path,
        mimetype=mimetype,
        conditional=True,
        as_attachment=False,
        download_name=track.get("original_filename") or path.name,
    )


def _decide_band(
    proposal_id: int,
    new_status: str,
    new_band_slug: str | None = None,
    new_release_slug: str | None = None,
    review_notes: str | None = None,
) -> tuple[bool, str]:
    conn = db.get_connection()
    approver_id = g.approver["id"]

    if new_band_slug:
        if not audio_validation.BAND_SLUG_RE.match(new_band_slug):
            return False, "invalid_band_slug"
        checkout = current_app.config["ARCHIVE_CHECKOUT_PATH"]
        if (Path(checkout) / "bands" / new_band_slug / "band.yaml").exists():
            return False, "collision_band_slug"
        collision = conn.execute(
            "SELECT id FROM band_proposals WHERE band_slug = ? AND id != ? AND status IN ('pending', 'approved', 'publishing')",
            (new_band_slug, proposal_id),
        ).fetchone()
        if collision:
            return False, "collision_band_slug"

    if new_release_slug:
        if not audio_validation.SLUG_RE.match(new_release_slug):
            return False, "invalid_release_slug"

    updates = ["status = ?", "decided_by = ?", "decided_at = datetime('now')", "review_notes = ?"]
    params: list[Any] = [new_status, approver_id, review_notes]
    if new_band_slug:
        updates.append("band_slug = ?")
        params.append(new_band_slug)
    if new_release_slug:
        updates.append("release_slug = ?")
        params.append(new_release_slug)

    params.extend([proposal_id, approver_id])
    cur = conn.execute(
        f"""
        UPDATE band_proposals
        SET {', '.join(updates)}
        WHERE id = ? AND status = 'pending'
          AND (submitted_by_approver_id IS NULL OR submitted_by_approver_id != ?)
        """,
        tuple(params),
    )
    conn.commit()
    if cur.rowcount == 1:
        return True, ""

    row = conn.execute(
        "SELECT status, submitted_by_approver_id FROM band_proposals WHERE id = ?", (proposal_id,)
    ).fetchone()
    if row is None:
        return False, "not_found"
    if row["submitted_by_approver_id"] == approver_id:
        return False, "self_approval"
    return False, "already_decided"


@bp.post("/band-proposals/<int:proposal_id>/approve")
@require_approver
def approve_band(proposal_id: int):
    new_band_slug = request.form.get("band_slug", "").strip() or None
    new_release_slug = request.form.get("release_slug", "").strip() or None

    conn = db.get_connection()
    row = conn.execute(
        "SELECT name, band_slug, release_slug, submitter_contact, lang FROM band_proposals WHERE id = ?",
        (proposal_id,),
    ).fetchone()

    ok, reason = _decide_band(
        proposal_id, "approved", new_band_slug=new_band_slug, new_release_slug=new_release_slug
    )
    if not ok:
        _abort_for_reason(reason)

    if row and row["submitter_contact"]:
        target_band_slug = new_band_slug or row["band_slug"]
        target_summary = row["name"]
        lang = row["lang"] if "lang" in row.keys() and row["lang"] else "ru"
        live_prefix = "https://daugavpils.fans/en" if lang == "en" else "https://daugavpils.fans"
        live_url = f"{live_prefix}/bands/{target_band_slug}/"
        try:
            mail.send_proposal_decision_notification(
                current_app.config["SMTP_CONFIG"],
                row["submitter_contact"],
                decision="approved",
                proposal_type="band",
                target_summary=target_summary,
                live_url=live_url,
                lang=lang,
            )
        except OSError:
            current_app.logger.exception(
                "failed to send approval notification for band proposal %s", proposal_id
            )

    is_throttled, _ = is_band_publishing_throttled(conn)
    if not is_throttled:
        conn.execute(
            "UPDATE band_proposals SET status = 'publishing', publish_error = NULL WHERE id = ?",
            (proposal_id,),
        )
        conn.commit()
        try:
            github_dispatch.trigger_band_apply(current_app.config["GITHUB_CONFIG"], proposal_id)
        except OSError:
            current_app.logger.exception(
                "failed to dispatch apply-band-proposal.yml for proposal %s", proposal_id
            )
            conn.execute(
                "UPDATE band_proposals SET status = 'publish_failed', publish_error = ? WHERE id = ?",
                ("Failed to dispatch upload workflow to GitHub Actions", proposal_id),
            )
            conn.commit()

    return redirect(url_for("dashboard.view_pending"))


@bp.post("/band-proposals/<int:proposal_id>/reject")
@require_approver
def reject_band(proposal_id: int):
    review_notes = request.form.get("review_notes", "").strip() or request.form.get("reason", "").strip() or None
    conn = db.get_connection()
    row = conn.execute(
        "SELECT band_photo_stored_filename, release_cover_stored_filename, release_tracks_json, name, band_slug, submitter_contact, lang FROM band_proposals WHERE id = ?",
        (proposal_id,),
    ).fetchone()
    ok, reason = _decide_band(proposal_id, "rejected", review_notes=review_notes)
    if not ok:
        _abort_for_reason(reason)

    if row is not None:
        if row["band_photo_stored_filename"]:
            _media_file_path(row["band_photo_stored_filename"]).unlink(missing_ok=True)
        if row["release_cover_stored_filename"]:
            _media_file_path(row["release_cover_stored_filename"]).unlink(missing_ok=True)
        if row["release_tracks_json"]:
            tracks = json.loads(row["release_tracks_json"])
            for t in tracks:
                if "stored_filename" in t:
                    _media_file_path(t["stored_filename"]).unlink(missing_ok=True)

        if row["submitter_contact"]:
            target_summary = row["name"]
            lang = row["lang"] if "lang" in row.keys() and row["lang"] else "ru"
            try:
                mail.send_proposal_decision_notification(
                    current_app.config["SMTP_CONFIG"],
                    row["submitter_contact"],
                    decision="rejected",
                    proposal_type="band",
                    target_summary=target_summary,
                    review_notes=review_notes,
                    lang=lang,
                )
            except OSError:
                current_app.logger.exception(
                    "failed to send rejection notification for band proposal %s", proposal_id
                )

    return redirect(url_for("dashboard.view_pending"))


@bp.post("/band-proposals/<int:proposal_id>/upload")
@require_approver
def upload_band(proposal_id: int):
    conn = db.get_connection()
    row = conn.execute(
        "SELECT id, status FROM band_proposals WHERE id = ?", (proposal_id,)
    ).fetchone()
    if row is None:
        abort(404)
    if row["status"] not in ("approved", "publishing", "publish_failed"):
        abort(409)

    is_throttled, _ = is_band_publishing_throttled(conn)
    if is_throttled:
        abort(429)

    conn.execute(
        "UPDATE band_proposals SET status = 'publishing', publish_error = NULL WHERE id = ?",
        (proposal_id,),
    )
    conn.commit()

    try:
        github_dispatch.trigger_band_apply(current_app.config["GITHUB_CONFIG"], proposal_id)
    except OSError:
        current_app.logger.exception(
            "failed to dispatch apply-band-proposal.yml for proposal %s", proposal_id
        )
        conn.execute(
            "UPDATE band_proposals SET status = 'publish_failed', publish_error = ? WHERE id = ?",
            ("Failed to dispatch upload workflow to GitHub Actions", proposal_id),
        )
        conn.commit()

    return redirect(url_for("dashboard.view_pending"))


@bp.post("/band-proposals/<int:proposal_id>/publish")
@require_approver
def publish_band(proposal_id: int):
    conn = db.get_connection()
    row = conn.execute(
        "SELECT band_photo_stored_filename, release_cover_stored_filename, release_tracks_json FROM band_proposals WHERE id = ? AND status IN ('approved', 'publish_failed')",
        (proposal_id,),
    ).fetchone()
    if row is None:
        existing = conn.execute("SELECT id FROM band_proposals WHERE id = ?", (proposal_id,)).fetchone()
        abort(404 if existing is None else 409)

    cur = conn.execute(
        "UPDATE band_proposals SET status = 'published', published_at = datetime('now') "
        "WHERE id = ? AND status IN ('approved', 'publish_failed')",
        (proposal_id,),
    )
    conn.commit()
    if cur.rowcount != 1:
        abort(409)

    if row["band_photo_stored_filename"]:
        _media_file_path(row["band_photo_stored_filename"]).unlink(missing_ok=True)
    if row["release_cover_stored_filename"]:
        _media_file_path(row["release_cover_stored_filename"]).unlink(missing_ok=True)
    if row["release_tracks_json"]:
        tracks = json.loads(row["release_tracks_json"])
        for t in tracks:
            if "stored_filename" in t:
                _media_file_path(t["stored_filename"]).unlink(missing_ok=True)

    return redirect(url_for("dashboard.view_pending"))


@bp.get("/band-proposals/<int:proposal_id>/photo")
@require_approver
def band_photo_file(proposal_id: int):
    conn = db.get_connection()
    row = conn.execute(
        "SELECT band_photo_stored_filename FROM band_proposals WHERE id = ?", (proposal_id,)
    ).fetchone()
    if row is None or not row["band_photo_stored_filename"]:
        abort(404)
    path = _media_file_path(row["band_photo_stored_filename"])
    if not path.exists():
        abort(404)
    return send_file(path)


@bp.get("/band-proposals/<int:proposal_id>/cover")
@require_approver
def band_release_cover_file(proposal_id: int):
    conn = db.get_connection()
    row = conn.execute(
        "SELECT release_cover_stored_filename FROM band_proposals WHERE id = ?", (proposal_id,)
    ).fetchone()
    if row is None or not row["release_cover_stored_filename"]:
        abort(404)
    path = _media_file_path(row["release_cover_stored_filename"])
    if not path.exists():
        abort(404)
    return send_file(path)


@bp.get("/dashboard/proposals/band/<int:proposal_id>/track/<int:position>/audio")
@bp.get("/band-proposals/<int:proposal_id>/tracks/<int:position>/file")
@require_approver
def band_release_track_audio_file(proposal_id: int, position: int):
    conn = db.get_connection()
    row = conn.execute(
        "SELECT status, release_tracks_json FROM band_proposals WHERE id = ?", (proposal_id,)
    ).fetchone()
    if row is None or not row["release_tracks_json"]:
        abort(404)
    if row["status"] in ("published", "applied", "rejected", "purged"):
        abort(404)
    tracks = json.loads(row["release_tracks_json"])
    track = next((t for t in tracks if t.get("position") == position), None)
    if track is None or not track.get("stored_filename"):
        abort(404)
    path = _media_file_path(track["stored_filename"])
    if not path.exists():
        abort(404)
    mimetype = _resolve_audio_mimetype(track.get("content_type"), path)
    return send_file(
        path,
        mimetype=mimetype,
        conditional=True,
        as_attachment=False,
        download_name=track.get("original_filename") or path.name,
    )


@bp.get("/dashboard/history")
@require_approver
def decision_history():
    conn = db.get_connection()
    db.prune_old_decided_proposals(conn, retention_days=90)

    raw_days = request.args.get("days", "7")
    try:
        days = int(raw_days)
        if days not in (7, 14, 30, 90):
            days = 7
    except ValueError:
        days = 7

    selected_status = request.args.get("status", "all").strip().lower()
    if selected_status not in ("all", "approved", "rejected"):
        selected_status = "all"

    selected_type = request.args.get("type", "all").strip().lower()
    if selected_type not in ("all", "edit", "media", "album", "band"):
        selected_type = "all"

    timeframe_filter = f"-{days} days"
    items: list[dict] = []

    # 1. Field edits (proposals)
    if selected_type in ("all", "edit"):
        status_clause = ""
        params: list[str] = [timeframe_filter]
        if selected_status == "approved":
            status_clause = "AND p.status IN ('applied', 'approved')"
        elif selected_status == "rejected":
            status_clause = "AND p.status = 'rejected'"
        else:
            status_clause = "AND p.status IN ('applied', 'approved', 'rejected')"

        query = f"""
            SELECT p.*, a.display_name AS decider_name, a.email AS decider_email
            FROM proposals p
            LEFT JOIN approvers a ON p.decided_by = a.id
            WHERE p.decided_at IS NOT NULL
              AND datetime(p.decided_at) >= datetime('now', ?)
              {status_clause}
            ORDER BY p.decided_at DESC
        """
        for r in conn.execute(query, params).fetchall():
            target_scope = f"{r['band_slug']}/{r['release_slug']}" if r["release_slug"] else r["band_slug"]
            norm_status = "approved" if r["status"] in ("applied", "approved") else "rejected"
            field_name = "new band member" if r["target"] == "new_member" else f"{r['target']}.{r['field']}"
            if r["list_index"] is not None:
                field_name += f"[{r['list_index']}]"

            orig_val = r["original_value"]
            prop_val = r["proposed_value"]
            try:
                orig_json = json.loads(orig_val)
                if isinstance(orig_json, (list, dict)):
                    orig_val = json.dumps(orig_json, ensure_ascii=False, indent=2)
            except Exception:
                pass
            try:
                prop_json = json.loads(prop_val)
                if isinstance(prop_json, (list, dict)):
                    prop_val = json.dumps(prop_json, ensure_ascii=False, indent=2)
            except Exception:
                pass

            items.append({
                "id": r["id"],
                "type": "edit",
                "type_label": "Field Edit",
                "target_title": f"{target_scope} — {field_name}",
                "status": norm_status,
                "raw_status": r["status"],
                "created_at": r["created_at"],
                "decided_at": r["decided_at"],
                "decider_name": r["decider_name"] or ("AI Approval Agent" if (r["decider_email"] == roles.AI_APPROVER_EMAIL or not r["decided_by"]) else "Approver"),
                "decider_email": r["decider_email"],
                "is_ai": bool(r["decider_email"] == roles.AI_APPROVER_EMAIL or r["decider_name"] == roles.AI_APPROVER_NAME),
                "submitter_name": r["submitter_name"],
                "submitter_contact": r["submitter_contact"],
                "ai_decision": r["ai_decision"],
                "ai_confidence": r["ai_confidence"],
                "ai_reasoning": r["ai_reasoning"],
                "review_notes": r["review_notes"] if "review_notes" in r.keys() else None,
                "details": {
                    "field": field_name,
                    "original_value": orig_val,
                    "proposed_value": prop_val,
                    "applied_at": r["applied_at"],
                    "github_run_id": r["github_run_id"],
                },
            })

    # 2. Media proposals
    if selected_type in ("all", "media"):
        status_clause = ""
        params = [timeframe_filter]
        if selected_status == "approved":
            status_clause = "AND m.status IN ('published', 'approved')"
        elif selected_status == "rejected":
            status_clause = "AND m.status = 'rejected'"
        else:
            status_clause = "AND m.status IN ('published', 'approved', 'rejected')"

        query = f"""
            SELECT m.*, a.display_name AS decider_name, a.email AS decider_email
            FROM media_proposals m
            LEFT JOIN approvers a ON m.decided_by = a.id
            WHERE m.decided_at IS NOT NULL
              AND datetime(m.decided_at) >= datetime('now', ?)
              {status_clause}
            ORDER BY m.decided_at DESC
        """
        for r in conn.execute(query, params).fetchall():
            target_scope = f"{r['band_slug']}/{r['release_slug']}" if r["release_slug"] else r["band_slug"]
            norm_status = "approved" if r["status"] in ("published", "approved") else "rejected"
            items.append({
                "id": r["id"],
                "type": "media",
                "type_label": f"Media ({r['media_type'].capitalize()})",
                "target_title": f"{target_scope} — {r['media_type']} ({r['original_filename']})",
                "status": norm_status,
                "raw_status": r["status"],
                "created_at": r["created_at"],
                "decided_at": r["decided_at"],
                "decider_name": r["decider_name"] or ("AI Approval Agent" if (r["decider_email"] == roles.AI_APPROVER_EMAIL or not r["decided_by"]) else "Approver"),
                "decider_email": r["decider_email"],
                "is_ai": bool(r["decider_email"] == roles.AI_APPROVER_EMAIL or r["decider_name"] == roles.AI_APPROVER_NAME),
                "submitter_name": r["submitter_name"],
                "submitter_contact": r["submitter_contact"],
                "ai_decision": r["ai_decision"],
                "ai_confidence": r["ai_confidence"],
                "ai_reasoning": r["ai_reasoning"],
                "review_notes": r["review_notes"] if "review_notes" in r.keys() else None,
                "details": {
                    "media_type": r["media_type"],
                    "caption": r["caption"],
                    "original_filename": r["original_filename"],
                    "size_bytes": r["size_bytes"],
                    "published_at": r["published_at"],
                    "github_run_id": r["github_run_id"],
                },
            })

    # 3. Album proposals
    if selected_type in ("all", "album"):
        status_clause = ""
        params = [timeframe_filter]
        if selected_status == "approved":
            status_clause = "AND ap.status IN ('published', 'approved')"
        elif selected_status == "rejected":
            status_clause = "AND ap.status = 'rejected'"
        else:
            status_clause = "AND ap.status IN ('published', 'approved', 'rejected')"

        query = f"""
            SELECT ap.*, a.display_name AS decider_name, a.email AS decider_email
            FROM album_proposals ap
            LEFT JOIN approvers a ON ap.decided_by = a.id
            WHERE ap.decided_at IS NOT NULL
              AND datetime(ap.decided_at) >= datetime('now', ?)
              {status_clause}
            ORDER BY ap.decided_at DESC
        """
        for r in conn.execute(query, params).fetchall():
            norm_status = "approved" if r["status"] in ("published", "approved") else "rejected"
            try:
                tracks = json.loads(r["tracks_json"]) if r["tracks_json"] else []
            except Exception:
                tracks = []
            try:
                genres = json.loads(r["genre"]) if r["genre"] else []
            except Exception:
                genres = []
            items.append({
                "id": r["id"],
                "type": "album",
                "type_label": "New Album",
                "target_title": f"{r['band_slug']} — {r['name']} ({r['date_published']})",
                "status": norm_status,
                "raw_status": r["status"],
                "created_at": r["created_at"],
                "decided_at": r["decided_at"],
                "decider_name": r["decider_name"] or ("AI Approval Agent" if (r["decider_email"] == roles.AI_APPROVER_EMAIL or not r["decided_by"]) else "Approver"),
                "decider_email": r["decider_email"],
                "is_ai": bool(r["decider_email"] == roles.AI_APPROVER_EMAIL or r["decider_name"] == roles.AI_APPROVER_NAME),
                "submitter_name": r["submitter_name"],
                "submitter_contact": r["submitter_contact"],
                "ai_decision": r["ai_decision"],
                "ai_confidence": r["ai_confidence"],
                "ai_reasoning": r["ai_reasoning"],
                "review_notes": r["review_notes"] if "review_notes" in r.keys() else None,
                "details": {
                    "name": r["name"],
                    "release_slug": r["release_slug"],
                    "date_published": r["date_published"],
                    "genres": ", ".join(genres) if genres else "—",
                    "license": r["license"],
                    "description": r["description"],
                    "tracks": tracks,
                    "tracks_count": len(tracks),
                    "published_at": r["published_at"],
                    "github_run_id": r["github_run_id"],
                },
            })

    # 4. Band proposals
    if selected_type in ("all", "band"):
        status_clause = ""
        params = [timeframe_filter]
        if selected_status == "approved":
            status_clause = "AND bp.status IN ('published', 'approved')"
        elif selected_status == "rejected":
            status_clause = "AND bp.status = 'rejected'"
        else:
            status_clause = "AND bp.status IN ('published', 'approved', 'rejected')"

        query = f"""
            SELECT bp.*, a.display_name AS decider_name, a.email AS decider_email
            FROM band_proposals bp
            LEFT JOIN approvers a ON bp.decided_by = a.id
            WHERE bp.decided_at IS NOT NULL
              AND datetime(bp.decided_at) >= datetime('now', ?)
              {status_clause}
            ORDER BY bp.decided_at DESC
        """
        for r in conn.execute(query, params).fetchall():
            norm_status = "approved" if r["status"] in ("published", "approved") else "rejected"
            try:
                tracks = json.loads(r["release_tracks_json"]) if r["release_tracks_json"] else []
            except Exception:
                tracks = []
            try:
                genres = json.loads(r["genre"]) if r["genre"] else []
            except Exception:
                genres = []
            items.append({
                "id": r["id"],
                "type": "band",
                "type_label": "New Band",
                "target_title": f"{r['name']} ({r['band_slug']})",
                "status": norm_status,
                "raw_status": r["status"],
                "created_at": r["created_at"],
                "decided_at": r["decided_at"],
                "decider_name": r["decider_name"] or ("AI Approval Agent" if (r["decider_email"] == roles.AI_APPROVER_EMAIL or not r["decided_by"]) else "Approver"),
                "decider_email": r["decider_email"],
                "is_ai": bool(r["decider_email"] == roles.AI_APPROVER_EMAIL or r["decider_name"] == roles.AI_APPROVER_NAME),
                "submitter_name": r["submitter_name"],
                "submitter_contact": r["submitter_contact"],
                "ai_decision": r["ai_decision"],
                "ai_confidence": r["ai_confidence"],
                "ai_reasoning": r["ai_reasoning"],
                "review_notes": r["review_notes"] if "review_notes" in r.keys() else None,
                "details": {
                    "name": r["name"],
                    "band_slug": r["band_slug"],
                    "founding_date": r["founding_date"],
                    "location": r["location"],
                    "genres": ", ".join(genres) if genres else "—",
                    "description": r["description"],
                    "has_release": bool(r["has_release"]),
                    "release_name": r["release_name"],
                    "tracks": tracks,
                    "tracks_count": len(tracks),
                    "published_at": r["published_at"],
                    "github_run_id": r["github_run_id"],
                },
            })

    # Sort unified feed by decided_at DESC
    items.sort(key=lambda x: x["decided_at"] or "", reverse=True)

    counts = {
        "total": len(items),
        "approved": sum(1 for i in items if i["status"] == "approved"),
        "rejected": sum(1 for i in items if i["status"] == "rejected"),
    }

    return render_template(
        "history.html",
        items=items,
        counts=counts,
        days=days,
        selected_status=selected_status,
        selected_type=selected_type,
        approver=g.approver,
        can_view_stats=roles.user_has_role(conn, g.approver["id"], roles.ROLE_STATS),
    )


@bp.get("/history")
@require_approver
def history_alias():
    return redirect(url_for("dashboard.decision_history", **request.args))


