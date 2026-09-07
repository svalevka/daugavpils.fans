"""
review_app's Flask application factory (see GitHub issue #8/#11): the
self-hosted app that lets anyone propose a text edit to the archive and
a curated group of approvers decide on it. create_app() takes an explicit
Config rather than reading the environment itself, so tests can point
each app instance at its own scratch database/checkout - a production
entrypoint (reading Config.from_env(), run via gunicorn) is added by the
deployment ticket (#14), once there's somewhere real to run it.
"""
from __future__ import annotations

import sys
from pathlib import Path

from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
from config import Config  # noqa: E402
from submissions import bp as submissions_bp  # noqa: E402


def create_app(config: Config) -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = config.secret_key
    app.config["DATABASE_PATH"] = config.database_path
    app.config["ARCHIVE_CHECKOUT_PATH"] = config.archive_checkout_path
    app.config["RATE_LIMIT_PER_IP_PER_HOUR"] = config.rate_limit_per_ip_per_hour

    db.init_app(app)
    app.register_blueprint(submissions_bp)

    return app
