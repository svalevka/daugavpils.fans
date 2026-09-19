"""
The authenticated callback API the GitHub Action calls (see GitHub issue
#13): fetch an approved proposal's content, and report back whether
applying it succeeded. Gated by a shared bearer key
(REVIEW_APP_CALLBACK_KEY) - the same value lives as an env var here and
as a GitHub repo secret, known only to review_app and the Action.
"""
from __future__ import annotations

import hmac
import json
import sys
import threading
import time
from pathlib import Path

from flask import Blueprint, Response, abort, current_app, jsonify, request, send_file

sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402

bp = Blueprint("api", __name__, url_prefix="/api")

_backup_lock = threading.Lock()
_backup_timestamps: list[float] = []
_backup_history_lock = threading.Lock()


def _require_callback_key() -> None:
    expected = current_app.config["REVIEW_APP_CALLBACK_KEY"]
    header = request.headers.get("Authorization", "")
    token = header.removeprefix("Bearer ") if header.startswith("Bearer ") else None
    if token is None or not hmac.compare_digest(token, expected):
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
        """
        SELECT p.id, p.band_slug, p.release_slug, p.target, p.list_index, p.field,
               p.original_value, p.proposed_value, p.ai_decision, p.ai_confidence,
               p.ai_reasoning, a.display_name AS decided_by_name
        FROM proposals p
        LEFT JOIN approvers a ON p.decided_by = a.id
        WHERE p.id = ?
        """,
        (proposal_id,),
    ).fetchone()
    return jsonify(
        {
            "id": row["id"],
            "band_slug": row["band_slug"],
            "release_slug": row["release_slug"],
            "target": row["target"],
            "list_index": row["list_index"],
            "field": row["field"],
            "original_value": json.loads(row["original_value"]),
            "proposed_value": json.loads(row["proposed_value"]),
            "decided_by_name": row["decided_by_name"] if "decided_by_name" in row.keys() else None,
            "ai_decision": row["ai_decision"] if "ai_decision" in row.keys() else None,
            "ai_confidence": row["ai_confidence"] if "ai_confidence" in row.keys() else None,
            "ai_reasoning": row["ai_reasoning"] if "ai_reasoning" in row.keys() else None,
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
        """
        SELECT m.id, m.band_slug, m.release_slug, m.media_type, m.original_filename,
               m.content_type, m.size_bytes, m.caption, m.ai_decision, m.ai_confidence,
               m.ai_reasoning, a.display_name AS decided_by_name
        FROM media_proposals m
        LEFT JOIN approvers a ON m.decided_by = a.id
        WHERE m.id = ?
        """,
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
            "decided_by_name": row["decided_by_name"] if "decided_by_name" in row.keys() else None,
            "ai_decision": row["ai_decision"] if "ai_decision" in row.keys() else None,
            "ai_confidence": row["ai_confidence"] if "ai_confidence" in row.keys() else None,
            "ai_reasoning": row["ai_reasoning"] if "ai_reasoning" in row.keys() else None,
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
    from dashboard import is_youtube_video_publishing_throttled

    conn = db.get_connection()
    # Direct-upload media is never throttled - only source_type =
    # 'youtube' is subject to the rolling-24h cap (see GitHub issue #49,
    # decision 14), same pattern as list_approved_album_proposals above.
    ids = [
        row["id"]
        for row in conn.execute(
            "SELECT id FROM media_proposals WHERE source_type != 'youtube' "
            "AND status IN ('approved', 'publishing') ORDER BY id"
        ).fetchall()
    ]

    is_throttled, count, _ = is_youtube_video_publishing_throttled(conn)
    if not is_throttled:
        remaining_quota = max(0, 3 - count)
        ids.extend(
            row["id"]
            for row in conn.execute(
                "SELECT id FROM media_proposals WHERE source_type = 'youtube' "
                "AND status IN ('approved', 'publishing') ORDER BY id LIMIT ?",
                (remaining_quota,),
            ).fetchall()
        )

    return jsonify({"ids": sorted(ids)})


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


@bp.get("/album-proposals/<int:proposal_id>")
def get_album_proposal(proposal_id: int):
    _require_callback_key()
    conn = db.get_connection()

    cur = conn.execute(
        "UPDATE album_proposals SET status = 'publishing' "
        "WHERE id = ? AND status IN ('approved', 'publishing', 'publish_failed')",
        (proposal_id,),
    )
    conn.commit()
    if cur.rowcount != 1:
        row = conn.execute("SELECT id FROM album_proposals WHERE id = ?", (proposal_id,)).fetchone()
        abort(404 if row is None else 409)

    row = conn.execute(
        """
        SELECT a.id, a.band_slug, a.release_slug, a.name, a.date_published, a.genre,
               a.license, a.description, a.description_en, a.cover_stored_filename,
               a.tracks_json, a.ai_decision, a.ai_confidence, a.ai_reasoning,
               ap.display_name AS decided_by_name
        FROM album_proposals a
        LEFT JOIN approvers ap ON a.decided_by = ap.id
        WHERE a.id = ?
        """,
        (proposal_id,),
    ).fetchone()
    return jsonify(
        {
            "id": row["id"],
            "band_slug": row["band_slug"],
            "release_slug": row["release_slug"],
            "name": row["name"],
            "date_published": row["date_published"],
            "genre": json.loads(row["genre"]) if row["genre"] else [],
            "license": row["license"],
            "description": row["description"],
            "description_en": row["description_en"],
            "has_cover": bool(row["cover_stored_filename"]),
            "tracks": json.loads(row["tracks_json"]) if row["tracks_json"] else [],
            "decided_by_name": row["decided_by_name"] if "decided_by_name" in row.keys() else None,
            "ai_decision": row["ai_decision"] if "ai_decision" in row.keys() else None,
            "ai_confidence": row["ai_confidence"] if "ai_confidence" in row.keys() else None,
            "ai_reasoning": row["ai_reasoning"] if "ai_reasoning" in row.keys() else None,
        }
    )


@bp.get("/album-proposals/<int:proposal_id>/cover")
def get_album_proposal_cover(proposal_id: int):
    _require_callback_key()
    conn = db.get_connection()
    row = conn.execute(
        "SELECT cover_stored_filename FROM album_proposals WHERE id = ?", (proposal_id,)
    ).fetchone()
    if row is None or not row["cover_stored_filename"]:
        abort(404)
    uploads_path = current_app.config["MEDIA_UPLOADS_PATH"]
    path = Path(uploads_path) / row["cover_stored_filename"]
    if not path.exists():
        abort(404)
    return send_file(path)


@bp.get("/album-proposals/<int:proposal_id>/tracks/<int:position>/file")
def get_album_proposal_track_file(proposal_id: int, position: int):
    _require_callback_key()
    conn = db.get_connection()
    row = conn.execute(
        "SELECT tracks_json FROM album_proposals WHERE id = ?", (proposal_id,)
    ).fetchone()
    if row is None or not row["tracks_json"]:
        abort(404)
    tracks = json.loads(row["tracks_json"])
    track = next((t for t in tracks if t.get("position") == position), None)
    if track is None or not track.get("stored_filename"):
        abort(404)
    uploads_path = current_app.config["MEDIA_UPLOADS_PATH"]
    path = Path(uploads_path) / track["stored_filename"]
    if not path.exists():
        abort(404)
    return send_file(path, mimetype=track.get("content_type", "audio/mpeg"))


@bp.get("/album-proposals/approved")
def list_approved_album_proposals():
    _require_callback_key()
    from dashboard import is_album_publishing_throttled

    conn = db.get_connection()
    is_throttled, count, _ = is_album_publishing_throttled(conn)
    if is_throttled:
        return jsonify([])
    remaining_quota = max(0, 3 - count)
    rows = conn.execute(
        "SELECT id FROM album_proposals WHERE status IN ('approved', 'publishing') ORDER BY id LIMIT ?",
        (remaining_quota,),
    ).fetchall()
    return jsonify([row["id"] for row in rows])


@bp.post("/album-proposals/<int:proposal_id>/result")
def record_album_publish_result(proposal_id: int):
    _require_callback_key()
    payload = request.get_json(silent=True) or {}
    success = bool(payload.get("success"))
    new_status = "published" if success else "publish_failed"

    conn = db.get_connection()
    row = conn.execute(
        "SELECT cover_stored_filename, tracks_json FROM album_proposals WHERE id = ?", (proposal_id,)
    ).fetchone()
    if row is None:
        abort(404)

    if success:
        conn.execute(
            "UPDATE album_proposals SET status = 'published', published_at = datetime('now'), "
            "github_run_id = ?, publish_error = NULL WHERE id = ?",
            (payload.get("run_id"), proposal_id),
        )
        conn.commit()

        uploads_path = current_app.config["MEDIA_UPLOADS_PATH"]
        if row["cover_stored_filename"]:
            (Path(uploads_path) / row["cover_stored_filename"]).unlink(missing_ok=True)
        if row["tracks_json"]:
            tracks = json.loads(row["tracks_json"])
            for t in tracks:
                if "stored_filename" in t:
                    (Path(uploads_path) / t["stored_filename"]).unlink(missing_ok=True)
    else:
        conn.execute(
            "UPDATE album_proposals SET status = 'publish_failed', "
            "github_run_id = ?, publish_error = ? WHERE id = ?",
            (payload.get("run_id"), payload.get("error"), proposal_id),
        )
        conn.commit()

    return jsonify({"status": new_status})


@bp.get("/band-proposals/<int:proposal_id>")
def get_band_proposal(proposal_id: int):
    _require_callback_key()
    conn = db.get_connection()

    cur = conn.execute(
        "UPDATE band_proposals SET status = 'publishing' "
        "WHERE id = ? AND status IN ('approved', 'publishing', 'publish_failed')",
        (proposal_id,),
    )
    conn.commit()
    if cur.rowcount != 1:
        row = conn.execute("SELECT id FROM band_proposals WHERE id = ?", (proposal_id,)).fetchone()
        abort(404 if row is None else 409)

    row = conn.execute(
        """
        SELECT b.id, b.name, b.band_slug, b.founding_date, b.dissolution_date, b.location,
               b.genre, b.description, b.description_en, b.band_photo_stored_filename,
               b.has_release, b.release_name, b.release_slug, b.release_date_published,
               b.release_genre, b.release_license, b.release_description, b.release_description_en,
               b.release_cover_stored_filename, b.release_tracks_json,
               b.ai_decision, b.ai_confidence, b.ai_reasoning,
               ap.display_name AS decided_by_name
        FROM band_proposals b
        LEFT JOIN approvers ap ON b.decided_by = ap.id
        WHERE b.id = ?
        """,
        (proposal_id,),
    ).fetchone()
    return jsonify(
        {
            "id": row["id"],
            "name": row["name"],
            "band_slug": row["band_slug"],
            "founding_date": row["founding_date"],
            "dissolution_date": row["dissolution_date"],
            "location": row["location"],
            "genre": json.loads(row["genre"]) if row["genre"] else [],
            "description": row["description"],
            "description_en": row["description_en"],
            "has_photo": bool(row["band_photo_stored_filename"]),
            "has_release": bool(row["has_release"]),
            "release_name": row["release_name"],
            "release_slug": row["release_slug"],
            "release_date_published": row["release_date_published"],
            "release_genre": json.loads(row["release_genre"]) if row["release_genre"] else [],
            "release_license": row["release_license"],
            "release_description": row["release_description"],
            "release_description_en": row["release_description_en"],
            "has_release_cover": bool(row["release_cover_stored_filename"]),
            "tracks": json.loads(row["release_tracks_json"]) if row["release_tracks_json"] else [],
            "decided_by_name": row["decided_by_name"] if "decided_by_name" in row.keys() else None,
            "ai_decision": row["ai_decision"] if "ai_decision" in row.keys() else None,
            "ai_confidence": row["ai_confidence"] if "ai_confidence" in row.keys() else None,
            "ai_reasoning": row["ai_reasoning"] if "ai_reasoning" in row.keys() else None,
        }
    )


@bp.get("/band-proposals/<int:proposal_id>/photo")
def get_band_proposal_photo(proposal_id: int):
    _require_callback_key()
    conn = db.get_connection()
    row = conn.execute(
        "SELECT band_photo_stored_filename FROM band_proposals WHERE id = ?", (proposal_id,)
    ).fetchone()
    if row is None or not row["band_photo_stored_filename"]:
        abort(404)
    uploads_path = current_app.config["MEDIA_UPLOADS_PATH"]
    path = Path(uploads_path) / row["band_photo_stored_filename"]
    if not path.exists():
        abort(404)
    return send_file(path)


@bp.get("/band-proposals/<int:proposal_id>/cover")
def get_band_proposal_cover(proposal_id: int):
    _require_callback_key()
    conn = db.get_connection()
    row = conn.execute(
        "SELECT release_cover_stored_filename FROM band_proposals WHERE id = ?", (proposal_id,)
    ).fetchone()
    if row is None or not row["release_cover_stored_filename"]:
        abort(404)
    uploads_path = current_app.config["MEDIA_UPLOADS_PATH"]
    path = Path(uploads_path) / row["release_cover_stored_filename"]
    if not path.exists():
        abort(404)
    return send_file(path)


@bp.get("/band-proposals/<int:proposal_id>/tracks/<int:position>/file")
def get_band_proposal_track_file(proposal_id: int, position: int):
    _require_callback_key()
    conn = db.get_connection()
    row = conn.execute(
        "SELECT release_tracks_json FROM band_proposals WHERE id = ?", (proposal_id,)
    ).fetchone()
    if row is None or not row["release_tracks_json"]:
        abort(404)
    tracks = json.loads(row["release_tracks_json"])
    track = next((t for t in tracks if t.get("position") == position), None)
    if track is None or not track.get("stored_filename"):
        abort(404)
    uploads_path = current_app.config["MEDIA_UPLOADS_PATH"]
    path = Path(uploads_path) / track["stored_filename"]
    if not path.exists():
        abort(404)
    return send_file(path, mimetype=track.get("content_type", "audio/mpeg"))


@bp.get("/band-proposals/approved")
def list_approved_band_proposals():
    _require_callback_key()
    from dashboard import is_band_publishing_throttled

    conn = db.get_connection()
    is_throttled, _ = is_band_publishing_throttled(conn)
    if is_throttled:
        return jsonify([])
    rows = conn.execute(
        "SELECT id FROM band_proposals WHERE status IN ('approved', 'publishing') ORDER BY id LIMIT 1"
    ).fetchall()
    return jsonify([row["id"] for row in rows])


@bp.post("/band-proposals/<int:proposal_id>/result")
def record_band_publish_result(proposal_id: int):
    _require_callback_key()
    payload = request.get_json(silent=True) or {}
    success = bool(payload.get("success"))
    new_status = "published" if success else "publish_failed"

    conn = db.get_connection()
    row = conn.execute(
        "SELECT band_photo_stored_filename, release_cover_stored_filename, release_tracks_json FROM band_proposals WHERE id = ?",
        (proposal_id,),
    ).fetchone()
    if row is None:
        abort(404)

    if success:
        conn.execute(
            "UPDATE band_proposals SET status = 'published', published_at = datetime('now'), "
            "github_run_id = ?, publish_error = NULL WHERE id = ?",
            (payload.get("run_id"), proposal_id),
        )
        conn.commit()

        uploads_path = current_app.config["MEDIA_UPLOADS_PATH"]
        if row["band_photo_stored_filename"]:
            (Path(uploads_path) / row["band_photo_stored_filename"]).unlink(missing_ok=True)
        if row["release_cover_stored_filename"]:
            (Path(uploads_path) / row["release_cover_stored_filename"]).unlink(missing_ok=True)
        if row["release_tracks_json"]:
            tracks = json.loads(row["release_tracks_json"])
            for t in tracks:
                if "stored_filename" in t:
                    (Path(uploads_path) / t["stored_filename"]).unlink(missing_ok=True)
    else:
        conn.execute(
            "UPDATE band_proposals SET status = 'publish_failed', "
            "github_run_id = ?, publish_error = ? WHERE id = ?",
            (payload.get("run_id"), payload.get("error"), proposal_id),
        )
        conn.commit()

    return jsonify({"status": new_status})


@bp.get("/backup")
def get_backup_bundle():
    """Stream an atomic snapshot of review.db and uploads/ to authorized caller."""
    _require_callback_key()
    import io
    import os
    import sqlite3
    import tarfile

    # Application-layer rate limiting: reject rapid bursts exceeding limit
    limit = current_app.config.get("BACKUP_RATE_LIMIT_PER_MINUTE", 2)
    now = time.time()
    with _backup_history_lock:
        _backup_timestamps[:] = [t for t in _backup_timestamps if now - t < 60.0]
        if len(_backup_timestamps) >= limit:
            abort(429)
        _backup_timestamps.append(now)

    # Concurrency limit: prevent concurrent backup generations from exhausting CPU/RAM
    if not _backup_lock.acquire(blocking=False):
        abort(429)

    fmt = request.args.get("format", "").lower()
    accept = request.headers.get("Accept", "").lower()
    is_plain_tar = (fmt == "tar") or ("application/x-tar" in accept and "gzip" not in accept)

    tar_mode = "w|" if is_plain_tar else "w|gz"
    mimetype = "application/x-tar" if is_plain_tar else "application/gzip"
    filename = "review-app-backup.tar" if is_plain_tar else "review-app-backup.tar.gz"

    db_path = Path(current_app.config["DATABASE_PATH"])
    uploads_path = Path(current_app.config["MEDIA_UPLOADS_PATH"])

    # Atomically snapshot SQLite database in memory without creating temporary files on disk
    db_bytes = b""
    if db_path.exists():
        src = sqlite3.connect(db_path)
        dst = sqlite3.connect(":memory:")
        src.backup(dst)
        db_bytes = dst.serialize()
        dst.close()
        src.close()

    try:
        r, w = os.pipe()

        def generate_tar():
            try:
                with os.fdopen(w, "wb") as f:
                    kwargs = {}
                    if not is_plain_tar:
                        kwargs["compresslevel"] = 1
                    with tarfile.open(mode=tar_mode, fileobj=f, **kwargs) as tar:
                        if db_bytes:
                            ti = tarfile.TarInfo(name="review.db")
                            ti.size = len(db_bytes)
                            tar.addfile(ti, io.BytesIO(db_bytes))

                        if uploads_path.exists() and any(uploads_path.iterdir()):
                            try:
                                tar.add(uploads_path, arcname="uploads", recursive=True)
                            except (FileNotFoundError, PermissionError):
                                pass
            except (BrokenPipeError, OSError):
                # Client disconnected prematurely during streaming
                pass

        t = threading.Thread(target=generate_tar, daemon=True)
        t.start()

        def generate_chunks():
            try:
                with os.fdopen(r, "rb") as rf:
                    while chunk := rf.read(65536):
                        yield chunk
            finally:
                try:
                    t.join(timeout=5)
                finally:
                    if _backup_lock.locked():
                        _backup_lock.release()

        response = Response(
            generate_chunks(),
            mimetype=mimetype,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
        response.call_on_close(lambda: _backup_lock.locked() and _backup_lock.release())
        return response
    except Exception:
        if _backup_lock.locked():
            _backup_lock.release()
        raise


