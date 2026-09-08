"""
The public /submit-media flow (see GitHub issue #21): upload one or more
new photos/videos for an existing band or release. Deliberately separate
from submissions.py's text-edit flow rather than folded into it - unlike
a text proposal, approving one of these never touches git or
archive.org (see the issue's "curated queue, manual finish" design), so
it has its own table (media_proposals) and its own decision routes (see
dashboard.py).
"""
from __future__ import annotations

import sys
from pathlib import Path

from flask import Blueprint, abort, current_app, render_template, request, session, url_for

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(REPO_ROOT / "tools"))

import archive_read  # noqa: E402
import db  # noqa: E402
import mail  # noqa: E402
import media_uploads  # noqa: E402

bp = Blueprint("media_submissions", __name__)


def _resolve_submitted_by_approver_id(
    conn, session_approver_id: int | None, submitter_contact: str | None
) -> int | None:
    """Same rule as submissions.py's own version - kept as an independent
    copy rather than a shared import, since the two tables it stamps
    (proposals vs. media_proposals) are otherwise unrelated and this is
    the only logic between them worth sharing; duplicating ~10 lines
    beats introducing a shared-utility module for just this."""
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


@bp.get("/submit/<band_slug>/media")
def band_media_form(band_slug: str):
    checkout = current_app.config["ARCHIVE_CHECKOUT_PATH"]
    try:
        band = archive_read.get_band(checkout, band_slug)
    except archive_read.ApplyError:
        abort(404)
    return render_template(
        "submit_media.html", band_slug=band_slug, release_slug=None, scope_label=band.name
    )


@bp.get("/submit/<band_slug>/<release_slug>/media")
def release_media_form(band_slug: str, release_slug: str):
    checkout = current_app.config["ARCHIVE_CHECKOUT_PATH"]
    try:
        band = archive_read.get_band(checkout, band_slug)
        release = archive_read.get_release(checkout, band_slug, release_slug)
    except archive_read.ApplyError:
        abort(404)
    return render_template(
        "submit_media.html",
        band_slug=band_slug,
        release_slug=release_slug,
        scope_label=f"{release.name} — {band.name}",
    )


@bp.post("/submit-media")
def create_media_proposal():
    conn = db.get_connection()
    ip = request.remote_addr or "unknown"

    # Same rate-limit mechanism (and the same submission_log table) as
    # submissions.py - one hit per POST regardless of how many files it
    # contains, matching how a batch upload is meant to read as one
    # visit, not N.
    limit = current_app.config["RATE_LIMIT_PER_IP_PER_HOUR"]
    recent = conn.execute(
        "SELECT COUNT(*) FROM submission_log WHERE ip = ? AND submitted_at > datetime('now', '-1 hour')",
        (ip,),
    ).fetchone()[0]
    if recent >= limit:
        abort(429)

    conn.execute("INSERT INTO submission_log (ip) VALUES (?)", (ip,))
    conn.commit()

    if request.form.get("website"):  # honeypot: real users never see/fill this field
        return render_template("submit_media_done.html", saved=0, skipped=[]), 201

    band_slug = request.form.get("band_slug", "")
    release_slug = request.form.get("release_slug") or None

    checkout = current_app.config["ARCHIVE_CHECKOUT_PATH"]
    try:
        if release_slug:
            archive_read.get_release(checkout, band_slug, release_slug)
        else:
            archive_read.get_band(checkout, band_slug)
    except archive_read.ApplyError:
        abort(400)

    files = [f for f in request.files.getlist("files") if f and f.filename]
    if not files:
        abort(400)

    submitter_name = request.form.get("submitter_name", "").strip() or None
    submitter_contact = request.form.get("submitter_contact", "").strip() or None
    submitted_by_approver_id = _resolve_submitted_by_approver_id(
        conn, session.get("approver_id"), submitter_contact
    )

    uploads_dir = current_app.config["MEDIA_UPLOADS_PATH"]
    max_bytes_by_type = current_app.config["MAX_UPLOAD_BYTES"]

    saved = 0
    skipped: list[str] = []
    for index, file_storage in enumerate(files):
        caption = request.form.get(f"caption_{index}", "").strip() or None
        try:
            stored_filename, content_type, media_type, size = media_uploads.save_upload(
                file_storage, uploads_dir, max_bytes_by_type
            )
        except media_uploads.UploadRejected as exc:
            skipped.append(f"{file_storage.filename}: {exc}")
            continue

        conn.execute(
            """
            INSERT INTO media_proposals (
                band_slug, release_slug, media_type, original_filename, stored_filename,
                content_type, size_bytes, caption,
                submitter_name, submitter_contact, submitter_ip,
                submitted_by_approver_id, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
            """,
            (
                band_slug,
                release_slug,
                media_type,
                file_storage.filename,
                stored_filename,
                content_type,
                size,
                caption,
                submitter_name,
                submitter_contact,
                ip,
                submitted_by_approver_id,
            ),
        )
        saved += 1
    conn.commit()

    if saved:
        scope = f"{band_slug}/{release_slug}" if release_slug else band_slug
        login_url = url_for("auth.login_form", _external=True)
        summary = (
            f"{saved} new media proposal(s) for {scope}"
            + (f" ({len(skipped)} skipped - see below)" if skipped else "")
            + f".\n\nLog in to the dashboard to review: {login_url}"
        )
        if skipped:
            summary += "\n\nSkipped:\n" + "\n".join(f"- {s}" for s in skipped)
        # Same stance as submissions.py: the proposals are already
        # durably saved above - a submitter should never see a failure
        # just because the notification couldn't be sent.
        try:
            mail.send_submission_notification(
                current_app.config["SMTP_CONFIG"], [current_app.config["MAINTAINER_EMAIL"]], summary
            )
        except OSError:
            current_app.logger.exception("failed to send submission notification email")

    return render_template("submit_media_done.html", saved=saved, skipped=skipped), 201
