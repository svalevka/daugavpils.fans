"""
The public /submit/<band_slug>/add-release flow (GitHub issue #20):
Propose a brand-new release with audio tracks and optional cover art
under an existing band.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from flask import Blueprint, abort, current_app, render_template, request, session, url_for

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(REPO_ROOT / "tools"))

import ai_agent  # noqa: E402
import archive_read  # noqa: E402
import audio_validation  # noqa: E402
import db  # noqa: E402
import mail  # noqa: E402
import media_uploads  # noqa: E402
import roles  # noqa: E402
from config import AiConfig  # noqa: E402

logger = logging.getLogger(__name__)

bp = Blueprint("album_submissions", __name__)

STANDARD_LICENSES = [
    ("https://creativecommons.org/licenses/by-nc-sa/4.0/", "Creative Commons BY-NC-SA 4.0 (Default)"),
    ("https://creativecommons.org/licenses/by/4.0/", "Creative Commons BY 4.0"),
    ("https://creativecommons.org/licenses/by-sa/4.0/", "Creative Commons BY-SA 4.0"),
    ("https://creativecommons.org/publicdomain/zero/1.0/", "CC0 1.0 Universal (Public Domain)"),
    ("all-rights-reserved", "All Rights Reserved"),
]


def _resolve_submitted_by_approver_id(
    conn, session_approver_id: int | None, submitter_contact: str | None
) -> int | None:
    if session_approver_id is not None:
        row = conn.execute(
            "SELECT id FROM approvers WHERE id = ? AND is_active = 1", (session_approver_id,)
        ).fetchone()
        if row is not None:
            return row["id"]
    if submitter_contact:
        row = conn.execute(
            "SELECT id FROM approvers WHERE is_active = 1 AND LOWER(email) = LOWER(?)",
            (submitter_contact,),
        ).fetchone()
        if row is not None:
            return row["id"]
    return None


@bp.get("/submit/<band_slug>/add-release")
def add_release_form(band_slug: str):
    checkout = current_app.config["ARCHIVE_CHECKOUT_PATH"]
    try:
        band = archive_read.get_band(checkout, band_slug)
    except archive_read.ApplyError:
        abort(404)

    return render_template(
        "submit_add_release.html",
        band_slug=band_slug,
        band_name=band.name,
        licenses=STANDARD_LICENSES,
        default_license="https://creativecommons.org/licenses/by-nc-sa/4.0/",
    )


@bp.post("/submit-release")
def create_album_proposal():
    conn = db.get_connection()
    ip = request.remote_addr or "unknown"

    limit = current_app.config["RATE_LIMIT_PER_IP_PER_HOUR"]
    recent = conn.execute(
        "SELECT COUNT(*) FROM submission_log WHERE ip = ? AND submitted_at > datetime('now', '-1 hour')",
        (ip,),
    ).fetchone()[0]
    if recent >= limit:
        abort(429)

    conn.execute("INSERT INTO submission_log (ip) VALUES (?)", (ip,))
    conn.commit()

    if request.form.get("website"):  # honeypot check
        return render_template("submit_add_release_done.html", title="", count=0), 201

    band_slug = request.form.get("band_slug", "").strip()
    album_name = request.form.get("name", "").strip()
    date_published = request.form.get("date_published", "").strip()
    genre_raw = request.form.get("genre", "").strip()
    license_url = request.form.get("license", "").strip() or "https://creativecommons.org/licenses/by-nc-sa/4.0/"
    description = request.form.get("description", "").strip() or None
    description_en = request.form.get("description_en", "").strip() or None
    submitter_name = request.form.get("submitter_name", "").strip() or None
    submitter_contact = request.form.get("submitter_contact", "").strip() or None

    if not band_slug or not album_name or not date_published:
        abort(400)

    checkout = current_app.config["ARCHIVE_CHECKOUT_PATH"]
    try:
        archive_read.get_band(checkout, band_slug)
    except archive_read.ApplyError:
        abort(404)

    try:
        release_slug = audio_validation.generate_release_slug(album_name, date_published)
    except audio_validation.AudioValidationError:
        abort(400)

    # Check if release already exists in archive
    existing_release_dir = Path(checkout) / "bands" / band_slug / release_slug
    if (existing_release_dir / "release.yaml").exists():
        abort(400)

    # Check files
    audio_files = [f for f in request.files.getlist("tracks") if f and f.filename]
    if not audio_files:
        abort(400)

    uploads_dir = current_app.config["MEDIA_UPLOADS_PATH"]
    max_track_bytes = current_app.config.get("MAX_TRACK_UPLOAD_BYTES", 50 * 1024 * 1024)
    max_album_total_bytes = current_app.config.get("MAX_ALBUM_TOTAL_BYTES", 350 * 1024 * 1024)
    max_cover_bytes = current_app.config.get("MAX_COVER_UPLOAD_BYTES", 15 * 1024 * 1024)
    max_total_storage_bytes = current_app.config.get("MAX_TOTAL_UPLOAD_STORAGE_BYTES")
    min_disk_free_bytes = current_app.config.get("MIN_DISK_FREE_BYTES")

    # Cover upload (optional)
    cover_stored_filename = None
    cover_file = request.files.get("cover")
    if cover_file and cover_file.filename:
        try:
            stored_name, content_type, media_type, _size = media_uploads.save_upload(
                cover_file,
                uploads_dir,
                {"image": max_cover_bytes},
                max_total_storage_bytes=max_total_storage_bytes,
                min_disk_free_bytes=min_disk_free_bytes,
            )
            cover_stored_filename = stored_name
        except media_uploads.UploadRejected as exc:
            logger.warning("Cover image upload rejected: %s", exc)
            abort(400)

    # Save audio files
    tracks_list: list[dict] = []
    total_audio_bytes = 0
    saved_files: list[Path] = []
    if cover_stored_filename:
        saved_files.append(Path(uploads_dir) / cover_stored_filename)

    for index, file_storage in enumerate(audio_files):
        try:
            stored_filename, content_type, media_type, size = media_uploads.save_upload(
                file_storage,
                uploads_dir,
                {"audio": max_track_bytes},
                max_total_storage_bytes=max_total_storage_bytes,
                min_disk_free_bytes=min_disk_free_bytes,
            )
        except media_uploads.UploadRejected as exc:
            logger.warning("Audio upload rejected: %s", exc)
            for f in saved_files:
                f.unlink(missing_ok=True)
            abort(400)

        file_path = Path(uploads_dir) / stored_filename
        saved_files.append(file_path)
        total_audio_bytes += size

        if total_audio_bytes > max_album_total_bytes:
            for f in saved_files:
                f.unlink(missing_ok=True)
            abort(400)

        try:
            probe_info = audio_validation.probe_audio_file(file_path)
        except audio_validation.AudioValidationError as exc:
            logger.warning("ffprobe audio validation failed for %s: %s", file_storage.filename, exc)
            for f in saved_files:
                f.unlink(missing_ok=True)
            abort(400)

        sha256 = audio_validation.sha256_of(file_path)
        track_title = (
            request.form.get(f"track_title_{index}")
            or audio_validation.sanitize_track_title(file_storage.filename, probe_info["tags"].get("title"))
        )

        tracks_list.append(
            {
                "position": index + 1,
                "name": track_title,
                "stored_filename": stored_filename,
                "original_filename": file_storage.filename,
                "content_type": content_type,
                "duration": probe_info["duration_iso"],
                "duration_seconds": probe_info["duration_seconds"],
                "bitrate": probe_info["bitrate"],
                "sha256": sha256,
                "ai_flags": probe_info["ai_flags"],
            }
        )

    genre_list = [g.strip() for g in genre_raw.split(",") if g.strip()] if genre_raw else []
    submitted_by_approver_id = _resolve_submitted_by_approver_id(
        conn, session.get("approver_id"), submitter_contact
    )

    cur = conn.execute(
        """
        INSERT INTO album_proposals (
            band_slug, release_slug, name, date_published, genre, license,
            description, description_en, cover_stored_filename, tracks_json,
            submitter_name, submitter_contact, submitter_ip,
            submitted_by_approver_id, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
        """,
        (
            band_slug,
            release_slug,
            album_name,
            date_published,
            json.dumps(genre_list),
            license_url,
            description,
            description_en,
            cover_stored_filename,
            json.dumps(tracks_list),
            submitter_name,
            submitter_contact,
            ip,
            submitted_by_approver_id,
        ),
    )
    proposal_id = cur.lastrowid
    conn.commit()

    ai_config: AiConfig = current_app.config.get("AI_CONFIG") or AiConfig()
    if ai_config.mode != "disabled":
        try:
            ai_agent.dispatch_album_evaluation(current_app._get_current_object(), proposal_id)
        except Exception:
            logger.exception("failed to dispatch AI evaluation for album proposal %s", proposal_id)
    else:
        # Fallback maintainer email notification
        smtp_cfg = current_app.config.get("SMTP_CONFIG")
        if smtp_cfg:
            try:
                login_url = url_for("auth.login_form", _external=True)
                summary = (
                    f"New album proposal #{proposal_id} submitted for band '{band_slug}':\n\n"
                    f"Title: {album_name}\n"
                    f"Year: {date_published}\n"
                    f"Tracks: {len(tracks_list)}\n"
                    f"Submitter: {submitter_name or 'Anonymous'} <{submitter_contact or 'no contact'}>\n\n"
                    f"Review in dashboard: {login_url}\n"
                )
                recipients = roles.get_approver_recipients(conn, current_app.config.get("MAINTAINER_EMAIL"))
                mail.send_submission_notification(smtp_cfg, recipients, summary)
            except Exception as exc:
                logger.warning("Failed to send album submission notification: %s", exc)

    return render_template("submit_add_release_done.html", title=album_name, count=len(tracks_list)), 201
