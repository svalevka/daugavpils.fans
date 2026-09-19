#!/usr/bin/env python3
"""
Unit tests for automated container security updates via Watchtower (GitHub issue #76).

Verifies:
- docker-compose.yml YAML syntax validity
- Watchtower service configuration (image, socket volume, restart policy)
- Watchtower environment safeguards (cleanup, label-enable opt-in, 6-field cron schedule)
- Nginx service opt-in label
- Review-app service exclusion (locally-built container must not opt in)
- Documentation in webapp/deploy/README.md
"""
from __future__ import annotations

import unittest
from pathlib import Path
import yaml

DEPLOY_DIR = Path(__file__).resolve().parent
DOCKER_COMPOSE_YML = DEPLOY_DIR / "docker-compose.yml"
README_FILE = DEPLOY_DIR / "README.md"


class WatchtowerComposeConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        self.raw_yaml = DOCKER_COMPOSE_YML.read_text()
        self.compose_data = yaml.safe_load(self.raw_yaml)
        self.services = self.compose_data.get("services", {})

    def test_compose_is_valid_yaml(self) -> None:
        """docker-compose.yml must parse cleanly as YAML."""
        self.assertIsInstance(self.compose_data, dict)
        self.assertIn("services", self.compose_data)

    def test_watchtower_service_defined(self) -> None:
        """watchtower service must be defined with containrrr/watchtower and restart: unless-stopped."""
        self.assertIn("watchtower", self.services)
        wt = self.services["watchtower"]
        self.assertEqual(wt.get("image"), "containrrr/watchtower")
        self.assertEqual(wt.get("container_name"), "daugavpils-fans-watchtower")
        self.assertEqual(wt.get("restart"), "unless-stopped")

    def test_watchtower_mounts_docker_socket(self) -> None:
        """watchtower must mount the host Docker socket."""
        wt = self.services.get("watchtower", {})
        volumes = wt.get("volumes", [])
        self.assertTrue(
            any("/var/run/docker.sock:/var/run/docker.sock" in v for v in volumes),
            f"Missing docker socket mount in watchtower volumes: {volumes}",
        )

    def test_watchtower_environment_safeguards(self) -> None:
        """watchtower environment must enforce opt-in only, cleanup, and off-peak cron schedule."""
        wt = self.services.get("watchtower", {})
        env = wt.get("environment", [])

        # Parse env list into dict
        env_dict = {}
        for item in env:
            if isinstance(item, str) and "=" in item:
                k, v = item.split("=", 1)
                env_dict[k.strip()] = v.strip()
            elif isinstance(item, dict):
                env_dict.update(item)

        # Docker API compatibility for modern Docker Engine 29+
        self.assertEqual(
            env_dict.get("DOCKER_API_VERSION", ""),
            "1.44",
            "DOCKER_API_VERSION must be set to 1.44 for Docker Engine 29+ compatibility",
        )

        # Opt-in safeguard: only containers explicitly labeled get updated
        self.assertEqual(
            env_dict.get("WATCHTOWER_LABEL_ENABLE", "").lower(),
            "true",
            "WATCHTOWER_LABEL_ENABLE must be true for opt-in safety",
        )

        # Cleanup safeguard: delete superseded image layers to save disk
        self.assertEqual(
            env_dict.get("WATCHTOWER_CLEANUP", "").lower(),
            "true",
            "WATCHTOWER_CLEANUP must be true to prune old image layers",
        )

        # 6-field cron schedule check (seconds minutes hours dom month dow)
        schedule = env_dict.get("WATCHTOWER_SCHEDULE", "")
        parts = schedule.split()
        self.assertEqual(
            len(parts),
            6,
            f"WATCHTOWER_SCHEDULE must be a 6-field cron expression, got: {schedule}",
        )
        # Verify it runs off-peak (04:00 UTC)
        self.assertEqual(parts[0], "0")   # 0 seconds
        self.assertEqual(parts[1], "0")   # 0 minutes
        self.assertEqual(parts[2], "4")   # 4 AM UTC

    def test_nginx_opts_in_to_watchtower(self) -> None:
        """nginx service must explicitly opt-in with com.centurylinklabs.watchtower.enable=true."""
        nginx = self.services.get("nginx", {})
        labels = nginx.get("labels", [])
        label_set = set()
        for label in labels:
            if isinstance(label, str):
                label_set.add(label.strip())
            elif isinstance(label, dict):
                for k, v in label.items():
                    label_set.add(f"{k}={v}")

        self.assertIn(
            "com.centurylinklabs.watchtower.enable=true",
            label_set,
            "nginx service must have com.centurylinklabs.watchtower.enable=true label",
        )

    def test_review_app_does_not_opt_in(self) -> None:
        """review-app is built from local source and must NOT have the watchtower enable label."""
        review_app = self.services.get("review-app", {})
        labels = review_app.get("labels", [])
        for label in labels:
            label_str = label if isinstance(label, str) else str(label)
            self.assertNotIn(
                "com.centurylinklabs.watchtower.enable=true",
                label_str,
                "review-app must not be managed by Watchtower because it is locally built",
            )

    def test_readme_documents_watchtower(self) -> None:
        """webapp/deploy/README.md must document Watchtower setup and safety controls."""
        readme_text = README_FILE.read_text()
        self.assertIn("Watchtower", readme_text)
        self.assertIn("WATCHTOWER_LABEL_ENABLE", readme_text)
        self.assertIn("com.centurylinklabs.watchtower.enable=true", readme_text)
        self.assertIn("WATCHTOWER_CLEANUP", readme_text)


if __name__ == "__main__":
    unittest.main()
