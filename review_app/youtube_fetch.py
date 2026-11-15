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

No transcoding: `_pick_format` selects an already-progressive (single-file,
combined audio+video) mp4/webm format when available, or an adaptive video+audio
stream pair remuxed via ffmpeg (stream copy, no re-encoding) into mp4/webm when
progressive formats are not served. It steps down to a lower-quality format if
the best available exceeds the existing per-video size cap.
"""
from __future__ import annotations

import concurrent.futures
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


YOUTUBE_INFO_TIMEOUT = 30  # seconds
YOUTUBE_DOWNLOAD_TIMEOUT = 180  # seconds


def _base_opts(cookiefile: str | None) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 15,
    }
    if cookiefile:
        # A dedicated account's exported cookies (see GitHub issue #49) -
        # without this, YouTube's bot-check ("Sign in to confirm you're
        # not a bot") increasingly blocks datacenter/VPS IPs like this
        # server's for anything but high-traffic videos. Optional: falls
        # back to cookie-less requests, which still work for popular
        # videos.
        opts["cookiefile"] = cookiefile
    return opts


def _extract_info(
    url: str, cookiefile: str | None, timeout_seconds: int = YOUTUBE_INFO_TIMEOUT
) -> dict[str, Any]:
    def _do_extract():
        opts = _base_opts(cookiefile)
        opts["skip_download"] = True
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_do_extract)
        try:
            return future.result(timeout=timeout_seconds)
        except concurrent.futures.TimeoutError as exc:
            raise FetchError("YouTube metadata extraction timed out") from exc


def _pick_format(info: dict[str, Any], max_bytes: int) -> str:
    """Chooses a format id whose reported size fits within max_bytes,
    preferring the highest quality that fits (see issue #49: auto
    step-down quality rather than reject outright).

    Supports:
    1. Progressive formats (single-file, combined audio+video mp4/webm).
    2. Adaptive stream pairs (separate video-only + audio-only) remuxed into
       mp4/webm without transcoding via ffmpeg stream copy, for videos where
       YouTube no longer provides combined progressive streams.
    """
    formats = info.get("formats", []) or []

    def size_of(f: dict[str, Any]) -> int:
        return f.get("filesize") or f.get("filesize_approx") or 0

    # 1. Progressive candidates (single file containing both video and audio)
    progressive = [
        f
        for f in formats
        if f.get("vcodec") not in (None, "none")
        and f.get("acodec") not in (None, "none")
        and f.get("ext") in _ACCEPTED_EXTS
        and f.get("format_id")
    ]
    sized_prog = [f for f in progressive if size_of(f)]
    if progressive:
        fitting_prog = [f for f in sized_prog if size_of(f) <= max_bytes]
        if fitting_prog:
            return max(fitting_prog, key=size_of)["format_id"]
        elif not sized_prog:
            return progressive[0]["format_id"]
        # If all progressive formats are oversized, fall through to check adaptive
        # streams which might have smaller resolutions/bitrates that fit.

    # 2. Adaptive stream pairs (separate video-only and audio-only streams)
    v_candidates = [
        f
        for f in formats
        if f.get("vcodec") not in (None, "none")
        and f.get("acodec") in (None, "none")
        and f.get("format_id")
    ]
    a_candidates = [
        f
        for f in formats
        if f.get("acodec") not in (None, "none")
        and f.get("vcodec") in (None, "none")
        and f.get("format_id")
    ]

    # Try MP4 video + M4A audio first (universal browser compatibility)
    v_mp4 = [
        v
        for v in v_candidates
        if v.get("ext") == "mp4" or (v.get("vcodec") or "").startswith(("avc", "h264"))
    ]
    a_m4a = [
        a
        for a in a_candidates
        if a.get("ext") in ("m4a", "mp4") or (a.get("acodec") or "").startswith(("mp4a", "aac"))
    ]

    # Fall back to WebM video + WebM audio
    v_webm = [
        v
        for v in v_candidates
        if v.get("ext") == "webm" or (v.get("vcodec") or "").startswith(("vp9", "vp8", "av01"))
    ]
    a_webm = [
        a
        for a in a_candidates
        if a.get("ext") in ("webm", "m4a") or (a.get("acodec") or "").startswith(("opus", "vorbis"))
    ]

    pairs: list[dict[str, Any]] = []
    if v_mp4 and a_m4a:
        best_a = max(a_m4a, key=lambda a: a.get("abr") or a.get("tbr") or size_of(a))
        a_size = size_of(best_a)
        for v in v_mp4:
            pairs.append({
                "format_id": f"{v['format_id']}+{best_a['format_id']}",
                "size": size_of(v) + a_size,
                "height": v.get("height") or 0,
                "is_avc": (v.get("vcodec") or "").startswith(("avc", "h264")),
                "ext": "mp4",
            })
    elif v_webm and a_webm:
        best_a = max(a_webm, key=lambda a: a.get("abr") or a.get("tbr") or size_of(a))
        a_size = size_of(best_a)
        for v in v_webm:
            pairs.append({
                "format_id": f"{v['format_id']}+{best_a['format_id']}",
                "size": size_of(v) + a_size,
                "height": v.get("height") or 0,
                "is_avc": False,
                "ext": "webm",
            })

    if pairs:
        sized_pairs = [p for p in pairs if p["size"] > 0]
        fitting_pairs = [p for p in sized_pairs if p["size"] <= max_bytes]
        if fitting_pairs:
            # Pick highest resolution, preferring AVC/H.264 if heights match, then highest size
            chosen = max(fitting_pairs, key=lambda p: (p["height"], p["is_avc"], p["size"]))
            return chosen["format_id"]
        elif sized_pairs:
            # If all adaptive pairs are oversized, pick the smallest one
            smallest_pair = min(sized_pairs, key=lambda p: p["size"])
            if progressive and sized_prog:
                smallest_prog = min(sized_prog, key=size_of)
                if size_of(smallest_prog) <= smallest_pair["size"]:
                    return smallest_prog["format_id"]
            return smallest_pair["format_id"]
        return pairs[0]["format_id"]

    # If progressive was available but all oversized and no adaptive pairs could be formed:
    if progressive and sized_prog:
        return min(sized_prog, key=size_of)["format_id"]
    elif progressive:
        return progressive[0]["format_id"]

    raise FetchError("no combined audio/video format available for this video")


def _download(
    url: str,
    format_id: str,
    dest_dir: Path,
    max_bytes: int,
    cookiefile: str | None,
    timeout_seconds: int = YOUTUBE_DOWNLOAD_TIMEOUT,
) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(16)
    outtmpl = str(dest_dir / f"{token}.%(ext)s")

    def _do_download():
        opts = _base_opts(cookiefile)
        opts.update({"format": format_id, "outtmpl": outtmpl, "max_filesize": max_bytes})
        if "+" in format_id:
            opts["merge_output_format"] = "mp4"
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_do_download)
        try:
            future.result(timeout=timeout_seconds)
        except concurrent.futures.TimeoutError as exc:
            for f in dest_dir.glob(f"{token}.*"):
                f.unlink(missing_ok=True)
            raise FetchError("YouTube video download timed out") from exc
        except Exception:
            for f in dest_dir.glob(f"{token}.*"):
                f.unlink(missing_ok=True)
            raise

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
        cookies_path = app.config.get("YOUTUBE_COOKIES_PATH")
        cookiefile = str(cookies_path) if cookies_path else None

        try:
            info = _extract_info(url, cookiefile)
            if info.get("is_live") or info.get("live_status") == "is_live":
                raise FetchError("this is a live stream, not a finished video")
            format_id = _pick_format(info, max_bytes)
            downloaded_path = _download(url, format_id, uploads_dir, max_bytes, cookiefile)
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
                youtube_title = ?, youtube_channel = ?, youtube_duration_seconds = ?,
                duration_seconds = ?
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
        if ai_config.effective_media_mode == "disabled":
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
