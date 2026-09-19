#!/usr/bin/env python3
"""
Unit and regression tests for nginx rate limiting (limit_req) configuration
in front of review_app (GitHub issue #73).

Verifies:
- Definition of limit_req_zone for general and strict traffic using $binary_remote_addr
- limit_req_status 429 configuration to match application-layer 429 responses (issue #26)
- Presence of rate-limiting directives in review.daugavpils.fans and daugavpils.fans server blocks
- Stricter throttling on abuse-sensitive endpoints (/submit, /login)
- Documentation of rate limiting and upload body sizes/timeouts in webapp/deploy/README.md
- Rationale comments explaining why parameters were selected
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

DEPLOY_DIR = Path(__file__).resolve().parent
NGINX_CONF = DEPLOY_DIR / "nginx" / "daugavpils.conf"
README_FILE = DEPLOY_DIR / "README.md"


class NginxRateLimitConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        self.conf_text = NGINX_CONF.read_text()
        self.readme_text = README_FILE.read_text()

    def test_rate_limit_zones_defined_globally(self) -> None:
        """Global configuration must declare review_general_zone, review_strict_zone, and limit_req_status 429."""
        self.assertRegex(
            self.conf_text,
            r"limit_req_zone\s+\$binary_remote_addr\s+zone=review_general_zone:10m\s+rate=10r/s;",
            "review_general_zone not defined with 10m memory and 10r/s rate",
        )
        self.assertRegex(
            self.conf_text,
            r"limit_req_zone\s+\$binary_remote_addr\s+zone=review_strict_zone:10m\s+rate=2r/s;",
            "review_strict_zone not defined with 10m memory and 2r/s rate",
        )
        self.assertRegex(
            self.conf_text,
            r"limit_req_zone\s+\$binary_remote_addr\s+zone=review_event_zone:10m\s+rate=2r/s;",
            "review_event_zone not defined with 10m memory and 2r/s rate",
        )
        self.assertRegex(
            self.conf_text,
            r"limit_req_status\s+429;",
            "limit_req_status must be set to 429 (Too Many Requests)",
        )

    def test_review_app_server_block_rate_limiting(self) -> None:
        """review.daugavpils.fans must apply general rate limit and strict limits for /submit and /login."""
        review_server_match = re.search(
            r"server_name\s+review\.daugavpils\.fans;.*?(?=server\s*\{|\Z)",
            self.conf_text,
            re.DOTALL,
        )
        self.assertIsNotNone(review_server_match, "review.daugavpils.fans server block not found")
        block_text = review_server_match.group(0)

        # General rate limit on review.daugavpils.fans
        self.assertRegex(
            block_text,
            r"limit_req\s+zone=review_general_zone\s+burst=20\s+nodelay;",
            "review.daugavpils.fans root should apply review_general_zone burst=20 nodelay",
        )

        # Strict rate limit on /submit
        self.assertRegex(
            block_text,
            r"location\s+/submit\s*\{[^}]*limit_req\s+zone=review_strict_zone\s+burst=10\s+nodelay;",
            "review.daugavpils.fans location /submit must apply review_strict_zone burst=10 nodelay",
        )

        # Strict rate limit on /login
        self.assertRegex(
            block_text,
            r"location\s+/login\s*\{[^}]*limit_req\s+zone=review_strict_zone\s+burst=5\s+nodelay;",
            "review.daugavpils.fans location /login must apply review_strict_zone burst=5 nodelay",
        )

    def test_api_event_rate_limited_and_size_capped(self) -> None:
        """location /api/event must enforce review_event_zone and client_max_body_size 64k (GitHub issue #86)."""
        main_server_match = re.search(
            r"server_name\s+daugavpils\.fans\s+www\.daugavpils\.fans;.*?(?=server\s*\{|\Z)",
            self.conf_text,
            re.DOTALL,
        )
        self.assertIsNotNone(main_server_match, "daugavpils.fans server block not found")
        main_text = main_server_match.group(0)

        self.assertRegex(
            main_text,
            r"location\s+/api/event\s*\{[^}]*limit_req\s+zone=review_event_zone\s+burst=5\s+nodelay;",
            "daugavpils.fans /api/event must apply review_event_zone burst=5 nodelay",
        )
        self.assertRegex(
            main_text,
            r"location\s+/api/event\s*\{[^}]*client_max_body_size\s+64k;",
            "daugavpils.fans /api/event must cap body size at 64k",
        )

    def test_large_upload_limits_scoped_strictly_to_upload_locations(self) -> None:
        """review.daugavpils.fans must scope 2g body size and 600s timeouts strictly to upload locations (GitHub issue #86)."""
        review_server_match = re.search(
            r"server_name\s+review\.daugavpils\.fans;.*?(?=server\s*\{|\Z)",
            self.conf_text,
            re.DOTALL,
        )
        self.assertIsNotNone(review_server_match, "review.daugavpils.fans server block not found")
        block_text = review_server_match.group(0)

        # Ensure client_max_body_size 2g is inside location /submit
        self.assertRegex(
            block_text,
            r"location\s+/submit\s*\{[^}]*client_max_body_size\s+2g;",
            "location /submit must contain client_max_body_size 2g",
        )
        self.assertRegex(
            block_text,
            r"location\s+/submit\s*\{[^}]*client_body_timeout\s+600s;",
            "location /submit must contain client_body_timeout 600s",
        )

        # Ensure server-level block does NOT contain client_max_body_size 2g outside location /submit
        server_level_before_locations = block_text.split("location")[0]
        self.assertNotIn(
            "client_max_body_size 2g",
            server_level_before_locations,
            "client_max_body_size 2g must not be configured at server level outside upload locations",
        )
        self.assertNotIn(
            "client_body_timeout 600s",
            server_level_before_locations,
            "client_body_timeout 600s must not be configured at server level outside upload locations",
        )

    def test_primary_domain_proxied_locations_rate_limiting(self) -> None:
        """daugavpils.fans proxied admin, dashboard, and login locations must also apply rate limits."""
        main_server_match = re.search(
            r"server_name\s+daugavpils\.fans\s+www\.daugavpils\.fans;.*?(?=server\s*\{|\Z)",
            self.conf_text,
            re.DOTALL,
        )
        self.assertIsNotNone(main_server_match, "daugavpils.fans server block not found")
        block_text = main_server_match.group(0)

        self.assertRegex(
            block_text,
            r"location\s+/admin\s*\{[^}]*limit_req\s+zone=review_general_zone\s+burst=20\s+nodelay;",
            "daugavpils.fans /admin must apply review_general_zone burst=20 nodelay",
        )
        self.assertRegex(
            block_text,
            r"location\s+/dashboard\s*\{[^}]*limit_req\s+zone=review_general_zone\s+burst=20\s+nodelay;",
            "daugavpils.fans /dashboard must apply review_general_zone burst=20 nodelay",
        )
        self.assertRegex(
            block_text,
            r"location\s+/login\s*\{[^}]*limit_req\s+zone=review_strict_zone\s+burst=5\s+nodelay;",
            "daugavpils.fans /login must apply review_strict_zone burst=5 nodelay",
        )

    def test_rationale_comments_exist(self) -> None:
        """daugavpils.conf must explain WHY rate limits are set as they are."""
        self.assertIn("GitHub issue #73", self.conf_text)
        self.assertIn("$binary_remote_addr", self.conf_text)
        self.assertIn("review_general_zone", self.conf_text)
        self.assertIn("review_strict_zone", self.conf_text)
        self.assertIn("review_event_zone", self.conf_text)

    def test_readme_documents_rate_limiting_and_upload_limits(self) -> None:
        """webapp/deploy/README.md must document the rate limits and upload body size / timeouts."""
        self.assertIn("review_general_zone", self.readme_text)
        self.assertIn("review_strict_zone", self.readme_text)
        self.assertIn("review_event_zone", self.readme_text)
        self.assertIn("client_max_body_size", self.readme_text)
        self.assertIn("client_body_timeout", self.readme_text)


if __name__ == "__main__":
    unittest.main()
