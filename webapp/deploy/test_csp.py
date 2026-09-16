#!/usr/bin/env python3
"""
Unit and regression tests for Content Security Policy (CSP) and zero-inline-script enforcement
across nginx configuration and review_app templates (GitHub issue #70).

Verifies:
- webapp/deploy/nginx/daugavpils.conf eliminates 'unsafe-inline' from script-src in review.daugavpils.fans
- daugavpils.fans also enforces script-src 'self' without 'unsafe-inline'
- All review_app templates have zero inline executable <script> tags
- All review_app templates have zero inline event handlers (onclick, onsubmit, etc.)
- Object URLs (blob:) are permitted in img-src and media-src for client-side previews
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

DEPLOY_DIR = Path(__file__).resolve().parent
NGINX_CONF = DEPLOY_DIR / "nginx" / "daugavpils.conf"
REVIEW_APP_DIR = DEPLOY_DIR.parent.parent / "review_app"
TEMPLATES_DIR = REVIEW_APP_DIR / "templates"
STATIC_DIR = REVIEW_APP_DIR / "static"


class NginxCspConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        self.conf_text = NGINX_CONF.read_text()

    def test_review_app_server_block_csp_eliminates_unsafe_inline_for_scripts(self) -> None:
        """review.daugavpils.fans must enforce script-src 'self' without 'unsafe-inline'."""
        # Find review.daugavpils.fans server block
        review_server_match = re.search(
            r"server_name\s+review\.daugavpils\.fans;.*?(?=server\s*\{|\Z)",
            self.conf_text,
            re.DOTALL,
        )
        self.assertIsNotNone(review_server_match, "review.daugavpils.fans server block not found")
        block_text = review_server_match.group(0)

        csp_match = re.search(r"add_header\s+Content-Security-Policy\s+\"([^\"]+)\"", block_text)
        self.assertIsNotNone(csp_match, "Content-Security-Policy header not found in review_app block")
        csp_header = csp_match.group(1)

        # Parse directives
        directives = {}
        for directive in csp_header.split(";"):
            directive = directive.strip()
            if not directive:
                continue
            parts = directive.split()
            directives[parts[0]] = parts[1:]

        self.assertIn("script-src", directives, "script-src directive missing in review_app CSP")
        self.assertIn("'self'", directives["script-src"], "script-src must allow 'self'")
        self.assertNotIn(
            "'unsafe-inline'",
            directives["script-src"],
            "script-src must NOT contain 'unsafe-inline' in review_app CSP (Issue #70)",
        )

    def test_review_app_csp_allows_blob_previews(self) -> None:
        """review.daugavpils.fans CSP must allow blob: in img-src and media-src for client previews."""
        review_server_match = re.search(
            r"server_name\s+review\.daugavpils\.fans;.*?(?=server\s*\{|\Z)",
            self.conf_text,
            re.DOTALL,
        )
        self.assertIsNotNone(review_server_match)
        block_text = review_server_match.group(0)
        csp_match = re.search(r"add_header\s+Content-Security-Policy\s+\"([^\"]+)\"", block_text)
        self.assertIsNotNone(csp_match)
        csp_header = csp_match.group(1)

        directives = {}
        for directive in csp_header.split(";"):
            directive = directive.strip()
            if not directive:
                continue
            parts = directive.split()
            directives[parts[0]] = parts[1:]

        self.assertIn("img-src", directives)
        self.assertIn("blob:", directives["img-src"], "img-src must allow blob: for image preview URLs")
        self.assertIn("media-src", directives)
        self.assertIn("blob:", directives["media-src"], "media-src must allow blob: for audio preview URLs")

    def test_main_site_csp_has_no_unsafe_inline_scripts(self) -> None:
        """daugavpils.fans must enforce script-src 'self' without 'unsafe-inline'."""
        main_server_match = re.search(
            r"server_name\s+daugavpils\.fans\s+www\.daugavpils\.fans;.*?(?=server\s*\{|\Z)",
            self.conf_text,
            re.DOTALL,
        )
        self.assertIsNotNone(main_server_match, "daugavpils.fans server block not found")
        block_text = main_server_match.group(0)
        csp_match = re.search(r"add_header\s+Content-Security-Policy\s+\"([^\"]+)\"", block_text)
        self.assertIsNotNone(csp_match)
        csp_header = csp_match.group(1)

        directives = {}
        for directive in csp_header.split(";"):
            directive = directive.strip()
            if not directive:
                continue
            parts = directive.split()
            directives[parts[0]] = parts[1:]

        self.assertIn("script-src", directives)
        self.assertNotIn("'unsafe-inline'", directives["script-src"])


class ReviewAppTemplatesCspAuditTest(unittest.TestCase):
    def setUp(self) -> None:
        self.template_files = list(TEMPLATES_DIR.rglob("*.html"))
        self.assertTrue(len(self.template_files) > 0, "No templates found to audit")

    def test_no_inline_executable_scripts_in_templates(self) -> None:
        """All <script> tags must either have a src attribute or be non-executable data (e.g. JSON)."""
        inline_script_pattern = re.compile(r"<script\b([^>]*)>(.*?)</script>", re.IGNORECASE | re.DOTALL)
        src_attr_pattern = re.compile(r'\bsrc\s*=\s*["\']', re.IGNORECASE)
        safe_type_pattern = re.compile(r'\btype\s*=\s*["\'](application/ld\+json|application/json)["\']', re.IGNORECASE)

        violations = []
        for tf in self.template_files:
            rel_path = tf.relative_to(TEMPLATES_DIR)
            content = tf.read_text(encoding="utf-8")
            for match in inline_script_pattern.finditer(content):
                attrs = match.group(1)
                body = match.group(2).strip()
                if not src_attr_pattern.search(attrs) and not safe_type_pattern.search(attrs) and body:
                    violations.append(f"{rel_path}: <script{attrs}>{body[:40]}...</script>")

        self.assertEqual(violations, [], f"Found inline executable scripts in templates: {violations}")

    def test_no_inline_event_handlers_in_templates(self) -> None:
        """Templates must not contain inline event handlers (onclick, onsubmit, onload, etc.)."""
        # Matches attributes like onclick="...", onsubmit='...'
        handler_pattern = re.compile(r'\b(on[a-zA-Z]{3,20})\s*=\s*["\']', re.IGNORECASE)

        violations = []
        for tf in self.template_files:
            rel_path = tf.relative_to(TEMPLATES_DIR)
            content = tf.read_text(encoding="utf-8")
            for match in handler_pattern.finditer(content):
                violations.append(f"{rel_path}: attribute '{match.group(1)}'")

        self.assertEqual(violations, [], f"Found inline event handlers in templates: {violations}")

    def test_admin_users_script_referenced_and_exists(self) -> None:
        """admin/users.html must reference admin_users.js, which must exist in static directory."""
        users_template = (TEMPLATES_DIR / "admin" / "users.html").read_text(encoding="utf-8")
        self.assertIn("admin_users.js", users_template)
        admin_users_js = STATIC_DIR / "admin_users.js"
        self.assertTrue(admin_users_js.is_file(), f"Expected {admin_users_js} to exist")


if __name__ == "__main__":
    unittest.main()
