"""
review_app's runtime configuration - env vars in production, explicit
constructor args in tests (so each test can point at its own scratch
database/checkout without racing other tests or touching real state).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SmtpConfig:
    """The handful of SMTP fields travel together everywhere they're
    used (mail.py's _send(), Config below) - given its own type rather
    than passed around as a bare dict, the same way submissions.py gives
    TargetOption its own type instead of a tuple."""

    host: str
    port: int
    from_addr: str
    user: str | None = None
    password: str | None = None


@dataclass(frozen=True)
class GithubConfig:
    """What github_dispatch.py needs to trigger apply-proposal.yml (see
    GitHub issue #13). `token` is a fine-grained PAT scoped to
    Actions: write only - it can start a workflow run, nothing more;
    review_app never holds any credential that can push to git, only the
    triggered Action's own per-run token does that."""

    token: str
    repo: str  # "owner/name"
    workflow_file: str = "apply-proposal.yml"
    ref: str = "main"


@dataclass
class Config:
    database_path: Path
    archive_checkout_path: Path
    secret_key: str
    maintainer_email: str
    smtp: SmtpConfig
    github: GithubConfig
    callback_key: str
    # 5 (the previous default) turned out too low for legitimate use: the
    # /submit flow is one field per POST (see submissions.py's module
    # docstring - there's no batched multi-field submission), so a
    # visitor correcting more than a handful of fields in one sitting hit
    # this as an outright block (GitHub issue #26 - a real user got a 429
    # after their 6th edit). Raised well above realistic spam-bot volume
    # tolerance is still the goal here, just not so tight it blocks a
    # genuine multi-field editing session.
    rate_limit_per_ip_per_hour: int = 20
    # 5 (the original value here) hit the same problem issue #26 already
    # found for rate_limit_per_ip_per_hour above: a real approver testing
    # or re-requesting a link a few times in a row (wrong email, expired
    # link, a dev session, ...) got locked out with a 429. Matched to the
    # /submit limit rather than left tighter - a flood here is still only
    # an inbox-spam/probing vector, not a credential-stuffing one (there's
    # no password to brute-force), and login_request_log counts every
    # attempt including non-matching emails, so this still bounds it well
    # under real spam-bot volume.
    login_rate_limit_per_ip_per_hour: int = 20
    # Where uploaded photos/videos wait for a maintainer to scp/rsync them
    # off before publishing (see GitHub issue #21 - "curated queue, manual
    # finish": review_app never uploads to archive.org itself). Defaults
    # to a sibling of the SQLite database's own directory, so a fresh
    # deploy needs no extra env var: it lands in the same bind-mounted
    # volume DATABASE_PATH already uses (review-app-data/, per
    # webapp/deploy/docker-compose.yml) without any compose change.
    media_uploads_path: Path | None = None
    max_photo_upload_bytes: int = 25 * 1024 * 1024
    max_video_upload_bytes: int = 500 * 1024 * 1024

    def resolved_media_uploads_path(self) -> Path:
        return self.media_uploads_path or (self.database_path.parent / "uploads")

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            database_path=Path(os.environ["DATABASE_PATH"]),
            archive_checkout_path=Path(os.environ["ARCHIVE_CHECKOUT_PATH"]),
            secret_key=os.environ["SECRET_KEY"],
            maintainer_email=os.environ["MAINTAINER_EMAIL"],
            smtp=SmtpConfig(
                host=os.environ["SMTP_HOST"],
                port=int(os.environ["SMTP_PORT"]),
                from_addr=os.environ["SMTP_FROM"],
                user=os.environ.get("SMTP_USER"),
                password=os.environ.get("SMTP_PASSWORD"),
            ),
            github=GithubConfig(
                token=os.environ["GITHUB_DISPATCH_TOKEN"],
                repo=os.environ["GITHUB_REPO"],
                workflow_file=os.environ.get("GITHUB_WORKFLOW_FILE", "apply-proposal.yml"),
                ref=os.environ.get("GITHUB_REF", "main"),
            ),
            callback_key=os.environ["REVIEW_APP_CALLBACK_KEY"],
            rate_limit_per_ip_per_hour=int(os.environ.get("RATE_LIMIT_PER_IP_PER_HOUR", "5")),
            login_rate_limit_per_ip_per_hour=int(os.environ.get("LOGIN_RATE_LIMIT_PER_IP_PER_HOUR", "20")),
            media_uploads_path=(
                Path(os.environ["MEDIA_UPLOADS_PATH"]) if "MEDIA_UPLOADS_PATH" in os.environ else None
            ),
            max_photo_upload_bytes=int(os.environ.get("MAX_PHOTO_UPLOAD_BYTES", str(25 * 1024 * 1024))),
            max_video_upload_bytes=int(os.environ.get("MAX_VIDEO_UPLOAD_BYTES", str(500 * 1024 * 1024))),
        )
