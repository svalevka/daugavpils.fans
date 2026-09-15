"""
Fetches a video from a submitted YouTube URL for the /submit-media flow's
YouTube-link path (see GitHub issue #49). Runs in a background thread (the
same dispatch pattern ai_agent.dispatch_evaluation already uses, for the
same reason: a multi-minute download can't block the request that created
the proposal), downloads it via yt-dlp, stores it on disk exactly like a
direct file upload would (media_uploads.save_upload's sibling for this
source), and then hands the media_proposals row off to the ordinary
AI-review/approve/apply pipeline unchanged - nothing downstream of a
'pending' row needs to know or care where the file came from.

No transcoding: `_pick_format` only ever selects an already-progressive
(single-file, combined audio+video) mp4/webm format - the same containers
media_uploads.py already accepts from direct uploads - and steps down to a
lower-quality one if the best available exceeds the existing per-video
size cap, rather than merging separate video-only/audio-only streams or
re-encoding anything.
"""
from __future__ import annotations

import logging
import re
import secrets
import sys
import threading
from pathlib import Path
from typing import Any

import yt_dlp

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ai_agent  # noqa: E402
import db  # noqa: E402
import mail  # noqa: E402
import roles  # noqa: E402
from config import AiConfig  # noqa: E402

logger = logging.getLogger(__name__)

# youtube.com/watch?v=, youtu.be/, youtube.com/shorts/ - deliberately not
# trying to cover every historical YouTube URL shape, just the ones a
# submitter pasting a link out of their browser or the YouTube app will
# actually produce.
YOUTUBE_URL_RE = re.compile(
    r"^https?://(www\.|m\.)?(youtube\.com/(watch\?v=|shorts/)|youtu\.be/)[\w-]{6,}",
    re.IGNORECASE,
)

_ACCEPTED_EXTS = ("mp4", "webm")


class FetchError(Exception):
    """A YouTube video could not be fetched - caught by fetch_and_store
    and turned into a failure notification, never left to propagate and
    silently kill the background thread."""


def is_youtube_url(url: str) -> bool:
    return bool(YOUTUBE_URL_RE.match((url or "").strip()))


def _extract_info(url: str) -> dict[str, Any]:
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


def _pick_format(info: dict[str, Any], max_bytes: int) -> str:
    """Chooses a format id whose reported size fits within max_bytes,
    preferring the highest quality that fits (see issue #49: auto
    step-down quality rather than reject outright). Falls back to the
    smallest available progressive format if no size is reported for
    any candidate - the actual downloaded bytes are still capped by
    yt-dlp's own max_filesize option in _download as a belt-and-suspenders
    check against a format whose reported size was wrong or missing."""
    candidates = [
        f
        for f in info.get("formats", []) or []
        if f.get("vcodec") not in (None, "none")
        and f.get("acodec") not in (None, "none")
        and f.get("ext") in _ACCEPTED_EXTS
        and f.get("format_id")
    ]
    if not candidates:
        raise FetchError("no combined audio/video format available for this video")

    def size_of(f: dict[str, Any]) -> int:
        return f.get("filesize") or f.get("filesize_approx") or 0

    sized = [f for f in candidates if size_of(f)]
    fitting = [f for f in sized if size_of(f) <= max_bytes]
    if fitting:
        chosen = max(fitting, key=size_of)
    elif sized:
        chosen = min(sized, key=size_of)
    else:
        chosen = candidates[0]
    return chosen["format_id"]


def _download(url: str, format_id: str, dest_dir: Path, max_bytes: int) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(16)
    outtmpl = str(dest_dir / f"{token}.%(ext)s")
    opts = {
        "format": format_id,
        "outtmpl": outtmpl,
        "max_filesize": max_bytes,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])

    produced = sorted(dest_dir.glob(f"{token}.*"))
    if not produced:
        raise FetchError("download reported success but no file was produced")
    return produced[0]


def _sniff_downloaded(path: Path) -> tuple[str, str]:
    """(content_type, media_type) from the downloaded file's own
    extension - yt-dlp only ever produces the container it was told to
    (_pick_format restricts candidates to mp4/webm), so this doesn't need
    media_uploads.py's full byte-sniffing, just the extension it wrote."""
    ext = path.suffix.lower()
    if ext == ".mp4":
        return "video/mp4", "video"
    if ext == ".webm":
        return "video/webm", "video"
    raise FetchError(f"unexpected downloaded file extension: {ext}")


def fetch_and_store(app, media_proposal_id: int) -> None:
    with app.app_context():
        conn = db.get_connection()
        row = conn.execute(
            "SELECT * FROM media_proposals WHERE id = ? AND status = 'fetching'",
            (media_proposal_id,),
        ).fetchone()
        if row is None:
            return
        proposal = dict(row)
        url = proposal["source_url"]
        uploads_dir = Path(app.config["MEDIA_UPLOADS_PATH"])
        max_bytes = app.config["MAX_UPLOAD_BYTES"]["video"]

        try:
            info = _extract_info(url)
            if info.get("is_live") or info.get("live_status") == "is_live":
                raise FetchError("this is a live stream, not a finished video")
            format_id = _pick_format(info, max_bytes)
            downloaded_path = _download(url, format_id, uploads_dir, max_bytes)
            content_type, media_type = _sniff_downloaded(downloaded_path)
            size_bytes = downloaded_path.stat().st_size
        except Exception as exc:  # yt_dlp raises its own broad DownloadError, etc.
            logger.warning("YouTube fetch failed for media proposal %s: %s", media_proposal_id, exc)
            _handle_fetch_failure(app, conn, proposal, str(exc))
            return

        title = info.get("title") or url
        conn.execute(
            """
            UPDATE media_proposals
            SET status = 'pending', original_filename = ?, stored_filename = ?,
                content_type = ?, media_type = ?, size_bytes = ?,
                youtube_title = ?, youtube_channel = ?, youtube_duration_seconds = ?
            WHERE id = ? AND status = 'fetching'
            """,
            (
                title,
                downloaded_path.name,
                content_type,
                media_type,
                size_bytes,
                info.get("title"),
                info.get("uploader") or info.get("channel"),
                info.get("duration"),
                media_proposal_id,
            ),
        )
        conn.commit()

        # Mirror media_submissions.create_media_proposal's own AI-disabled
        # vs. AI-enabled branch (that code never runs for this path, since
        # the row wasn't 'pending' yet when the request that created it
        # returned) - otherwise a fetched video would silently sit
        # 'pending' forever with nobody notified when AI review is off.
        ai_config: AiConfig = app.config.get("AI_CONFIG") or AiConfig()
        if ai_config.mode == "disabled":
            scope = (
                f"{proposal['band_slug']}/{proposal['release_slug']}"
                if proposal.get("release_slug")
                else proposal["band_slug"]
            )
            base_url = (app.config.get("BASE_URL") or "").rstrip("/")
            summary = (
                f"1 new media proposal for {scope} (YouTube: {title})\n\n"
                f"Log in to the dashboard to review: {base_url}/login"
            )
            try:
                recipients = roles.get_approver_recipients(conn, app.config.get("MAINTAINER_EMAIL"))
                mail.send_submission_notification(app.config["SMTP_CONFIG"], recipients, summary)
            except OSError:
                logger.exception(
                    "failed to send submission notification email for proposal %s", media_proposal_id
                )
            return

    ai_agent.dispatch_evaluation(app, media_proposal_id, is_media=True)


def _handle_fetch_failure(app, conn, proposal: dict[str, Any], error: str) -> None:
    """Nothing was fetched, so there's nothing left to review - the row
    is removed rather than surfaced as a persistent 'fetch_failed' state
    (see issue #49). Both the submitter (if a contact was given) and the
    approvers are notified, since a submitter can't tell from silence
    whether their link is still queued or was quietly dropped."""
    conn.execute("DELETE FROM media_proposals WHERE id = ? AND status = 'fetching'", (proposal["id"],))
    conn.commit()

    scope = (
        f"{proposal['band_slug']}/{proposal['release_slug']}"
        if proposal.get("release_slug")
        else proposal["band_slug"]
    )

    if proposal.get("submitter_contact"):
        try:
            mail.send_youtube_fetch_failed_notification(
                app.config["SMTP_CONFIG"], proposal["submitter_contact"], scope, error
            )
        except OSError:
            logger.exception(
                "failed to send fetch-failure notification to submitter for proposal %s",
                proposal["id"],
            )

    try:
        recipients = roles.get_approver_recipients(conn, app.config.get("MAINTAINER_EMAIL"))
        mail.send_youtube_fetch_failed_admin_notification(
            app.config["SMTP_CONFIG"], recipients, scope, proposal["source_url"], error
        )
    except OSError:
        logger.exception(
            "failed to send fetch-failure notification to approvers for proposal %s",
            proposal["id"],
        )


def dispatch_fetch(app, media_proposal_id: int, sync: bool = False) -> None:
    """Dispatches the fetch either synchronously or in a background
    thread - same is_sync test hook shape as ai_agent.dispatch_evaluation."""
    is_sync = sync or bool(app.config.get("TESTING") and app.config.get("YOUTUBE_SYNC_FETCH"))
    if is_sync:
        fetch_and_store(app, media_proposal_id)
    else:
        thread = threading.Thread(target=fetch_and_store, args=(app, media_proposal_id), daemon=True)
        thread.start()
