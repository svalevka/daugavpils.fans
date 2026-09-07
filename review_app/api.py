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

from flask import Blueprint, abort, current_app, jsonify, request

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
