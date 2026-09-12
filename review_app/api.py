"""
The authenticated callback API the GitHub Action calls (see GitHub issue
#13): fetch an approved proposal's content, and report back whether
applying it succeeded. Gated by a shared bearer key
(REVIEW_APP_CALLBACK_KEY) - the same value lives as an env var here and
as a GitHub repo secret, known only to review_app and the Action.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from flask import Blueprint, abort, current_app, jsonify, request, send_file

sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402

bp = Blueprint("api", __name__, url_prefix="/api")


def _require_callback_key() -> None:
    expected = current_app.config["REVIEW_APP_CALLBACK_KEY"]
    header = request.headers.get("Authorization", "")
    token = header.removeprefix("Bearer ") if header.startswith("Bearer ") else None
    if token is None or token != expected:
        abort(401)


@bp.get("/proposals/<int:proposal_id>")
def get_proposal(proposal_id: int):
    _require_callback_key()
    conn = db.get_connection()

    # Atomic check-and-set (same pattern as dashboard.py's _decide() and
    # auth.py's verify()): only a proposal currently 'approved' can be
    # fetched, and fetching immediately flips it to 'applying' - so a
    # retried Action run, or anyone who captures a proposal id, can never
    # fetch the same approved content twice.
    cur = conn.execute(
        "UPDATE proposals SET status = 'applying' WHERE id = ? AND status = 'approved'",
        (proposal_id,),
    )
    conn.commit()
    if cur.rowcount != 1:
        row = conn.execute("SELECT id FROM proposals WHERE id = ?", (proposal_id,)).fetchone()
        abort(404 if row is None else 409)

    row = conn.execute(
        "SELECT band_slug, release_slug, target, list_index, field, original_value, proposed_value "
        "FROM proposals WHERE id = ?",
        (proposal_id,),
    ).fetchone()
    return jsonify(
        {
            "band_slug": row["band_slug"],
            "release_slug": row["release_slug"],
            "target": row["target"],
            "list_index": row["list_index"],
            "field": row["field"],
            "original_value": json.loads(row["original_value"]),
            "proposed_value": json.loads(row["proposed_value"]),
        }
    )


@bp.get("/proposals/approved")
def list_approved_proposals():
    """Every proposal still sitting at 'approved' - i.e. never even
    reached the fetch step, whether because no dispatch was made for it
    yet or because a dispatch was made but GitHub's own concurrency-group
    queue silently evicted it before it ever ran (only one *running* + one
    *queued* run are kept per group; a burst of approvals can drop an
    older queued dispatch entirely - see GitHub issue #28). Consumed by
    apply-proposal.yml's `schedule`-triggered sweep as its worklist, so a
    dropped dispatch still gets applied on the next sweep instead of
    sitting stuck forever. Deliberately does *not* include 'apply_failed'
    rows - those did run and failed for some other reason (bad content, a
    push race that outlasted its retries), which needs a human look, not
    an automatic retry loop."""
    _require_callback_key()
    conn = db.get_connection()
    rows = conn.execute("SELECT id FROM proposals WHERE status = 'approved' ORDER BY id").fetchall()
    return jsonify({"ids": [row["id"] for row in rows]})


@bp.post("/proposals/<int:proposal_id>/apply-result")
def apply_result(proposal_id: int):
    _require_callback_key()
    conn = db.get_connection()

    payload = request.get_json(silent=True) or {}
    success = bool(payload.get("success"))
    new_status = "applied" if success else "apply_failed"

    # A success report can only resolve a proposal we ourselves flipped
    # to 'applying' (GET /proposals/<id>) - that's the only way anyone
    # could truthfully be reporting a completed apply. A *failure*
    # report, though, must also be accepted straight from 'approved': the
    # Action can fail before it ever reaches the fetch step (checkout,
    # dependency install, or the fetch itself failing on a network blip),
    # in which case the proposal was never flipped at all. Without this,
    # that failure report would silently 404/409 and the proposal would
    # sit at 'approved' forever with no record anything went wrong -
    # exactly the "stuck" state the status field exists to prevent.
    from_statuses = ("applying",) if success else ("applying", "approved")
    cur = conn.execute(
        f"UPDATE proposals SET status = ?, applied_at = datetime('now'), "
        f"github_run_id = ?, apply_error = ? "
        f"WHERE id = ? AND status IN ({','.join('?' * len(from_statuses))})",
        (new_status, payload.get("run_id"), payload.get("error"), proposal_id, *from_statuses),
    )
    conn.commit()
    if cur.rowcount != 1:
        row = conn.execute("SELECT id FROM proposals WHERE id = ?", (proposal_id,)).fetchone()
        abort(404 if row is None else 409)

    return jsonify({"status": new_status})


@bp.get("/media-proposals/<int:proposal_id>")
def get_media_proposal(proposal_id: int):
    _require_callback_key()
    conn = db.get_connection()

    cur = conn.execute(
        "UPDATE media_proposals SET status = 'publishing' "
        "WHERE id = ? AND status IN ('approved', 'publishing', 'publish_failed')",
        (proposal_id,),
    )
    conn.commit()
    if cur.rowcount != 1:
        row = conn.execute("SELECT id FROM media_proposals WHERE id = ?", (proposal_id,)).fetchone()
        abort(404 if row is None else 409)

    row = conn.execute(
        "SELECT id, band_slug, release_slug, media_type, original_filename, content_type, size_bytes, caption "
        "FROM media_proposals WHERE id = ?",
        (proposal_id,),
    ).fetchone()
    return jsonify(
        {
            "id": row["id"],
            "band_slug": row["band_slug"],
            "release_slug": row["release_slug"],
            "media_type": row["media_type"],
            "original_filename": row["original_filename"],
            "content_type": row["content_type"],
            "size_bytes": row["size_bytes"],
            "caption": row["caption"],
        }
    )


@bp.get("/media-proposals/<int:proposal_id>/file")
def get_media_proposal_file(proposal_id: int):
    _require_callback_key()
    conn = db.get_connection()
    row = conn.execute(
        "SELECT stored_filename, content_type FROM media_proposals WHERE id = ?",
        (proposal_id,),
    ).fetchone()
    if row is None:
        abort(404)
    uploads_path = current_app.config["MEDIA_UPLOADS_PATH"]
    path = Path(uploads_path) / row["stored_filename"]
    if not path.exists():
        abort(404)
    return send_file(path, mimetype=row["content_type"])


@bp.get("/media-proposals/approved")
def list_approved_media_proposals():
    _require_callback_key()
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT id FROM media_proposals WHERE status IN ('approved', 'publishing') ORDER BY id"
    ).fetchall()
    return jsonify({"ids": [row["id"] for row in rows]})


@bp.post("/media-proposals/<int:proposal_id>/publish-result")
def media_publish_result(proposal_id: int):
    _require_callback_key()
    conn = db.get_connection()

    payload = request.get_json(silent=True) or {}
    success = bool(payload.get("success"))
    new_status = "published" if success else "publish_failed"

    row = conn.execute(
        "SELECT stored_filename, status FROM media_proposals WHERE id = ?", (proposal_id,)
    ).fetchone()
    if row is None:
        abort(404)

    from_statuses = ("publishing", "approved")
    if row["status"] not in from_statuses:
        abort(409)

    if success:
        cur = conn.execute(
            "UPDATE media_proposals SET status = 'published', published_at = datetime('now'), "
            "github_run_id = ?, publish_error = NULL "
            "WHERE id = ? AND status IN ('publishing', 'approved')",
            (payload.get("run_id"), proposal_id),
        )
        conn.commit()
        if cur.rowcount == 1:
            uploads_path = current_app.config["MEDIA_UPLOADS_PATH"]
            (Path(uploads_path) / row["stored_filename"]).unlink(missing_ok=True)
        else:
            abort(409)
    else:
        conn.execute(
            "UPDATE media_proposals SET status = 'publish_failed', "
            "github_run_id = ?, publish_error = ? WHERE id = ?",
            (payload.get("run_id"), payload.get("error"), proposal_id),
        )
        conn.commit()

    return jsonify({"status": new_status})

