#!/usr/bin/env python3
"""
Unit tests for nginx default_server catch-all configuration (GitHub issue #77).

Verifies:
- Definition of default_server block on port 80 dropping unrecognized hostnames with 444.
- Definition of default_server block on port 443 (SSL) dropping unrecognized hostnames with 444.
- TLS certificate directives are present in the HTTPS default_server block.
- Explicit server_name _; catch-all.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

DEPLOY_DIR = Path(__file__).resolve().parent
NGINX_CONF = DEPLOY_DIR / "nginx" / "daugavpils.conf"


class NginxDefaultServerConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        self.conf_text = NGINX_CONF.read_text()

    def test_port_80_default_server_returns_444(self) -> None:
        """Nginx must declare a default_server on port 80 returning 444 for unknown hostnames."""
        match = re.search(
            r"server\s*\{[^}]*?listen\s+80\s+default_server;[^}]*?server_name\s+_;\s*return\s+444;\s*\}",
            self.conf_text,
            re.DOTALL,
        )
        self.assertIsNotNone(match, "Port 80 default_server block returning 444 not found")

    def test_port_443_default_server_returns_444(self) -> None:
        """Nginx must declare a default_server on port 443 returning 444 for unknown hostnames."""
        match = re.search(
            r"server\s*\{[^}]*?listen\s+443\s+ssl\s+default_server;[^}]*?server_name\s+_;\s*[^}]*?return\s+444;\s*\}",
            self.conf_text,
            re.DOTALL,
        )
        self.assertIsNotNone(match, "Port 443 SSL default_server block returning 444 not found")

    def test_port_443_default_server_has_tls_certs(self) -> None:
        """HTTPS default_server block must configure SSL certificates to complete TLS handshake before returning 444."""
        match = re.search(
            r"server\s*\{[^}]*?listen\s+443\s+ssl\s+default_server;[^}]*?\}",
            self.conf_text,
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        block = match.group(0)
        self.assertIn("ssl_certificate", block)
        self.assertIn("ssl_certificate_key", block)


if __name__ == "__main__":
    unittest.main()
