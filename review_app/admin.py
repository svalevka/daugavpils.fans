"""
Admin statistics dashboard, RBAC and user management for site maintainers (GitHub issues #34 and #35).
Accessible at /admin/, protected by passwordless magic links.
Roles:
  - 'admin': superuser; can manage users and roles, view stats, and approve proposals.
  - 'changes-approver': can review/approve community proposals.
  - 'viewer-stats': can view site statistics on /admin/.
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
import roles  # noqa: E402

bp = Blueprint("admin", __name__, url_prefix="/admin")


def _is_admin() -> bool:
    user_id = session.get("user_id")
    if user_id is not None:
        conn = db.get_connection()
        user = conn.execute("SELECT id, email, is_active FROM approvers WHERE id = ?", (user_id,)).fetchone()
        if not user or not user["is_active"]:
            session.clear()
            return False
        maintainer_email = current_app.config.get("MAINTAINER_EMAIL", "").strip().lower()
        if maintainer_email and user["email"].lower() == maintainer_email:
            return True
        user_roles = roles.get_user_roles(conn, user_id)
        return roles.ROLE_ADMIN in user_roles
    return session.get("is_admin") is True


def _can_view_stats() -> bool:
    if _is_admin():
        return True
    user_id = session.get("user_id")
    if user_id is not None:
        conn = db.get_connection()
        user = conn.execute("SELECT id, email, is_active FROM approvers WHERE id = ?", (user_id,)).fetchone()
        if not user or not user["is_active"]:
            session.clear()
            return False
        user_roles = roles.get_user_roles(conn, user_id)
        return roles.ROLE_STATS in user_roles or roles.ROLE_ADMIN in user_roles
    user_roles = set(session.get("roles", []))
    return roles.ROLE_STATS in user_roles or roles.ROLE_ADMIN in user_roles


def _can_review() -> bool:
    if _is_admin():
        return True
    user_id = session.get("user_id")
    if user_id is not None:
        conn = db.get_connection()
        user = conn.execute("SELECT id, email, is_active FROM approvers WHERE id = ?", (user_id,)).fetchone()
        if not user or not user["is_active"]:
            session.clear()
            return False
        user_roles = roles.get_user_roles(conn, user_id)
        return roles.ROLE_APPROVER in user_roles or roles.ROLE_ADMIN in user_roles
    user_roles = set(session.get("roles", []))
    return roles.ROLE_APPROVER in user_roles or roles.ROLE_ADMIN in user_roles


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@bp.get("/")
def dashboard():
    if not _can_view_stats():
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

    media_errors = conn.execute(
        f"SELECT COUNT(*) FROM analytics_events WHERE {time_filter} AND event_type = 'media_error'"
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
        active_tab="stats",
        period=period,
        unique_visitors=unique_visitors,
        pageviews=pageviews,
        audio_plays=audio_plays,
        video_views=video_views,
        media_errors=media_errors,
        top_tracks=top_tracks,
        top_videos=top_videos,
        top_pages=top_pages,
        top_referrers=top_referrers,
        top_countries=top_countries,
        ru_views=ru_views,
        en_views=en_views,
        devices=devices,
        recent_activity=recent_activity,
        is_admin=_is_admin(),
        can_review=_can_review(),
        user_email=session.get("email", current_app.config.get("MAINTAINER_EMAIL", "")),
        maintainer_email=current_app.config.get("MAINTAINER_EMAIL", ""),
    )


@bp.get("/users")
@bp.get("/users/")
def users_list():
    if not _is_admin():
        if not _can_view_stats():
            return redirect(url_for("admin.login_form"))
        abort(403)

    conn = db.get_connection()
    users = roles.get_all_users_with_roles(conn)
    maintainer_email = current_app.config.get("MAINTAINER_EMAIL", "").strip().lower()
    current_user_id = session.get("user_id")

    return render_template(
        "admin/users.html",
        active_tab="users",
        users=users,
        maintainer_email=maintainer_email,
        current_user_id=current_user_id,
        is_admin=True,
        can_review=_can_review(),
        all_roles=roles.ALL_ROLES,
        role_labels=roles.ROLE_LABELS,
        user_email=session.get("email", maintainer_email),
    )


@bp.post("/users/add")
def user_add():
    if not _is_admin():
        abort(403)

    email = request.form.get("email", "").strip().lower()
    display_name = request.form.get("display_name", "").strip()
    selected_roles = [r for r in request.form.getlist("roles") if r in roles.ALL_ROLES]
    if not selected_roles:
        selected_roles = [roles.ROLE_APPROVER]

    if not email or "@" not in email or not display_name:
        return redirect(url_for("admin.users_list"))

    conn = db.get_connection()
    user = conn.execute(
        "SELECT id FROM approvers WHERE LOWER(email) = LOWER(?)", (email,)
    ).fetchone()

    if user is not None:
        user_id = user["id"]
        conn.execute(
            "UPDATE approvers SET display_name = ?, is_active = 1 WHERE id = ?",
            (display_name, user_id),
        )
    else:
        cur = conn.execute(
            "INSERT INTO approvers (email, display_name, is_active) VALUES (?, ?, 1)",
            (email, display_name),
        )
        user_id = cur.lastrowid

    roles.set_user_roles(conn, user_id, selected_roles)
    conn.commit()

    token = secrets.token_urlsafe(32)
    if roles.ROLE_ADMIN not in selected_roles and roles.ROLE_STATS not in selected_roles and roles.ROLE_APPROVER in selected_roles:
        conn.execute(
            "INSERT INTO magic_links (approver_id, token_hash, expires_at, requested_ip) "
            "VALUES (?, ?, datetime('now', '+15 minutes'), ?)",
            (user_id, _hash_token(token), request.remote_addr),
        )
        conn.commit()
        login_url = url_for("auth.verify", token=token, _external=True)
        if "daugavpils.fans" in login_url and "review.daugavpils.fans" not in login_url:
            login_url = login_url.replace("daugavpils.fans", "review.daugavpils.fans")
    else:
        conn.execute(
            "INSERT INTO admin_magic_links (email, token_hash, expires_at, requested_ip) "
            "VALUES (?, ?, datetime('now', '+15 minutes'), ?)",
            (email, _hash_token(token), request.remote_addr),
        )
        conn.commit()
        login_url = url_for("admin.verify", token=token, _external=True)

    try:
        mail.send_welcome_invitation(
            current_app.config["SMTP_CONFIG"],
            email,
            display_name,
            [roles.ROLE_LABELS.get(r, r) for r in selected_roles],
            login_url,
        )
    except OSError:
        current_app.logger.exception("failed to send welcome invitation email")

    return redirect(url_for("admin.users_list"))


@bp.post("/users/<int:user_id>/roles")
def user_update_roles(user_id: int):
    if not _is_admin():
        abort(403)

    conn = db.get_connection()
    user = conn.execute("SELECT id, email FROM approvers WHERE id = ?", (user_id,)).fetchone()
    if user is None:
        abort(404)

    email = user["email"].lower()
    maintainer_email = current_app.config.get("MAINTAINER_EMAIL", "").strip().lower()
    current_user_id = session.get("user_id")

    selected_roles = set(request.form.getlist("roles"))

    # Safety check 1: maintainer email can never lose admin role
    if email == maintainer_email:
        selected_roles.add(roles.ROLE_ADMIN)

    # Safety check 2: current admin cannot remove their own admin role
    if user_id == current_user_id:
        selected_roles.add(roles.ROLE_ADMIN)

    roles.set_user_roles(conn, user_id, selected_roles)
    conn.commit()

    # Update session if editing self
    if user_id == current_user_id:
        session["roles"] = list(selected_roles)
        session["is_admin"] = roles.ROLE_ADMIN in selected_roles

    return redirect(url_for("admin.users_list"))


@bp.post("/users/<int:user_id>/toggle-active")
def user_toggle_active(user_id: int):
    if not _is_admin():
        abort(403)

    conn = db.get_connection()
    user = conn.execute("SELECT id, email, is_active FROM approvers WHERE id = ?", (user_id,)).fetchone()
    if user is None:
        abort(404)

    email = user["email"].lower()
    maintainer_email = current_app.config.get("MAINTAINER_EMAIL", "").strip().lower()
    current_user_id = session.get("user_id")

    # Safety check: Cannot deactivate oneself or MAINTAINER_EMAIL
    if user_id == current_user_id or email == maintainer_email:
        abort(400)

    new_status = 0 if user["is_active"] else 1
    conn.execute("UPDATE approvers SET is_active = ? WHERE id = ?", (new_status, user_id))
    conn.commit()

    return redirect(url_for("admin.users_list"))


@bp.get("/login")
def login_form():
    if _can_view_stats():
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

    email = request.form.get("email", "").strip().lower()
    maintainer_email = current_app.config.get("MAINTAINER_EMAIL", "").strip().lower()

    user = conn.execute(
        "SELECT id, email FROM approvers WHERE is_active = 1 AND LOWER(email) = LOWER(?)",
        (email,),
    ).fetchone()

    is_maintainer = bool(email and maintainer_email and email == maintainer_email)
    is_authorized = False

    if is_maintainer:
        is_authorized = True
    elif user is not None:
        user_roles = roles.get_user_roles(conn, user["id"])
        if roles.ROLE_ADMIN in user_roles or roles.ROLE_STATS in user_roles:
            is_authorized = True

    # Uniform response to prevent email probing
    if is_authorized:
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

    row = conn.execute(
        "SELECT email FROM admin_magic_links WHERE token_hash = ?", (_hash_token(token),)
    ).fetchone()
    email = row["email"].lower()
    maintainer_email = current_app.config.get("MAINTAINER_EMAIL", "").strip().lower()
    is_maintainer = bool(email == maintainer_email)

    user = conn.execute(
        "SELECT id, email FROM approvers WHERE is_active = 1 AND LOWER(email) = LOWER(?)", (email,)
    ).fetchone()

    if user is None and is_maintainer:
        cur = conn.execute(
            "INSERT INTO approvers (email, display_name, is_active) VALUES (?, 'Site Maintainer', 1)",
            (email,),
        )
        user_id = cur.lastrowid
        roles.set_user_roles(conn, user_id, [roles.ROLE_ADMIN])
        conn.commit()
    elif user is not None:
        user_id = user["id"]
    else:
        return render_template("admin/login_invalid.html"), 400

    user_roles = roles.get_user_roles(conn, user_id)
    if is_maintainer:
        user_roles.add(roles.ROLE_ADMIN)

    session["user_id"] = user_id
    session["approver_id"] = user_id
    session["email"] = email
    session["roles"] = list(user_roles)
    session["is_admin"] = roles.ROLE_ADMIN in user_roles or is_maintainer

    if roles.ROLE_ADMIN in user_roles or roles.ROLE_STATS in user_roles or is_maintainer:
        return redirect(url_for("admin.dashboard"))
    return redirect(url_for("dashboard.view_pending"))


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("admin.login_form"))
