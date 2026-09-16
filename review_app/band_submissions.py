"""
The public /submit/add-band flow (GitHub issue #22):
Propose a brand-new band with testimony, optional band photo,
and optional first release (audio tracks + cover art).
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
import audio_validation  # noqa: E402
import db  # noqa: E402
import i18n  # noqa: E402
import mail  # noqa: E402
import media_uploads  # noqa: E402
import roles  # noqa: E402
from config import AiConfig  # noqa: E402

logger = logging.getLogger(__name__)

bp = Blueprint("band_submissions", __name__)

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


@bp.get("/submit/add-band")
def add_band_form():
    return render_template(
        "submit_add_band.html",
        licenses=STANDARD_LICENSES,
        default_license="https://creativecommons.org/licenses/by-nc-sa/4.0/",
    )


@bp.post("/submit-band")
def create_band_proposal():
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
        return render_template("submit_add_band_done.html", band_name="", has_release=False, count=0), 201

    band_name = request.form.get("name", "").strip()
    founding_date = request.form.get("founding_date", "").strip() or None
    dissolution_date = request.form.get("dissolution_date", "").strip() or None
    location = request.form.get("location", "").strip() or "Daugavpils, Latvia"
    genre_raw = request.form.get("genre", "").strip()
    description = request.form.get("description", "").strip() or None
    description_en = request.form.get("description_en", "").strip() or None
    submitter_name = request.form.get("submitter_name", "").strip() or None
    submitter_contact = request.form.get("submitter_contact", "").strip() or None

    if not band_name:
        abort(400)

    try:
        band_slug = audio_validation.generate_band_slug(band_name)
    except audio_validation.AudioValidationError:
        abort(400)

    # Check collision with existing band directory in checkout
    checkout = current_app.config["ARCHIVE_CHECKOUT_PATH"]
    existing_band_dir = Path(checkout) / "bands" / band_slug
    if (existing_band_dir / "band.yaml").exists():
        logger.warning("Band slug %s already exists in archive", band_slug)
        abort(400)

    # Check collision with existing pending/approved band proposals
    collision = conn.execute(
        "SELECT id FROM band_proposals WHERE band_slug = ? AND status IN ('pending', 'approved', 'publishing')",
        (band_slug,),
    ).fetchone()
    if collision is not None:
        logger.warning("Band slug %s already exists in pending proposals", band_slug)
        abort(400)

    uploads_dir = current_app.config["MEDIA_UPLOADS_PATH"]
    max_total_storage_bytes = current_app.config.get("MAX_TOTAL_UPLOAD_STORAGE_BYTES", 2 * 1024 * 1024 * 1024)
    min_disk_free_bytes = current_app.config.get("MIN_DISK_FREE_BYTES", 1024 * 1024 * 1024)
    max_photo_bytes = current_app.config.get("MAX_UPLOAD_BYTES", {}).get("image", 25 * 1024 * 1024)
    max_cover_bytes = current_app.config.get("MAX_COVER_UPLOAD_BYTES", 15 * 1024 * 1024)
    max_track_bytes = current_app.config.get("MAX_TRACK_UPLOAD_BYTES", 50 * 1024 * 1024)
    max_album_bytes = current_app.config.get("MAX_ALBUM_TOTAL_BYTES", 350 * 1024 * 1024)

    saved_files: list[Path] = []

    # Optional band photo
    band_photo_stored_filename = None
    photo_file = request.files.get("photo")
    if photo_file and photo_file.filename:
        try:
            stored_name, content_type, media_type, _size = media_uploads.save_upload(
                photo_file,
                uploads_dir,
                {"image": max_photo_bytes},
                max_total_storage_bytes=max_total_storage_bytes,
                min_disk_free_bytes=min_disk_free_bytes,
            )
            band_photo_stored_filename = stored_name
            saved_files.append(Path(uploads_dir) / stored_name)
        except media_uploads.UploadRejected as exc:
            logger.warning("Band photo upload rejected: %s", exc)
            abort(400)

    # Optional first release
    has_release = request.form.get("has_release") in ("1", "true", "on", "yes")
    release_name = None
    release_slug = None
    release_date_published = None
    release_genre = None
    release_license = None
    release_description = None
    release_description_en = None
    release_cover_stored_filename = None
    release_tracks_json = None
    tracks_list: list[dict] = []

    if has_release:
        release_name = request.form.get("release_name", "").strip()
        release_date_published = request.form.get("release_date_published", "").strip()
        rel_genre_raw = request.form.get("release_genre", "").strip()
        release_license = (
            request.form.get("release_license", "").strip()
            or "https://creativecommons.org/licenses/by-nc-sa/4.0/"
        )
        release_description = request.form.get("release_description", "").strip() or None
        release_description_en = request.form.get("release_description_en", "").strip() or None

        if not release_name or not release_date_published:
            for p in saved_files:
                p.unlink(missing_ok=True)
            abort(400)

        try:
            release_slug = audio_validation.generate_release_slug(release_name, release_date_published)
        except audio_validation.AudioValidationError:
            for p in saved_files:
                p.unlink(missing_ok=True)
            abort(400)

        release_genre = json.dumps([g.strip() for g in rel_genre_raw.split(",") if g.strip()]) if rel_genre_raw else None

        # Cover upload (optional)
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
                release_cover_stored_filename = stored_name
                saved_files.append(Path(uploads_dir) / stored_name)
            except media_uploads.UploadRejected as exc:
                logger.warning("Cover image upload rejected: %s", exc)
                for p in saved_files:
                    p.unlink(missing_ok=True)
                abort(400)

        # Audio files
        audio_files = [f for f in request.files.getlist("tracks") if f and f.filename]
        if not audio_files:
            for p in saved_files:
                p.unlink(missing_ok=True)
            abort(400)

        total_audio_bytes = 0
        for index, file_storage in enumerate(audio_files):
            try:
                stored_name, content_type, media_type, file_size = media_uploads.save_upload(
                    file_storage,
                    uploads_dir,
                    {"audio": max_track_bytes},
                    max_total_storage_bytes=max_total_storage_bytes,
                    min_disk_free_bytes=min_disk_free_bytes,
                )
            except media_uploads.UploadRejected as exc:
                logger.warning("Track %s upload rejected: %s", file_storage.filename, exc)
                for p in saved_files:
                    p.unlink(missing_ok=True)
                abort(400)

            file_path = Path(uploads_dir) / stored_name
            saved_files.append(file_path)
            total_audio_bytes += file_size
            if total_audio_bytes > max_album_bytes:
                logger.warning("Album total size %d exceeds max %d", total_audio_bytes, max_album_bytes)
                for p in saved_files:
                    p.unlink(missing_ok=True)
                abort(400)

            try:
                probe_info = audio_validation.probe_audio_file(file_path)
            except audio_validation.AudioValidationError as exc:
                logger.warning("ffprobe audio validation failed for %s: %s", file_storage.filename, exc)
                for p in saved_files:
                    p.unlink(missing_ok=True)
                abort(400)

            position = index + 1
            custom_title = request.form.get(f"track_title_{index}", "").strip()
            track_title = custom_title or audio_validation.sanitize_track_title(
                file_storage.filename, probe_info["tags"].get("title")
            )
            track_sha = audio_validation.sha256_of(file_path)

            tracks_list.append(
                {
                    "position": position,
                    "name": track_title,
                    "stored_filename": stored_name,
                    "original_filename": file_storage.filename,
                    "content_type": content_type,
                    "duration": probe_info["duration_iso"],
                    "duration_seconds": probe_info["duration_seconds"],
                    "bitrate": probe_info["bitrate"],
                    "sha256": track_sha,
                    "ai_flags": probe_info["ai_flags"],
                }
            )

        release_tracks_json = json.dumps(tracks_list)

    genre_json = json.dumps([g.strip() for g in genre_raw.split(",") if g.strip()]) if genre_raw else None

    # Stamp approver id if logged in
    session_approver_id = session.get("approver_id") or session.get("user_id")
    submitted_by_approver_id = _resolve_submitted_by_approver_id(
        conn, session_approver_id, submitter_contact
    )

    lang = i18n.get_locale()
    cur = conn.execute(
        """
        INSERT INTO band_proposals (
            name, band_slug, founding_date, dissolution_date, location, genre,
            description, description_en, band_photo_stored_filename,
            has_release, release_name, release_slug, release_date_published,
            release_genre, release_license, release_description, release_description_en,
            release_cover_stored_filename, release_tracks_json,
            submitter_name, submitter_contact, submitter_ip, submitted_by_approver_id, lang
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            band_name,
            band_slug,
            founding_date,
            dissolution_date,
            location,
            genre_json,
            description,
            description_en,
            band_photo_stored_filename,
            1 if has_release else 0,
            release_name,
            release_slug,
            release_date_published,
            release_genre,
            release_license,
            release_description,
            release_description_en,
            release_cover_stored_filename,
            release_tracks_json,
            submitter_name,
            submitter_contact,
            ip,
            submitted_by_approver_id,
            lang,
        ),
    )
    conn.commit()
    proposal_id = cur.lastrowid

    ai_config: AiConfig = current_app.config.get("AI_CONFIG") or AiConfig()
    if ai_config.mode == "disabled":
        try:
            login_url = url_for("auth.login_form", _external=True)
            summary_str = f"New Band: {band_name} ({band_slug})"
            if has_release:
                summary_str += f" with release {release_name} ({len(tracks_list)} tracks)"
            summary = (
                f"New band proposal #{proposal_id} ({summary_str}):\n\n"
                f"Band: {band_name} ({band_slug})\n"
                f"Submitter: {submitter_name or 'anonymous'} <{submitter_contact or 'none'}>\n\n"
                f"Log in to the dashboard to review it: {login_url}"
            )
            recipients = roles.get_approver_recipients(conn, current_app.config.get("MAINTAINER_EMAIL"))
            mail.send_submission_notification(
                current_app.config["SMTP_CONFIG"],
                recipients,
                summary,
            )
        except Exception:
            current_app.logger.exception("failed to send notification email for band proposal %s", proposal_id)
    else:
        # AI evaluation dispatch
        try:
            sync_eval = current_app.config.get("AI_SYNC_EVALUATION", False)
            ai_agent.dispatch_band_evaluation(current_app._get_current_object(), proposal_id, sync=sync_eval)
        except Exception:
            current_app.logger.exception("failed to dispatch AI evaluation for band proposal %s", proposal_id)

    return render_template(
        "submit_add_band_done.html",
        band_name=band_name,
        has_release=has_release,
        release_name=release_name,
        count=len(tracks_list),
    ), 201
