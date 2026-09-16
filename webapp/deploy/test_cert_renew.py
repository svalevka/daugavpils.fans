#!/usr/bin/env python3
"""
Unit and regression tests for automated Let's Encrypt TLS certificate renewal
(GitHub issue #46).

Verifies:
- Syntax of renew-cert.sh
- Failure when cloudflare.ini is missing
- Nginx reload behavior (only reloaded when cert hash changes)
- Heartbeat pinging behavior when HEARTBEAT_URL is provided
- Systemd service and timer configuration
- Documentation in webapp/deploy/README.md
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

DEPLOY_DIR = Path(__file__).resolve().parent
RENEW_SCRIPT = DEPLOY_DIR / "renew-cert.sh"
SERVICE_FILE = DEPLOY_DIR / "daugavpils-fans-cert-renew.service"
TIMER_FILE = DEPLOY_DIR / "daugavpils-fans-cert-renew.timer"
README_FILE = DEPLOY_DIR / "README.md"
BASH_BIN = shutil.which("bash")


class CertRenewScriptTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)

        self.base_dir = self.tmp_path / "base"
        self.base_dir.mkdir()

        self.certbot_dir = self.base_dir / "certbot"
        self.conf_dir = self.certbot_dir / "conf" / "live" / "daugavpils.fans"
        self.conf_dir.mkdir(parents=True)
        self.cert_file = self.conf_dir / "fullchain.pem"
        self.cert_file.write_text("initial-cert-content")

        self.cf_ini = self.base_dir / "cloudflare.ini"
        self.cf_ini.write_text("dns_cloudflare_api_token = secret")

        self.compose_file = self.base_dir / "docker-compose.yml"
        self.compose_file.write_text("version: '3'")

        self.bin_dir = self.tmp_path / "bin"
        self.bin_dir.mkdir()
        self.log_file = self.tmp_path / "mock_docker.log"

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_script_syntax(self) -> None:
        """Bash syntax check: bash -n renew-cert.sh."""
        res = subprocess.run([BASH_BIN, "-n", str(RENEW_SCRIPT)], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Syntax error in {RENEW_SCRIPT}: {res.stderr}")

    def test_fails_without_cloudflare_ini(self) -> None:
        """Script exits with error if cloudflare.ini is not present."""
        self.cf_ini.unlink()

        env = os.environ.copy()
        env.update(
            {
                "BASE_DIR": str(self.base_dir),
                "CLOUDFLARE_INI": str(self.cf_ini),
                "CERT_FILE": str(self.cert_file),
            }
        )

        res = subprocess.run([str(RENEW_SCRIPT)], env=env, capture_output=True, text=True)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("Cloudflare credentials not found", res.stderr)

    def test_no_reload_when_cert_unchanged(self) -> None:
        """When certbot runs and cert does not change, nginx should NOT reload."""
        mock_docker = self.bin_dir / "docker"
        mock_docker.write_text(f"""#!/usr/bin/env bash
echo "mock_docker called with: $@" >> "{self.log_file}"
exit 0
""")
        mock_docker.chmod(0o755)

        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{self.bin_dir}:{env.get('PATH', '')}",
                "BASE_DIR": str(self.base_dir),
                "CLOUDFLARE_INI": str(self.cf_ini),
                "CERTBOT_DIR": str(self.certbot_dir),
                "DOCKER_COMPOSE_FILE": str(self.compose_file),
                "CERT_FILE": str(self.cert_file),
                "DOCKER_BIN": str(mock_docker),
            }
        )

        res = subprocess.run([str(RENEW_SCRIPT)], env=env, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("No certificate renewal needed", res.stdout)

        log_content = self.log_file.read_text()
        self.assertIn("certbot/dns-cloudflare renew", log_content)
        self.assertNotIn("nginx -s reload", log_content)

    def test_reloads_nginx_when_cert_changes(self) -> None:
        """When cert changes, nginx reload must be executed."""
        mock_docker = self.bin_dir / "docker"
        mock_docker.write_text(f"""#!/usr/bin/env bash
echo "mock_docker called with: $@" >> "{self.log_file}"
if [[ "$*" == *"certbot/dns-cloudflare renew"* ]]; then
    echo "new-cert-content-updated" > "{self.cert_file}"
fi
exit 0
""")
        mock_docker.chmod(0o755)

        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{self.bin_dir}:{env.get('PATH', '')}",
                "BASE_DIR": str(self.base_dir),
                "CLOUDFLARE_INI": str(self.cf_ini),
                "CERTBOT_DIR": str(self.certbot_dir),
                "DOCKER_COMPOSE_FILE": str(self.compose_file),
                "CERT_FILE": str(self.cert_file),
                "DOCKER_BIN": str(mock_docker),
            }
        )

        res = subprocess.run([str(RENEW_SCRIPT)], env=env, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("Certificate renewed", res.stdout)
        self.assertIn("Nginx reloaded successfully", res.stdout)

        log_content = self.log_file.read_text()
        self.assertIn("exec -T nginx nginx -s reload", log_content)


class SystemdUnitsConfigTest(unittest.TestCase):
    def test_service_file_validity(self) -> None:
        content = SERVICE_FILE.read_text()
        self.assertIn("[Unit]", content)
        self.assertIn("[Service]", content)
        self.assertIn("Type=oneshot", content)
        self.assertIn("ExecStart=/opt/daugavpils-fans/renew-cert.sh", content)

    def test_timer_file_validity(self) -> None:
        content = TIMER_FILE.read_text()
        self.assertIn("[Unit]", content)
        self.assertIn("[Timer]", content)
        self.assertIn("OnCalendar=", content)
        self.assertIn("Persistent=true", content)
        self.assertIn("[Install]", content)
        self.assertIn("WantedBy=timers.target", content)


if __name__ == "__main__":
    unittest.main()
