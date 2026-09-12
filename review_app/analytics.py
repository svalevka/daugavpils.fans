"""
Privacy-preserving visitor and media analytics (GitHub issue #34).
Tracks pageviews, audio track listens, and video views without cookies or raw IPs.
"""
from __future__ import annotations

import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from flask import Blueprint, current_app, request

sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402

bp = Blueprint("analytics", __name__)

BOT_PATTERNS = (
    "bot",
    "spider",
    "crawl",
    "slurp",
    "mediapartners",
    "headless",
    "phantom",
    "selenium",
    "lighthouse",
    "postman",
    "curl",
    "wget",
    "python-requests",
    "httpx",
    "aiohttp",
    "go-http-client",
    "urllib",
    "bytespider",
    "gptbot",
    "claude-web",
    "anthropic",
    "semrush",
    "ahrefs",
    "mj12bot",
    "yandexbot",
    "googlebot",
    "bingbot",
    "duckduckbot",
    "baiduspider",
    "sogou",
    "exabot",
    "facebot",
    "facebookexternalhit",
    "ia_archiver",
    "archive.org_bot",
)


def is_bot(user_agent: str | None) -> bool:
    if not user_agent or not user_agent.strip():
        return True
    ua = user_agent.lower()
    return any(pattern in ua for pattern in BOT_PATTERNS)


def hash_visitor(ip: str, user_agent: str, secret_key: str, date_str: str | None = None) -> str:
    """Produces a daily-rotating salted hash. The salt changes every UTC day,
    so unique visitors can be counted within a single day without tracking
    them across days or reconstructing their IP address."""
    if date_str is None:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    salt = hashlib.sha256(f"{secret_key}:{date_str}".encode()).hexdigest()[:16]
    return hashlib.sha256(f"{ip}:{user_agent}:{salt}".encode()).hexdigest()


def detect_device(user_agent: str | None) -> str:
    if not user_agent:
        return "desktop"
    ua = user_agent.lower()
    if any(k in ua for k in ("ipad", "tablet", "playbook", "silk")):
        return "tablet"
    if any(k in ua for k in ("mobile", "android", "iphone", "ipod", "blackberry")):
        return "mobile"
    return "desktop"


def detect_country(headers: dict | None, ip: str | None = None) -> str | None:
    if not headers:
        return None
    for header in ("CF-IPCountry", "X-Country", "X-GeoIP-Country"):
        val = headers.get(header)
        if val and len(val.strip()) == 2:
            return val.strip().upper()
    return None


def parse_referrer(referrer: str | None, current_host: str = "daugavpils.fans") -> str:
    if not referrer or not referrer.strip():
        return "Direct"
    try:
        parsed = urlparse(referrer.strip())
        netloc = parsed.netloc.lower()
        if not netloc:
            return "Direct"
        if current_host in netloc or "localhost" in netloc or "127.0.0.1" in netloc:
            return "Internal"
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc
    except Exception:
        return "Direct"


def parse_path_slugs(path: str) -> tuple[str | None, str | None]:
    parts = [p for p in path.strip("/").split("/") if p]
    if parts and parts[0] in ("en", "ru"):
        parts = parts[1:]
    band_slug = None
    release_slug = None
    if len(parts) >= 2 and parts[0] == "bands":
        band_slug = parts[1]
        if len(parts) >= 3 and parts[2] not in ("media",):
            release_slug = parts[2]
    return band_slug, release_slug


@bp.post("/api/event")
def record_event():
    data = request.get_json(silent=True)
    if not data or not isinstance(data, dict):
        return ("", 204)

    event_type = str(data.get("type", "")).strip()
    if event_type not in ("pageview", "track_play", "video_play"):
        return ("", 204)

    ua = request.user_agent.string or ""
    if is_bot(ua):
        return ("", 204)

    ip = request.remote_addr or "127.0.0.1"
    secret_key = current_app.config.get("SECRET_KEY", "default-salt")
    visitor_hash = hash_visitor(ip, ua, secret_key)
    device = detect_device(ua)
    country = detect_country(request.headers, ip)

    raw_path = str(data.get("path", "/")).strip()
    path = raw_path if raw_path.startswith("/") else f"/{raw_path}"

    band_slug, release_slug = parse_path_slugs(path)
    if not band_slug and data.get("band"):
        band_slug = str(data.get("band")).strip()
    if not release_slug and data.get("release"):
        release_slug = str(data.get("release")).strip()

    track_name = str(data["track"]).strip() if data.get("track") else None
    video_name = str(data["video"]).strip() if data.get("video") else None
    referrer = parse_referrer(str(data.get("referrer", "")))

    conn = db.get_connection()
    conn.execute(
        """INSERT INTO analytics_events (
            event_type, path, band_slug, release_slug, track_name, video_name,
            referrer_domain, country_code, device_type, visitor_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            event_type,
            path,
            band_slug,
            release_slug,
            track_name,
            video_name,
            referrer,
            country,
            device,
            visitor_hash,
        ),
    )
    conn.commit()
    return ("", 204)
