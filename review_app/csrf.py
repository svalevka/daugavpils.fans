"""
CSRF protection for review_app.
Generates and validates session-bound tokens for state-changing HTTP requests.
"""
from __future__ import annotations

import hmac
import secrets

from flask import abort, current_app, request, session


def generate_csrf_token() -> str:
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_hex(32)
        session["_csrf_token"] = token
    return token


def init_app(app) -> None:
    app.jinja_env.globals["csrf_token"] = generate_csrf_token

    @app.before_request
    def check_csrf():
        # Only validate state-changing methods
        if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
            return

        # Skip API endpoints authenticated via bearer tokens or analytics event beacons
        if request.blueprint in ("api", "analytics") or request.path.startswith("/api/"):
            return

        # In testing mode, default to disabled unless explicitly enabled
        csrf_enabled = current_app.config.get("CSRF_ENABLED")
        if csrf_enabled is None:
            csrf_enabled = not current_app.config.get("TESTING", False)
        if not csrf_enabled:
            return

        token = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
        expected = session.get("_csrf_token")
        if not expected or not token or not hmac.compare_digest(token, expected):
            abort(400, "CSRF validation failed: missing or invalid CSRF token")
