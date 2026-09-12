"""
Admin statistics dashboard and authentication for site maintainer (GitHub issue #34).
Accessible at /admin/, protected by passwordless magic link to MAINTAINER_EMAIL.
"""
from __future__ import annotations

import hashlib
import secrets
import sys
from pathlib import Path

from flask import (
    Blueprint,
    abort,
    current_app,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
import mail  # noqa: E402

bp = Blueprint("admin", __name__, url_prefix="/admin")


def _is_admin() -> bool:
    return session.get("is_admin") is True


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@bp.get("/")
def dashboard():
    if not _is_admin():
        return redirect(url_for("admin.login_form"))

    period = request.args.get("period", "7d")
    if period not in ("today", "7d", "30d", "all"):
        period = "7d"

    if period == "today":
        time_filter = "created_at >= datetime('now', 'start of day')"
    elif period == "7d":
        time_filter = "created_at >= datetime('now', '-7 days')"
    elif period == "30d":
        time_filter = "created_at >= datetime('now', '-30 days')"
    else:
        time_filter = "1=1"

    conn = db.get_connection()

    # Summary metrics
    unique_visitors = conn.execute(
        f"SELECT COUNT(DISTINCT visitor_hash) FROM analytics_events WHERE {time_filter}"
    ).fetchone()[0]

    pageviews = conn.execute(
        f"SELECT COUNT(*) FROM analytics_events WHERE {time_filter} AND event_type = 'pageview'"
    ).fetchone()[0]

    audio_plays = conn.execute(
        f"SELECT COUNT(*) FROM analytics_events WHERE {time_filter} AND event_type = 'track_play'"
    ).fetchone()[0]

    video_views = conn.execute(
        f"SELECT COUNT(*) FROM analytics_events WHERE {time_filter} AND event_type = 'video_play'"
    ).fetchone()[0]

    # Top tracks
    top_tracks_rows = conn.execute(
        f"""SELECT track_name, band_slug, release_slug, COUNT(*) as play_count
            FROM analytics_events
            WHERE {time_filter} AND event_type = 'track_play' AND track_name IS NOT NULL AND track_name != ''
            GROUP BY track_name, band_slug, release_slug
            ORDER BY play_count DESC
            LIMIT 10"""
    ).fetchall()
    max_track_plays = top_tracks_rows[0]["play_count"] if top_tracks_rows else 1
    top_tracks = [
        {
            "name": row["track_name"],
            "band": row["band_slug"],
            "release": row["release_slug"],
            "count": row["play_count"],
            "pct": min(100, int((row["play_count"] / max_track_plays) * 100)),
        }
        for row in top_tracks_rows
    ]

    # Top videos
    top_videos_rows = conn.execute(
        f"""SELECT video_name, band_slug, COUNT(*) as view_count
            FROM analytics_events
            WHERE {time_filter} AND event_type = 'video_play' AND video_name IS NOT NULL AND video_name != ''
            GROUP BY video_name, band_slug
            ORDER BY view_count DESC
            LIMIT 10"""
    ).fetchall()
    max_video_views = top_videos_rows[0]["view_count"] if top_videos_rows else 1
    top_videos = [
        {
            "name": row["video_name"],
            "band": row["band_slug"],
            "count": row["view_count"],
            "pct": min(100, int((row["view_count"] / max_video_views) * 100)),
        }
        for row in top_videos_rows
    ]

    # Top pages
    top_pages_rows = conn.execute(
        f"""SELECT path, band_slug, COUNT(*) as view_count
            FROM analytics_events
            WHERE {time_filter} AND event_type = 'pageview'
            GROUP BY path, band_slug
            ORDER BY view_count DESC
            LIMIT 10"""
    ).fetchall()
    max_page_views = top_pages_rows[0]["view_count"] if top_pages_rows else 1
    top_pages = [
        {
            "path": row["path"],
            "band": row["band_slug"],
            "count": row["view_count"],
            "pct": min(100, int((row["view_count"] / max_page_views) * 100)),
        }
        for row in top_pages_rows
    ]

    # Top referrers
    top_referrers = conn.execute(
        f"""SELECT referrer_domain, COUNT(*) as count
            FROM analytics_events
            WHERE {time_filter} AND referrer_domain NOT IN ('Direct', 'Internal') AND referrer_domain IS NOT NULL AND referrer_domain != ''
            GROUP BY referrer_domain
            ORDER BY count DESC
            LIMIT 10"""
    ).fetchall()

    # Top countries
    top_countries = conn.execute(
        f"""SELECT COALESCE(country_code, 'Unknown') as country, COUNT(*) as count
            FROM analytics_events
            WHERE {time_filter}
            GROUP BY country
            ORDER BY count DESC
            LIMIT 10"""
    ).fetchall()

    # Languages
    ru_views = conn.execute(
        f"SELECT COUNT(*) FROM analytics_events WHERE {time_filter} AND event_type = 'pageview' AND path NOT LIKE '/en/%'"
    ).fetchone()[0]
    en_views = conn.execute(
        f"SELECT COUNT(*) FROM analytics_events WHERE {time_filter} AND event_type = 'pageview' AND path LIKE '/en/%'"
    ).fetchone()[0]

    # Device breakdown
    device_rows = conn.execute(
        f"""SELECT device_type, COUNT(*) as count
            FROM analytics_events
            WHERE {time_filter} AND event_type = 'pageview'
            GROUP BY device_type"""
    ).fetchall()
    devices = {row["device_type"]: row["count"] for row in device_rows}

    # Recent activity
    recent_activity = conn.execute(
        f"""SELECT id, event_type, path, track_name, video_name, band_slug, country_code, created_at
            FROM analytics_events
            ORDER BY id DESC
            LIMIT 15"""
    ).fetchall()

    return render_template(
        "admin/dashboard.html",
        period=period,
        unique_visitors=unique_visitors,
        pageviews=pageviews,
        audio_plays=audio_plays,
        video_views=video_views,
        top_tracks=top_tracks,
        top_videos=top_videos,
        top_pages=top_pages,
        top_referrers=top_referrers,
        top_countries=top_countries,
        ru_views=ru_views,
        en_views=en_views,
        devices=devices,
        recent_activity=recent_activity,
        maintainer_email=current_app.config.get("MAINTAINER_EMAIL", ""),
    )


@bp.get("/login")
def login_form():
    if _is_admin():
        return redirect(url_for("admin.dashboard"))
    return render_template("admin/login.html")


@bp.post("/login")
def login_request():
    conn = db.get_connection()
    ip = request.remote_addr or "unknown"

    limit = current_app.config["LOGIN_RATE_LIMIT_PER_IP_PER_HOUR"]
    recent = conn.execute(
        "SELECT COUNT(*) FROM admin_login_request_log WHERE ip = ? AND requested_at > datetime('now', '-1 hour')",
        (ip,),
    ).fetchone()[0]
    if recent >= limit:
        abort(429)

    conn.execute("INSERT INTO admin_login_request_log (ip) VALUES (?)", (ip,))
    conn.commit()

    email = request.form.get("email", "").strip()
    maintainer_email = current_app.config.get("MAINTAINER_EMAIL", "").strip()

    # Response is identical whether or not email matches MAINTAINER_EMAIL
    if email and maintainer_email and email.lower() == maintainer_email.lower():
        token = secrets.token_urlsafe(32)
        conn.execute(
            "INSERT INTO admin_magic_links (email, token_hash, expires_at, requested_ip) "
            "VALUES (?, ?, datetime('now', '+15 minutes'), ?)",
            (email, _hash_token(token), request.remote_addr),
        )
        conn.commit()
        link_url = url_for("admin.verify", token=token, _external=True)
        try:
            mail.send_admin_magic_link(current_app.config["SMTP_CONFIG"], email, link_url)
        except OSError:
            current_app.logger.exception("failed to send admin magic link email")

    return render_template("admin/login_sent.html")


@bp.get("/verify")
def verify():
    token = request.args.get("token", "")
    conn = db.get_connection()

    cur = conn.execute(
        "UPDATE admin_magic_links SET used_at = datetime('now') "
        "WHERE token_hash = ? AND used_at IS NULL AND expires_at > datetime('now')",
        (_hash_token(token),),
    )
    conn.commit()
    if cur.rowcount != 1:
        return render_template("admin/login_invalid.html"), 400

    session["is_admin"] = True
    return redirect(url_for("admin.dashboard"))


@bp.post("/logout")
def logout():
    session.pop("is_admin", None)
    return redirect(url_for("admin.login_form"))
