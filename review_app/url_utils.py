"""
URL generation helpers for review_app.

Ensures absolute URLs (e.g. for emailed magic links and notifications) are
always constructed from the explicit, configured BASE_URL / REVIEW_APP_BASE_URL,
never trusting incoming request Host or X-Forwarded-Host headers (GitHub issue #77).
"""
from __future__ import annotations

import os
from flask import current_app, url_for


def external_url(endpoint: str, **values) -> str:
    """Build an absolute URL using configured BASE_URL, ignoring untrusted host headers."""
    base = (
        (current_app.config.get("BASE_URL") if current_app else None)
        or os.environ.get("REVIEW_APP_BASE_URL")
        or "https://review.daugavpils.fans"
    ).rstrip("/")
    path = url_for(endpoint, **values)
    return f"{base}{path}"
