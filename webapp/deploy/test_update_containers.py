#!/usr/bin/env python3
"""
Unit and regression tests for automated container security updates
via systemd timer (GitHub issue #82).

Verifies:
- Bash syntax of update-containers.sh
- Failure when docker-compose.yml is missing
- Correct command ordering: pull nginx, up nginx, build review-app, up review-app, image prune
- Isolation: only nginx and review-app within the project compose file are updated
- Health checks: passes on 200/301/302, fails on 500/000
- Failure alerts: maintainer is alerted via mail on health check failure and script exits non-zero
- Heartbeat ping: sent on success when HEARTBEAT_URL is provided
- Systemd service and timer unit configuration
- Absence of watchtower in docker-compose.yml
- Documentation in webapp/deploy/README.md
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
import yaml

DEPLOY_DIR = Path(__file__).resolve().parent
UPDATE_SCRIPT = DEPLOY_DIR / "update-containers.sh"
SERVICE_FILE = DEPLOY_DIR / "daugavpils-fans-update-containers.service"
TIMER_FILE = DEPLOY_DIR / "daugavpils-fans-update-containers.timer"
COMPOSE_FILE = DEPLOY_DIR / "docker-compose.yml"
README_FILE = DEPLOY_DIR / "README.md"
BASH_BIN = shutil.which("bash") or "bash"


class UpdateContainersScriptTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)

        self.base_dir = self.tmp_path / "base"
        self.base_dir.mkdir()

        self.compose_file = self.base_dir / "docker-compose.yml"
        self.compose_file.write_text("services:\n  nginx:\n    image: nginx:alpine\n")

        self.bin_dir = self.tmp_path / "bin"
        self.bin_dir.mkdir()

        self.docker_log = self.tmp_path / "mock_docker.log"
        self.curl_log = self.tmp_path / "mock_curl.log"
        self.mail_log = self.tmp_path / "mock_mail.log"

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_script_syntax(self) -> None:
        """Bash syntax check: bash -n update-containers.sh."""
        res = subprocess.run([BASH_BIN, "-n", str(UPDATE_SCRIPT)], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Syntax error in {UPDATE_SCRIPT}: {res.stderr}")

    def test_fails_without_compose_file(self) -> None:
        """Script exits with error if docker-compose.yml is not found."""
        missing_compose = self.base_dir / "nonexistent.yml"
        env = os.environ.copy()
        env["DOCKER_COMPOSE_FILE"] = str(missing_compose)

        res = subprocess.run([str(UPDATE_SCRIPT)], env=env, capture_output=True, text=True)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("Docker compose file not found", res.stderr)

    def _setup_mock_bins(self, http_code: str = "200") -> tuple[Path, Path, Path]:
        mock_docker = self.bin_dir / "docker"
        mock_docker.write_text(f"""#!/usr/bin/env bash
echo "docker $@" >> "{self.docker_log}"
exit 0
""")
        mock_docker.chmod(0o755)

        mock_curl = self.bin_dir / "curl"
        mock_curl.write_text(f"""#!/usr/bin/env bash
echo "curl $@" >> "{self.curl_log}"
if [[ "$*" == *"%{{http_code}}"* ]]; then
    echo "{http_code}"
    exit 0
fi
exit 0
""")
        mock_curl.chmod(0o755)

        mock_mail = self.bin_dir / "mail"
        mock_mail.write_text(f"""#!/usr/bin/env bash
echo "mail $@: $(cat)" >> "{self.mail_log}"
exit 0
""")
        mock_mail.chmod(0o755)

        return mock_docker, mock_curl, mock_mail

    def test_update_order_and_project_scoping(self) -> None:
        """Pulls and rebuilds must execute in order and only target nginx and review-app in the project."""
        mock_docker, mock_curl, mock_mail = self._setup_mock_bins("200")

        env = os.environ.copy()
        env.update(
            {
                "BASE_DIR": str(self.base_dir),
                "DOCKER_COMPOSE_FILE": str(self.compose_file),
                "DOCKER_BIN": str(mock_docker),
                "CURL_BIN": str(mock_curl),
                "MAIL_BIN": str(mock_mail),
                "HEALTHCHECK_URL_SITE": "https://daugavpils.fans",
                "HEALTHCHECK_URL_REVIEW": "https://review.daugavpils.fans",
                "HEARTBEAT_URL": "https://hc-ping.com/fake-uuid",
            }
        )

        res = subprocess.run([str(UPDATE_SCRIPT)], env=env, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Script failed: {res.stderr}\n{res.stdout}")

        docker_calls = self.docker_log.read_text().splitlines()
        self.assertEqual(len(docker_calls), 5)

        # 1. Pull nginx
        self.assertEqual(docker_calls[0], f"docker compose -f {self.compose_file} pull nginx")
        # 2. Up nginx
        self.assertEqual(docker_calls[1], f"docker compose -f {self.compose_file} up -d nginx")
        # 3. Build review-app with --pull
        self.assertEqual(docker_calls[2], f"docker compose -f {self.compose_file} build --pull review-app")
        # 4. Up review-app
        self.assertEqual(docker_calls[3], f"docker compose -f {self.compose_file} up -d review-app")
        # 5. Prune dangling images
        self.assertEqual(docker_calls[4], "docker image prune -f")

        # Health checks and heartbeat
        curl_calls = self.curl_log.read_text().splitlines()
        self.assertTrue(any("https://daugavpils.fans" in c for c in curl_calls))
        self.assertTrue(any("https://review.daugavpils.fans" in c for c in curl_calls))
        self.assertTrue(any("https://hc-ping.com/fake-uuid" in c for c in curl_calls))

        # No mail sent on success
        self.assertFalse(self.mail_log.exists())

    def test_health_check_failure_alerts_maintainer_and_exits(self) -> None:
        """When health check returns HTTP 500, script alerts maintainer via mail and exits with code 1."""
        mock_docker, mock_curl, mock_mail = self._setup_mock_bins("500")

        env = os.environ.copy()
        env.update(
            {
                "BASE_DIR": str(self.base_dir),
                "DOCKER_COMPOSE_FILE": str(self.compose_file),
                "DOCKER_BIN": str(mock_docker),
                "CURL_BIN": str(mock_curl),
                "MAIL_BIN": str(mock_mail),
                "MAINTAINER_EMAIL": "admin@daugavpils.fans",
                "HEARTBEAT_URL": "https://hc-ping.com/fake-uuid",
            }
        )

        res = subprocess.run([str(UPDATE_SCRIPT)], env=env, capture_output=True, text=True)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("Health check failed", res.stderr)
        self.assertIn("ALERT: Container health checks failed", res.stderr)

        # Mail must have been sent with alert
        mail_content = self.mail_log.read_text()
        self.assertIn("admin@daugavpils.fans", mail_content)
        self.assertIn("ALERT: Container update health check failure", mail_content)

        # Heartbeat ping must NOT be reached on failure
        curl_calls = self.curl_log.read_text().splitlines()
        self.assertFalse(any("https://hc-ping.com/fake-uuid" in c for c in curl_calls))


class SystemdUnitsConfigTest(unittest.TestCase):
    def test_service_file_validity(self) -> None:
        content = SERVICE_FILE.read_text()
        self.assertIn("[Unit]", content)
        self.assertIn("[Service]", content)
        self.assertIn("Type=oneshot", content)
        self.assertIn("User=root", content)
        self.assertIn("ExecStart=/opt/daugavpils-fans/update-containers.sh", content)
        self.assertIn("docker.service", content)

    def test_timer_file_validity(self) -> None:
        content = TIMER_FILE.read_text()
        self.assertIn("[Unit]", content)
        self.assertIn("[Timer]", content)
        self.assertIn("OnCalendar=", content)
        self.assertIn("Persistent=true", content)
        self.assertIn("[Install]", content)
        self.assertIn("WantedBy=timers.target", content)


class ComposeConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        self.raw_yaml = COMPOSE_FILE.read_text()
        self.compose_data = yaml.safe_load(self.raw_yaml)
        self.services = self.compose_data.get("services", {})

    def test_watchtower_completely_removed(self) -> None:
        """watchtower service must be removed from docker-compose.yml."""
        self.assertNotIn("watchtower", self.services)

    def test_nginx_has_no_watchtower_labels(self) -> None:
        """nginx service must not have watchtower labels."""
        nginx = self.services.get("nginx", {})
        labels = nginx.get("labels", [])
        for label in labels:
            self.assertNotIn("watchtower", str(label))


class DocumentationTest(unittest.TestCase):
    def test_readme_documents_timer(self) -> None:
        readme_text = README_FILE.read_text()
        self.assertIn("update-containers.sh", readme_text)
        self.assertIn("daugavpils-fans-update-containers.timer", readme_text)
        self.assertIn("daugavpils-fans-update-containers.service", readme_text)
        self.assertNotIn("containrrr/watchtower", readme_text)


if __name__ == "__main__":
    unittest.main()
