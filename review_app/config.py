"""
review_app's runtime configuration - env vars in production, explicit
constructor args in tests (so each test can point at its own scratch
database/checkout without racing other tests or touching real state).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Config:
    database_path: Path
    archive_checkout_path: Path
    secret_key: str
    rate_limit_per_ip_per_hour: int = 5

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            database_path=Path(os.environ["DATABASE_PATH"]),
            archive_checkout_path=Path(os.environ["ARCHIVE_CHECKOUT_PATH"]),
            secret_key=os.environ["SECRET_KEY"],
            rate_limit_per_ip_per_hour=int(os.environ.get("RATE_LIMIT_PER_IP_PER_HOUR", "5")),
        )
