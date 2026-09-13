"""
sqlite3 connection handling, following Flask's own documented pattern
(https://flask.palletsprojects.com/en/latest/tutorial/database/): one
connection per request, opened lazily and cached on `flask.g`, closed
automatically when the request context tears down.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import flask

import roles  # noqa: E402

SCHEMA_SQL = (Path(__file__).resolve().parent / "schema.sql").read_text()


def init_schema(database_path: Path, maintainer_email: str | None = None) -> None:
    """Idempotent: every CREATE TABLE in schema.sql is IF NOT EXISTS, so
    this is safe to run on every app startup, not just the first."""
    database_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(database_path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.executescript(SCHEMA_SQL)
        try:
            conn.execute("ALTER TABLE media_proposals ADD COLUMN github_run_id TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE media_proposals ADD COLUMN publish_error TEXT")
        except sqlite3.OperationalError:
            pass
        for col, col_type in [
            ("ai_decision", "TEXT"),
            ("ai_confidence", "REAL"),
            ("ai_reasoning", "TEXT"),
            ("ai_evaluated_at", "TEXT"),
        ]:
            try:
                conn.execute(f"ALTER TABLE proposals ADD COLUMN {col} {col_type}")
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute(f"ALTER TABLE media_proposals ADD COLUMN {col} {col_type}")
            except sqlite3.OperationalError:
                pass
        conn.commit()
        roles.ensure_default_roles(conn, maintainer_email)
    finally:
        conn.close()


def get_connection() -> sqlite3.Connection:
    if "db_connection" not in flask.g:
        conn = sqlite3.connect(flask.current_app.config["DATABASE_PATH"], timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        flask.g.db_connection = conn
    return flask.g.db_connection


def close_connection(_exception: BaseException | None = None) -> None:
    conn = flask.g.pop("db_connection", None)
    if conn is not None:
        conn.close()


def init_app(app: flask.Flask) -> None:
    init_schema(Path(app.config["DATABASE_PATH"]), app.config.get("MAINTAINER_EMAIL"))
    app.teardown_appcontext(close_connection)
