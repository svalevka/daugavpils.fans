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


@dataclass
class Config:
    database_path: Path
    archive_checkout_path: Path
    secret_key: str
    maintainer_email: str
    smtp: SmtpConfig
    rate_limit_per_ip_per_hour: int = 5

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
            rate_limit_per_ip_per_hour=int(os.environ.get("RATE_LIMIT_PER_IP_PER_HOUR", "5")),
        )
