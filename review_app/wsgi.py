"""
Production WSGI entrypoint (see GitHub issue #14): what gunicorn actually
imports (`gunicorn wsgi:app`, see Dockerfile). Reads all configuration
from the environment via Config.from_env() - the one place in review_app
that's allowed to do that; everywhere else takes an explicit Config so
tests can supply their own (see config.py, test_support.py).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import create_app  # noqa: E402
from config import Config  # noqa: E402

app = create_app(Config.from_env())
