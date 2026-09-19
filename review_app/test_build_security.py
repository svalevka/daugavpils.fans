#!/usr/bin/env python3
"""
Unit and regression tests for build security, pinned dependencies, and cryptographic
verification of container assets (GitHub issue #91).
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = REPO_ROOT / "review_app" / "Dockerfile"
TOOLS_REQ = REPO_ROOT / "tools" / "requirements.txt"
WEBAPP_REQ = REPO_ROOT / "webapp" / "requirements.txt"
REVIEW_APP_REQ = REPO_ROOT / "review_app" / "requirements.txt"
REVIEW_APP_LOCK = REPO_ROOT / "review_app" / "requirements.lock"


class BuildSecurityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dockerfile_text = DOCKERFILE.read_text()

    def test_dockerfile_does_not_execute_unpinned_curl_pipe_sh(self) -> None:
        """Dockerfile must never pipe curl into sh/bash without pinned hash verification (GitHub issue #91)."""
        self.assertNotRegex(
            self.dockerfile_text,
            r"curl\s+[^|\n]+\|\s*(sh|bash)",
            "Unpinned curl | sh execution found in review_app/Dockerfile",
        )

    def test_dockerfile_pins_deno_version_and_checksums(self) -> None:
        """Dockerfile must specify pinned DENO_VERSION and verify SHA-256 checksums for architectures."""
        self.assertIn("ARG DENO_VERSION=", self.dockerfile_text)
        self.assertIn("ARG DENO_SHA256_AMD64=", self.dockerfile_text)
        self.assertIn("ARG DENO_SHA256_ARM64=", self.dockerfile_text)
        self.assertIn("sha256sum -c", self.dockerfile_text)

    def test_dockerfile_verifies_mmdb_hash(self) -> None:
        """Dockerfile must verify SHA-256 hash of GeoLite2-Country.mmdb download."""
        self.assertIn("ARG MMDB_SHA256=", self.dockerfile_text)
        self.assertIn("hashlib.sha256", self.dockerfile_text)
        self.assertIn("MMDB SHA-256 mismatch", self.dockerfile_text)

    def test_dockerfile_installs_from_requirements_lock(self) -> None:
        """Dockerfile must install container dependencies using requirements.lock."""
        self.assertIn("COPY review_app/requirements.lock review_app/requirements.lock", self.dockerfile_text)
        self.assertIn("pip install --no-cache-dir -r review_app/requirements.lock", self.dockerfile_text)

    def _assert_all_requirements_pinned(self, req_path: Path) -> None:
        self.assertTrue(req_path.exists(), f"{req_path} does not exist")
        lines = [
            line.strip()
            for line in req_path.read_text().splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        self.assertTrue(len(lines) > 0, f"{req_path} is empty")
        for line in lines:
            self.assertNotIn(">=", line, f"Floating requirement '>=' found in {req_path}: {line}")
            self.assertNotIn(">", line, f"Floating requirement '>' found in {req_path}: {line}")
            self.assertNotIn("~=", line, f"Floating requirement '~=' found in {req_path}: {line}")
            self.assertIn("==", line, f"Requirement missing exact '==' pin in {req_path}: {line}")

    def test_tools_requirements_pinned(self) -> None:
        self._assert_all_requirements_pinned(TOOLS_REQ)

    def test_webapp_requirements_pinned(self) -> None:
        self._assert_all_requirements_pinned(WEBAPP_REQ)

    def test_review_app_requirements_pinned(self) -> None:
        self._assert_all_requirements_pinned(REVIEW_APP_REQ)

    def test_review_app_requirements_lock_pinned(self) -> None:
        self._assert_all_requirements_pinned(REVIEW_APP_LOCK)


if __name__ == "__main__":
    unittest.main()
