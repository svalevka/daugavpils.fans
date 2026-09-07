"""
review_app's Flask application factory (see GitHub issue #8/#11): the
self-hosted app that lets anyone propose a text edit to the archive and
a curated group of approvers decide on it. create_app() takes an explicit
Config rather than reading the environment itself, so tests can point
each app instance at its own scratch database/checkout. The production
entrypoint (reading Config.from_env(), run via gunicorn behind nginx) is
wsgi.py - see GitHub issue #14.
"""
from __future__ import annotations

import sys
from pathlib import Path

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
from api import bp as api_bp  # noqa: E402
from auth import bp as auth_bp  # noqa: E402
from config import Config  # noqa: E402
from dashboard import bp as dashboard_bp  # noqa: E402
from submissions import bp as submissions_bp  # noqa: E402


def create_app(config: Config) -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = config.secret_key
    app.config["DATABASE_PATH"] = config.database_path
    app.config["ARCHIVE_CHECKOUT_PATH"] = config.archive_checkout_path
    app.config["RATE_LIMIT_PER_IP_PER_HOUR"] = config.rate_limit_per_ip_per_hour
    app.config["MAINTAINER_EMAIL"] = config.maintainer_email
    app.config["SMTP_CONFIG"] = config.smtp
    app.config["GITHUB_CONFIG"] = config.github
    app.config["REVIEW_APP_CALLBACK_KEY"] = config.callback_key

    db.init_app(app)
    app.register_blueprint(submissions_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(api_bp)

    # Behind nginx (see webapp/deploy/nginx/daugavpils.conf's `proxy_set_header
    # X-Forwarded-*` lines), only one hop of forwarding headers is ever
    # trusted - this is what makes url_for(..., _external=True) generate
    # https://review.daugavpils.fans/... links (auth.py's magic links) and
    # request.remote_addr reflect the real submitter's IP (submissions.py's
    # rate limiting) instead of nginx's own address. A no-op when there's
    # no reverse proxy in front (local dev, tests) - no X-Forwarded-* header
    # ever arrives, so there's nothing to trust.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    return app
