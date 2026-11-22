#!/usr/bin/env python3
"""
Tests for security_snapshot.py - the root-run host collector behind the
weekly security review (documentation/SECURITY_REVIEW.md).

The parts that matter most are the ones facing untrusted input: the
request file sits in a directory the review-app container owns, and a
root process reads it. Also covered: the text parsers, snapshot
rotation, that a failing collector never costs the whole snapshot, and
the systemd/compose wiring.
"""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path

import yaml

DEPLOY_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DEPLOY_DIR))

import security_snapshot as ss  # noqa: E402

NONCE = "0123456789abcdef0123456789abcdef"


class TmpCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)


class ReadNonceTest(TmpCase):
    def test_valid_nonce_is_returned(self):
        path = self.tmp / "request"
        path.write_text(NONCE + "\n")
        self.assertEqual(ss.read_nonce(path), NONCE)

    def test_missing_file_is_rejected(self):
        with self.assertRaises(ss.RequestError):
            ss.read_nonce(self.tmp / "nope")

    def test_symlink_is_never_followed(self):
        secret = self.tmp / "secret"
        secret.write_text(NONCE)  # even a "valid" target must not be read through a link
        link = self.tmp / "request"
        link.symlink_to(secret)
        with self.assertRaises(ss.RequestError):
            ss.read_nonce(link)

    def test_directory_is_rejected(self):
        (self.tmp / "request").mkdir()
        with self.assertRaises(ss.RequestError):
            ss.read_nonce(self.tmp / "request")

    def test_malformed_content_is_rejected(self):
        for content in ("", "short", NONCE.upper(), NONCE + "; rm -rf /", "../../etc/passwd", NONCE[:-1] + "g", "x" * 5000):
            path = self.tmp / "request"
            path.write_text(content)
            with self.assertRaises(ss.RequestError, msg=repr(content[:40])):
                ss.read_nonce(path)

    def test_main_writes_nothing_for_an_invalid_request(self):
        request = self.tmp / "request"
        request.write_text("not-a-nonce")
        out_dir = self.tmp / "out"
        old = (ss.REQUEST_FILE, ss.OUT_DIR)
        ss.REQUEST_FILE, ss.OUT_DIR = request, out_dir
        try:
            self.assertEqual(ss.main(), 1)
        finally:
            ss.REQUEST_FILE, ss.OUT_DIR = old
        self.assertFalse(out_dir.exists())


class WriteSnapshotTest(TmpCase):
    def test_writes_json_readable_by_group_but_not_others(self):
        ss.write_snapshot({"nonce": NONCE, "collected_at": 100}, self.tmp, gid=None)
        path = self.tmp / "snapshot.json"
        self.assertEqual(json.loads(path.read_text())["nonce"], NONCE)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode) & 0o007, 0)
        self.assertFalse((self.tmp / ".snapshot.json.tmp").exists())

    def test_week_old_snapshot_becomes_the_baseline(self):
        now = time.time()
        ss.write_snapshot({"nonce": "old", "collected_at": now - 7 * 86400}, self.tmp, gid=None, now=now - 7 * 86400)
        ss.write_snapshot({"nonce": "new", "collected_at": now}, self.tmp, gid=None, now=now)
        self.assertEqual(json.loads((self.tmp / "previous.json").read_text())["nonce"], "old")
        self.assertEqual(json.loads((self.tmp / "snapshot.json").read_text())["nonce"], "new")

    def test_a_retry_minutes_later_does_not_overwrite_the_baseline(self):
        now = time.time()
        ss.write_snapshot({"nonce": "week-ago", "collected_at": now - 7 * 86400}, self.tmp, gid=None, now=now - 7 * 86400)
        ss.write_snapshot({"nonce": "first-try", "collected_at": now - 300}, self.tmp, gid=None, now=now - 300)
        ss.write_snapshot({"nonce": "retry", "collected_at": now}, self.tmp, gid=None, now=now)
        self.assertEqual(json.loads((self.tmp / "previous.json").read_text())["nonce"], "week-ago")
        self.assertEqual(json.loads((self.tmp / "snapshot.json").read_text())["nonce"], "retry")

    def test_corrupt_existing_snapshot_is_replaced_not_fatal(self):
        (self.tmp / "snapshot.json").write_text("{not json")
        ss.write_snapshot({"nonce": NONCE, "collected_at": 1}, self.tmp, gid=None)
        self.assertEqual(json.loads((self.tmp / "snapshot.json").read_text())["nonce"], NONCE)

    def test_planted_temp_symlink_is_not_written_through(self):
        target = self.tmp / "victim"
        target.write_text("untouched")
        (self.tmp / ".snapshot.json.tmp").symlink_to(target)
        ss.write_snapshot({"nonce": NONCE, "collected_at": 1}, self.tmp, gid=None)
        self.assertEqual(target.read_text(), "untouched")


class ParserTest(unittest.TestCase):
    def test_parse_ss(self):
        text = (
            'tcp LISTEN 0 4096 0.0.0.0:443 0.0.0.0:* users:(("docker-proxy",pid=1,fd=8))\n'
            'tcp LISTEN 0 4096 [::]:22 [::]:* users:(("sshd",pid=2,fd=4),("systemd",pid=1,fd=182))\n'
            'udp UNCONN 0 0 127.0.0.53%lo:53 0.0.0.0:* users:(("systemd-resolve",pid=3,fd=14))\n'
            'udp UNCONN 0 0 0.0.0.0:5353 0.0.0.0:* users:(("gw",pid=4,fd=27))\n'
            'udp UNCONN 0 0 0.0.0.0:5353 0.0.0.0:* users:(("gw",pid=4,fd=26))\n'
            "garbage\n"
        )
        self.assertEqual(
            ss.parse_ss(text),
            [
                {"proto": "tcp", "addr": "0.0.0.0", "port": "443", "process": "docker-proxy"},
                {"proto": "tcp", "addr": "[::]", "port": "22", "process": "sshd"},
                {"proto": "udp", "addr": "127.0.0.53%lo", "port": "53", "process": "systemd-resolve"},
                {"proto": "udp", "addr": "0.0.0.0", "port": "5353", "process": "gw"},
            ],
        )

    def test_parse_sshd_config_keeps_only_security_relevant_keys(self):
        parsed = ss.parse_sshd_config("port 22\npasswordauthentication no\npermitrootlogin without-password\nhostkey /etc/ssh/key\n")
        self.assertEqual(parsed, {"port": "22", "passwordauthentication": "no", "permitrootlogin": "without-password"})

    def test_parse_shadow_never_returns_hashes(self):
        text = "root:*:1::::::\nsergei:$y$j9T$secret-hash:1::::::\nguest::1::::::\nlocked:!$y$abc:1::::::\n"
        parsed = ss.parse_shadow(text)
        self.assertEqual(parsed, {"empty_password": ["guest"], "password_set": ["sergei"]})
        self.assertNotIn("secret-hash", json.dumps(parsed))

    def test_parse_authorized_keys_fingerprints_and_options(self):
        blob = "AAAAC3NzaC1lZDI1NTE5AAAAIHRlc3R0ZXN0dGVzdHRlc3R0ZXN0dGVzdHRlc3R0ZXN0"
        text = f"# comment\nssh-ed25519 {blob} me@laptop\ncommand=\"/bin/true\" ssh-ed25519 {blob} forced\nssh-rsa not-base64!! broken\n\n"
        keys = ss.parse_authorized_keys(text)
        self.assertEqual(len(keys), 2)
        self.assertTrue(keys[0]["fingerprint"].startswith("SHA256:"))
        self.assertEqual(keys[0]["comment"], "me@laptop")
        self.assertFalse(keys[0]["has_options"])
        self.assertTrue(keys[1]["has_options"])
        self.assertNotIn(blob, json.dumps(keys))

    def test_parse_apt_upgradable_counts_security_separately(self):
        text = (
            "Listing...\n"
            "openssl/noble-security 3.0.2 amd64 [upgradable from: 3.0.1]\n"
            "vim/noble-updates 9.1 amd64 [upgradable from: 9.0]\n"
        )
        self.assertEqual(ss.parse_apt_upgradable(text), {"upgradable": 2, "security": 1, "security_packages": ["openssl"]})

    def test_parse_ssh_journal(self):
        text = (
            "Accepted publickey for sergei from 1.2.3.4 port 5 ssh2: ED25519 SHA256:x\n"
            "Accepted publickey for sergei from 1.2.3.4 port 6 ssh2: ED25519 SHA256:x\n"
            "Failed password for root from 9.9.9.9 port 1 ssh2\n"
            "Invalid user admin from 9.9.9.9 port 2\n"
        )
        parsed = ss.parse_ssh_journal(text)
        self.assertEqual(parsed["failed_auth"], 1)
        self.assertEqual(parsed["invalid_user"], 1)
        self.assertEqual(parsed["accepted"], [{"user": "sergei", "from": "1.2.3.4", "method": "publickey", "count": 2}])

    def test_access_log_summary_contains_no_visitor_ips(self):
        lines = [
            '203.0.113.9 - - [22/Nov/2026:10:00:00 +0000] "GET / HTTP/1.1" 200 512 "-" "UA"',
            '203.0.113.9 - - [22/Nov/2026:10:00:01 +0000] "GET /wp-login.php?x=1 HTTP/1.1" 404 10 "-" "UA"',
            '198.51.100.7 - - [22/Nov/2026:10:00:02 +0000] "GET /wp-login.php HTTP/1.1" 404 10 "-" "UA"',
            '198.51.100.7 - - [22/Nov/2026:10:00:03 +0000] "POST /submit HTTP/1.1" 429 10 "-" "UA"',
            "2026/11/22 10:00:00 [notice] 1#1: start worker processes",
        ]
        summary = ss.summarize_access_log(lines)
        self.assertEqual(summary["requests"], 4)
        self.assertEqual(summary["by_status"], {"200": 1, "404": 2, "429": 1})
        self.assertEqual(summary["top_4xx_paths"], [{"path": "/wp-login.php", "count": 2}])
        self.assertNotIn("203.0.113.9", json.dumps(summary))
        self.assertNotIn("198.51.100.7", json.dumps(summary))


class CollectorResilienceTest(TmpCase):
    def test_missing_command_is_recorded_not_raised(self):
        c = ss.Collector()
        self.assertIsNone(c.run(["definitely-not-a-real-command-xyz"]))
        self.assertEqual(c.errors, ["definitely-not-a-real-command-xyz: not installed"])

    def test_one_failing_section_does_not_abort_the_rest(self):
        c = ss.Collector()
        self.assertIsNone(c.section("boom", lambda: 1 / 0))
        self.assertEqual(c.section("fine", lambda: 42), 42)
        self.assertIn("boom: ZeroDivisionError", c.errors[0])

    def test_stat_entry_reports_mode_and_never_file_content(self):
        secret = self.tmp / "review-app.env"
        secret.write_text("SECRET_KEY=hunter2")
        os.chmod(secret, 0o600)
        entry = ss.stat_entry(str(secret))
        self.assertEqual(entry["mode"], "0600")
        self.assertEqual(entry["kind"], "file")
        self.assertNotIn("hunter2", json.dumps(entry))
        self.assertIsNone(ss.stat_entry(str(self.tmp / "missing")))

    def test_app_anomalies_summary_reads_the_database_read_only(self):
        import sqlite3

        db = self.tmp / "review.db"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE anomaly_events (created_at TEXT, severity TEXT, anomaly_type TEXT, acknowledged_at TEXT)")
        conn.execute("INSERT INTO anomaly_events VALUES (datetime('now'), 'CRITICAL', 'approval_velocity', NULL)")
        conn.execute("INSERT INTO anomaly_events VALUES (datetime('now', '-30 days'), 'CRITICAL', 'old', NULL)")
        conn.commit()
        conn.close()
        self.assertEqual(
            ss.collect_app_anomalies(db),
            {"last_7_days": [{"severity": "CRITICAL", "type": "approval_velocity", "count": 1, "unacknowledged": 1}]},
        )
        self.assertIsNone(ss.collect_app_anomalies(self.tmp / "absent.db"))

    def test_app_anomalies_can_be_read_from_a_read_only_directory(self):
        # The service runs under ProtectSystem=strict: the collector
        # cannot create SQLite's -shm/-wal side files next to a WAL db.
        import sqlite3

        ro_dir = self.tmp / "ro"
        ro_dir.mkdir()
        db = ro_dir / "review.db"
        conn = sqlite3.connect(db)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE anomaly_events (created_at TEXT, severity TEXT, anomaly_type TEXT, acknowledged_at TEXT)")
        conn.execute("INSERT INTO anomaly_events VALUES (datetime('now'), 'WARNING', 'traffic_surge', 'x')")
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
        for side in ("review.db-wal", "review.db-shm"):
            (ro_dir / side).unlink(missing_ok=True)
        os.chmod(ro_dir, 0o555)
        self.addCleanup(os.chmod, ro_dir, 0o755)
        self.assertEqual(
            ss.collect_app_anomalies(db),
            {"last_7_days": [{"severity": "WARNING", "type": "traffic_surge", "count": 1, "unacknowledged": 0}]},
        )
        self.assertEqual(sorted(p.name for p in ro_dir.iterdir()), ["review.db"])


class WiringTest(unittest.TestCase):
    def test_path_unit_watches_the_request_file_and_starts_the_service(self):
        text = (DEPLOY_DIR / "daugavpils-fans-security-snapshot.path").read_text()
        self.assertIn("PathChanged=/opt/daugavpils-fans/review-app-data/security-review-request", text)
        self.assertIn("Unit=daugavpils-fans-security-snapshot.service", text)

    def test_there_is_no_timer_for_it_on_the_server(self):
        # GitHub owns the schedule (documentation/SECURITY_REVIEW.md).
        self.assertFalse((DEPLOY_DIR / "daugavpils-fans-security-snapshot.timer").exists())

    def test_service_runs_the_collector_isolated_and_read_only(self):
        text = (DEPLOY_DIR / "daugavpils-fans-security-snapshot.service").read_text()
        self.assertIn("ExecStart=/usr/bin/python3 -I /opt/daugavpils-fans/security_snapshot.py", text)
        self.assertIn("ProtectSystem=strict", text)
        self.assertIn("ReadWritePaths=/opt/daugavpils-fans/security-review", text)

    def test_container_gets_the_snapshot_directory_read_only(self):
        compose = yaml.safe_load((DEPLOY_DIR / "docker-compose.yml").read_text())
        self.assertIn("./security-review:/security-review:ro", compose["services"]["review-app"]["volumes"])
        self.assertNotIn("./security-review:/security-review:ro", compose["services"]["nginx"]["volumes"])

    def test_collector_is_stdlib_only(self):
        import ast

        tree = ast.parse((DEPLOY_DIR / "security_snapshot.py").read_text())
        imported = {n.names[0].name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)}
        imported |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        self.assertLessEqual(imported, set(sys.stdlib_module_names) | {"__future__"})

    def test_reboot_config_enables_automatic_reboot(self):
        text = (DEPLOY_DIR / "52unattended-upgrades-reboot").read_text()
        self.assertIn('Unattended-Upgrade::Automatic-Reboot "true";', text)
        self.assertIn("Unattended-Upgrade::Automatic-Reboot-Time", text)


if __name__ == "__main__":
    unittest.main()
