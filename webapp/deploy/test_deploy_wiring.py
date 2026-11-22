#!/usr/bin/env python3
"""
Checks on deploy config files that other pieces silently depend on.
"""
from __future__ import annotations

import unittest
from pathlib import Path

import yaml

DEPLOY_DIR = Path(__file__).resolve().parent


class DeployWiringTest(unittest.TestCase):
    def test_only_review_app_gets_the_snapshot_directory_and_only_read_only(self):
        compose = yaml.safe_load((DEPLOY_DIR / "docker-compose.yml").read_text())
        mount = "./security-review:/security-review:ro"
        self.assertIn(mount, compose["services"]["review-app"]["volumes"])
        self.assertNotIn(mount, compose["services"]["nginx"]["volumes"])
        writable = [v for s in compose["services"].values() for v in s.get("volumes", []) if "security-review" in v and not v.endswith(":ro")]
        self.assertEqual(writable, [])

    def test_reboot_config_enables_automatic_reboot(self):
        text = (DEPLOY_DIR / "52unattended-upgrades-reboot").read_text()
        self.assertIn('Unattended-Upgrade::Automatic-Reboot "true";', text)
        self.assertIn("Unattended-Upgrade::Automatic-Reboot-Time", text)


if __name__ == "__main__":
    unittest.main()
