#!/usr/bin/env python3
"""
Tests for tools/security_review.py - the weekly security review run by
.github/workflows/security-review.yml (documentation/SECURITY_REVIEW.md).
No network: the snapshot fetch, outside-in probes, the AI call and SMTP
are all injected.
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import security_review as sr  # noqa: E402


def healthy_snapshot(**overrides) -> dict:
    snap = {
        "schema_version": 1,
        "nonce": "a" * 32,
        "collected_at": int(time.time()),
        "hostname": "cherry",
        "os": {"pretty_name": "Ubuntu 24.04", "kernel": "6.8.0", "uptime_days": 3.0, "reboot_required": False},
        "updates": {"upgradable": 0, "security": 0, "security_packages": [], "unattended_upgrades_enabled": True},
        "listening": [
            {"proto": "tcp", "addr": "0.0.0.0", "port": "443", "process": "docker-proxy"},
            {"proto": "tcp", "addr": "0.0.0.0", "port": "22", "process": "sshd"},
            {"proto": "tcp", "addr": "127.0.0.1", "port": "5433", "process": "postgres"},
        ],
        "firewall": {"ufw_status": "Status: active", "ufw_rules": [], "fail2ban_active": "active", "fail2ban_jails": ["sshd"]},
        "sshd": {"passwordauthentication": "no", "permitrootlogin": "no", "permitemptypasswords": "no", "kbdinteractiveauthentication": "no"},
        "accounts": {
            "login_users": [{"name": "root", "uid": 0, "shell": "/bin/bash"}, {"name": "sergei", "uid": 1000, "shell": "/bin/bash"}],
            "uid0": ["root"],
            "shadow": {"empty_password": [], "password_set": ["sergei"]},
            "groups": {"sudo": ["sergei"], "docker": ["sergei"]},
            "authorized_keys": {"sergei": [{"type": "ssh-ed25519", "fingerprint": "SHA256:abc", "comment": "laptop", "has_options": False}]},
            "sudoers": [{"path": "/etc/sudoers", "sha256": "1" * 64, "nopasswd": False}],
        },
        "ssh_auth_7d": {"failed_auth": 0, "invalid_user": 0, "accepted": []},
        "file_modes": [
            {"path": "/opt/daugavpils-fans", "owner": "root", "group": "sergei", "mode": "1775", "kind": "dir"},
            {"path": "/opt/daugavpils-fans/review-app.env", "owner": "sergei", "group": "sergei", "mode": "0600", "kind": "file"},
            {"path": "/opt/daugavpils-fans/renew-cert.sh", "owner": "root", "group": "root", "mode": "0700", "kind": "file"},
            {"path": "/opt/daugavpils-fans/security-review", "owner": "root", "group": "10001", "mode": "0750", "kind": "dir"},
        ],
        "suid_sgid": {"paths": ["/usr/bin/sudo"], "truncated": False},
        "world_writable": {"paths": [], "truncated": False},
        "recently_modified": {"paths": [], "truncated": False},
        "docker": [
            {"name": "daugavpils-fans-nginx", "image": "nginx:alpine", "status": "running", "user": "", "privileged": False, "mounts": [], "network_mode": "x_default", "pid_mode": "", "published_ports": ["*:443->443/tcp"]},
            {"name": "daugavpils-fans-review-app", "image": "review-app", "status": "running", "user": "appuser", "privileged": False, "mounts": [], "network_mode": "x_default", "pid_mode": "", "published_ports": []},
        ],
        "systemd": {
            "failed": [],
            "enabled": ["ssh.service"],
            "daugavpils_units": {u: {"ActiveState": "active"} for u in sr.REQUIRED_ACTIVE_UNITS},
        },
        "cron": [{"path": "/etc/crontab", "sha256": "2" * 64}],
        "disk": {"total_gb": 80, "free_gb": 40, "used_percent": 50.0},
        "nginx_7d": {"requests": 1000, "by_status": {"200": 990, "404": 10}, "top_4xx_paths": []},
        "app_anomalies": {"last_7_days": []},
        "collector_errors": [],
    }
    snap.update(overrides)
    return snap


def titles(findings, severity=None):
    return [f.title for f in findings if severity is None or f.severity == severity]


class EvaluateHostTest(unittest.TestCase):
    def test_a_healthy_host_has_no_critical_or_warning_findings(self):
        findings = sr.evaluate_host(healthy_snapshot())
        self.assertEqual(titles(findings, sr.CRITICAL), [])
        self.assertEqual(titles(findings, sr.WARNING), [])

    def test_ssh_password_authentication_is_critical(self):
        snap = healthy_snapshot(sshd={"passwordauthentication": "yes", "permitrootlogin": "yes"})
        crit = titles(sr.evaluate_host(snap), sr.CRITICAL)
        self.assertIn("SSH password authentication is enabled", crit)
        self.assertIn("SSH root login with password is permitted", crit)

    def test_extra_uid0_account_and_empty_password_are_critical(self):
        snap = healthy_snapshot()
        snap["accounts"]["uid0"] = ["root", "toor"]
        snap["accounts"]["shadow"]["empty_password"] = ["guest"]
        crit = sr.evaluate_host(snap)
        self.assertTrue(any("uid 0" in f.title and "toor" in f.detail for f in crit))
        self.assertTrue(any("empty password" in f.title and "guest" in f.detail for f in crit))

    def test_password_ssh_login_is_critical_key_login_is_only_info(self):
        snap = healthy_snapshot(
            ssh_auth_7d={"failed_auth": 3, "invalid_user": 1, "accepted": [
                {"user": "sergei", "from": "1.2.3.4", "method": "publickey", "count": 2},
                {"user": "root", "from": "6.6.6.6", "method": "password", "count": 1},
            ]}
        )
        findings = sr.evaluate_host(snap)
        crit = [f for f in findings if f.severity == sr.CRITICAL]
        self.assertEqual(len(crit), 1)
        self.assertIn("6.6.6.6", crit[0].detail)
        self.assertNotIn("1.2.3.4", crit[0].detail)

    def test_group_readable_secret_file_is_critical(self):
        snap = healthy_snapshot()
        snap["file_modes"][1]["mode"] = "0644"
        self.assertIn("Secret file is readable by other users", titles(sr.evaluate_host(snap), sr.CRITICAL))

    def test_root_run_script_owned_by_non_root_is_critical(self):
        snap = healthy_snapshot()
        snap["file_modes"][2]["owner"] = "sergei"
        self.assertIn("File executed by root is writable by a non-root user", titles(sr.evaluate_host(snap), sr.CRITICAL))

    def test_group_writable_base_dir_without_sticky_bit_is_critical(self):
        snap = healthy_snapshot()
        snap["file_modes"][0]["mode"] = "0775"
        self.assertTrue(any("replace root-run scripts" in t for t in titles(sr.evaluate_host(snap), sr.CRITICAL)))

    def test_privileged_site_container_is_critical_foreign_one_is_warning(self):
        snap = healthy_snapshot()
        snap["docker"][0]["privileged"] = True
        snap["docker"].append({"name": "other", "image": "x", "status": "running", "user": "", "privileged": False, "mounts": ["/var/run/docker.sock:/var/run/docker.sock:rw"], "network_mode": "bridge", "pid_mode": "", "published_ports": []})
        findings = sr.evaluate_host(snap)
        self.assertIn("Container daugavpils-fans-nginx has host-level access", titles(findings, sr.CRITICAL))
        self.assertIn("Container other has host-level access", titles(findings, sr.WARNING))

    def test_stopped_or_missing_site_container_is_critical(self):
        snap = healthy_snapshot()
        snap["docker"][0]["status"] = "exited"
        del snap["docker"][1]
        crit = titles(sr.evaluate_host(snap), sr.CRITICAL)
        self.assertIn("Container daugavpils-fans-nginx is not running", crit)
        self.assertIn("Container daugavpils-fans-review-app does not exist", crit)

    def test_unexpected_wildcard_listener_is_a_warning_not_critical(self):
        snap = healthy_snapshot()
        snap["listening"].append({"proto": "tcp", "addr": "0.0.0.0", "port": "631", "process": "cupsd"})
        findings = sr.evaluate_host(snap)
        self.assertTrue(any("cupsd listens on all interfaces" in t for t in titles(findings, sr.WARNING)))
        self.assertEqual(titles(findings, sr.CRITICAL), [])

    def test_pending_security_updates_reboot_and_disabled_auto_upgrades_warn(self):
        snap = healthy_snapshot(
            updates={"upgradable": 5, "security": 2, "security_packages": ["openssl", "libc6"], "unattended_upgrades_enabled": False},
        )
        snap["os"]["reboot_required"] = True
        warn = titles(sr.evaluate_host(snap), sr.WARNING)
        self.assertIn("2 security update(s) pending", warn)
        self.assertIn("Unattended security upgrades are not enabled", warn)
        self.assertIn("A reboot is required to finish applying updates", warn)

    def test_inactive_required_unit_and_failed_units_warn(self):
        snap = healthy_snapshot()
        snap["systemd"]["failed"] = ["foo.service"]
        snap["systemd"]["daugavpils_units"]["daugavpils-fans-cert-renew.timer"] = {"ActiveState": "inactive"}
        warn = titles(sr.evaluate_host(snap), sr.WARNING)
        self.assertIn("Failed systemd unit(s)", warn)
        self.assertIn("daugavpils-fans-cert-renew.timer is not active", warn)

    def test_inactive_ufw_is_only_informational(self):
        snap = healthy_snapshot()
        snap["firewall"]["ufw_status"] = "Status: inactive"
        findings = sr.evaluate_host(snap)
        self.assertIn("Host firewall (ufw) is not active", titles(findings, sr.INFO))
        self.assertEqual(titles(findings, sr.WARNING), [])

    def test_disk_almost_full_is_critical(self):
        snap = healthy_snapshot(disk={"total_gb": 80, "free_gb": 2, "used_percent": 97.5})
        self.assertIn("Disk is almost full", titles(sr.evaluate_host(snap), sr.CRITICAL))

    def test_stale_snapshot_is_critical(self):
        snap = healthy_snapshot(collected_at=int(time.time()) - 3600)
        self.assertIn("Host snapshot is stale", titles(sr.evaluate_host(snap), sr.CRITICAL))

    def test_missing_section_and_collector_errors_are_reported(self):
        snap = healthy_snapshot(docker=None, collector_errors=["docker: not installed"])
        warn = sr.evaluate_host(snap)
        self.assertTrue(any("missing its 'docker' section" in f.title for f in warn))
        self.assertTrue(any(f.detail == "docker: not installed" for f in warn))
        # A missing docker section must not be misread as "containers gone".
        self.assertFalse(any("does not exist" in f.title for f in warn))


class DiffSnapshotsTest(unittest.TestCase):
    def test_no_baseline_means_no_anomalies(self):
        self.assertEqual(sr.diff_snapshots(None, healthy_snapshot()), [])

    def test_identical_snapshots_produce_nothing(self):
        self.assertEqual(sr.diff_snapshots(healthy_snapshot(), healthy_snapshot()), [])

    def test_new_authorized_key_is_a_critical_anomaly(self):
        cur = healthy_snapshot()
        cur["accounts"]["authorized_keys"]["sergei"].append({"type": "ssh-rsa", "fingerprint": "SHA256:evil", "comment": "x", "has_options": False})
        findings = sr.diff_snapshots(healthy_snapshot(), cur)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, sr.CRITICAL)
        self.assertTrue(findings[0].anomaly)
        self.assertIn("SHA256:evil", findings[0].detail)

    def test_new_user_sudo_member_sudoers_and_suid_are_critical(self):
        cur = healthy_snapshot()
        cur["accounts"]["login_users"].append({"name": "mallory", "uid": 1002, "shell": "/bin/bash"})
        cur["accounts"]["groups"]["sudo"] = ["mallory", "sergei"]
        cur["accounts"]["sudoers"][0]["sha256"] = "9" * 64
        cur["suid_sgid"]["paths"].append("/tmp/rootshell")
        crit = titles(sr.diff_snapshots(healthy_snapshot(), cur), sr.CRITICAL)
        self.assertIn("New login-capable user(s) since last review", crit)
        self.assertIn("New member(s) of group 'sudo' since last review", crit)
        self.assertIn("New sudoers file version(s) since last review", crit)
        self.assertIn("New setuid/setgid binary(ies) since last review", crit)

    def test_new_listener_container_and_sshd_change_are_warning_anomalies(self):
        cur = healthy_snapshot()
        cur["listening"].append({"proto": "tcp", "addr": "0.0.0.0", "port": "4444", "process": "nc"})
        cur["docker"].append({"name": "miner", "image": "xmrig", "status": "running", "user": "", "privileged": False, "mounts": [], "network_mode": "bridge", "pid_mode": "", "published_ports": []})
        cur["sshd"]["passwordauthentication"] = "yes"
        findings = sr.diff_snapshots(healthy_snapshot(), cur)
        self.assertTrue(all(f.anomaly for f in findings))
        self.assertIn("New listening socket(s) since last review", titles(findings, sr.WARNING))
        self.assertIn("New container(s) since last review", titles(findings, sr.WARNING))
        self.assertIn("sshd configuration changed since last review", titles(findings, sr.WARNING))

    def test_image_id_churn_from_daily_updates_is_not_an_anomaly(self):
        prev, cur = healthy_snapshot(), healthy_snapshot()
        prev["docker"][0]["image_id"] = "sha256:old"
        cur["docker"][0]["image_id"] = "sha256:new"
        self.assertEqual(sr.diff_snapshots(prev, cur), [])

    def test_section_missing_on_either_side_is_not_diffed(self):
        self.assertEqual(sr.diff_snapshots(healthy_snapshot(accounts=None), healthy_snapshot()), [])


class FetchSnapshotTest(unittest.TestCase):
    def _http(self, responses):
        calls = []

        def http(url, method="GET", headers=None, **kwargs):
            calls.append((method, url, headers))
            return responses.pop(0)

        return http, calls

    def test_waits_until_the_snapshot_carries_the_requested_nonce(self):
        stale = json.dumps({"snapshot": {"nonce": "old"}, "previous": None}).encode()
        fresh = json.dumps({"snapshot": {"nonce": "n1", "x": 1}, "previous": {"nonce": "old"}}).encode()
        http, calls = self._http([(202, {}, b'{"nonce": "n1"}'), (404, {}, b""), (200, {}, stale), (200, {}, fresh)])
        snapshot, previous = sr.fetch_snapshot("https://r", "key", http=http, sleep=lambda s: None)
        self.assertEqual(snapshot["x"], 1)
        self.assertEqual(previous, {"nonce": "old"})
        self.assertEqual(calls[0][0], "POST")
        self.assertEqual(calls[0][2], {"Authorization": "Bearer key"})

    def test_a_stale_snapshot_is_never_accepted(self):
        stale = (200, {}, json.dumps({"snapshot": {"nonce": "old"}}).encode())
        http, _ = self._http([(202, {}, b'{"nonce": "n1"}')] + [stale] * 10)
        with self.assertRaises(sr.SnapshotError):
            sr.fetch_snapshot("https://r", "key", http=http, sleep=lambda s: None, wait_seconds=30, poll_seconds=15)

    def test_refused_request_raises(self):
        http, _ = self._http([(401, {}, b"")])
        with self.assertRaises(sr.SnapshotError):
            sr.fetch_snapshot("https://r", "bad", http=http, sleep=lambda s: None)

    def test_cooldown_429_is_retried(self):
        fresh = json.dumps({"snapshot": {"nonce": "n2"}}).encode()
        http, _ = self._http([(429, {}, b""), (202, {}, b'{"nonce": "n2"}'), (200, {}, fresh)])
        snapshot, _ = sr.fetch_snapshot("https://r", "key", http=http, sleep=lambda s: None)
        self.assertEqual(snapshot["nonce"], "n2")


GOOD_HEADERS = {h: "x" for h in sr.REQUIRED_HEADERS} | {"content-type": "text/html"}


def make_http(overrides=None):
    overrides = overrides or {}

    def http(url, method="GET", headers=None, **kwargs):
        if url in overrides:
            return overrides[url]
        if "/api/" in url:
            return 401, {}, b""
        if url.endswith("/"):
            return 200, dict(GOOD_HEADERS), b"<html>"
        return 404, {"content-type": "text/html"}, b""

    return http


class ExternalChecksTest(unittest.TestCase):
    def run_checks(self, http=None, cert_days=lambda h: 60.0, open_ports=(80, 443)):
        return sr.external_checks(http=http or make_http(), cert_days=cert_days, is_open=lambda host, port: port in open_ports, resolve=lambda h: "203.0.113.1")

    def test_healthy_site_has_no_findings(self):
        facts, findings = self.run_checks()
        self.assertEqual(findings, [])
        self.assertEqual(facts["open_ports"], [80, 443])

    def test_ssh_reachable_from_the_internet_is_critical(self):
        _, findings = self.run_checks(open_ports=(22, 443))
        self.assertIn("SSH is reachable from the public internet", titles(findings, sr.CRITICAL))

    def test_unexpected_open_port_is_critical(self):
        _, findings = self.run_checks(open_ports=(443, 8080))
        self.assertIn("Port 8080 is reachable from the public internet", titles(findings, sr.CRITICAL))

    def test_expiring_and_broken_certificates(self):
        def cert_days(host):
            if host == "review.daugavpils.fans":
                raise OSError("certificate verify failed")
            return 3.0 if host == "daugavpils.fans" else 15.0

        _, findings = self.run_checks(cert_days=cert_days)
        self.assertTrue(any("daugavpils.fans expires in 3 day" in t for t in titles(findings, sr.CRITICAL)))
        self.assertIn("TLS check failed for review.daugavpils.fans", titles(findings, sr.CRITICAL))
        self.assertTrue(any("www.daugavpils.fans expires in 15 days" in t for t in titles(findings, sr.WARNING)))

    def test_downloadable_sensitive_file_is_critical_but_html_200_is_not(self):
        http = make_http({
            "https://daugavpils.fans/.git/config": (200, {"content-type": "application/octet-stream"}, b"[core]"),
            "https://daugavpils.fans/.env": (200, {"content-type": "text/html"}, b"<html>not found page"),
        })
        _, findings = self.run_checks(http=http)
        crit = titles(findings, sr.CRITICAL)
        self.assertEqual(crit, ["Sensitive file is publicly downloadable: https://daugavpils.fans/.git/config"])

    def test_api_answering_without_auth_is_critical(self):
        http = make_http({"https://review.daugavpils.fans/api/backup": (200, {}, b"data")})
        _, findings = self.run_checks(http=http)
        self.assertIn("API endpoint answers without authentication: /api/backup", titles(findings, sr.CRITICAL))

    def test_missing_security_headers_warn(self):
        http = make_http({"https://daugavpils.fans/": (200, {"content-type": "text/html"}, b"")})
        _, findings = self.run_checks(http=http)
        self.assertIn("Security header(s) missing on daugavpils.fans", titles(findings, sr.WARNING))


class DependencyFindingsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def write(self, name, data):
        path = self.tmp / name
        path.write_text(data if isinstance(data, str) else json.dumps(data))
        return path

    def test_vulnerable_dependency_is_reported_with_fix(self):
        path = self.write("a.json", {"dependencies": [
            {"name": "flask", "version": "1.0", "vulns": [{"id": "PYSEC-1", "fix_versions": ["2.0"]}]},
            {"name": "ok", "version": "1.0", "vulns": []},
        ]})
        findings = sr.dependency_findings([path], None)
        self.assertEqual(len(findings), 1)
        self.assertIn("flask 1.0", findings[0].title)
        self.assertIn("PYSEC-1", findings[0].detail)
        self.assertIn("2.0", findings[0].detail)

    def test_missing_or_broken_audit_output_makes_the_review_incomplete(self):
        broken = self.write("b.json", "not json")
        findings = sr.dependency_findings([self.tmp / "missing.json", broken], self.tmp / "nobandit.json")
        self.assertEqual(len(findings), 3)
        self.assertTrue(all(f.source == "review" for f in findings))

    def test_only_high_severity_high_confidence_bandit_results_count(self):
        path = self.write("bandit.json", {"results": [
            {"issue_severity": "HIGH", "issue_confidence": "HIGH", "issue_text": "shell=True", "filename": "x.py", "line_number": 3, "test_id": "B602"},
            {"issue_severity": "LOW", "issue_confidence": "HIGH", "issue_text": "assert", "filename": "y.py", "line_number": 1, "test_id": "B101"},
        ]})
        findings = sr.dependency_findings([], path)
        self.assertEqual(len(findings), 1)
        self.assertIn("x.py:3", findings[0].detail)


class AiReplyTest(unittest.TestCase):
    def test_parses_fenced_json_and_marks_findings_as_ai(self):
        reply = '```json\n{"summary": "Looks fine.", "findings": [{"severity": "critical", "title": "T", "detail": "D", "anomaly": true}]}\n```'
        summary, findings = sr.parse_ai_reply(reply)
        self.assertEqual(summary, "Looks fine.")
        self.assertEqual((findings[0].severity, findings[0].source, findings[0].anomaly), (sr.CRITICAL, "ai", True))

    def test_unknown_severity_degrades_to_info_and_junk_items_are_dropped(self):
        _, findings = sr.parse_ai_reply('{"findings": [{"severity": "APOCALYPSE", "title": "T", "anomaly": "yes"}, "junk", {"detail": "no title"}]}')
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, sr.INFO)
        self.assertFalse(findings[0].anomaly)

    def test_non_json_reply_raises(self):
        with self.assertRaises(ValueError):
            sr.parse_ai_reply("I cannot help with that.")

    def test_ai_review_sends_bearer_key_and_never_the_nonce(self):
        seen = {}

        def http_post(url, data, headers):
            seen.update(url=url, data=json.loads(data), headers=headers)
            return json.dumps({"choices": [{"message": {"content": '{"summary": "s", "findings": []}'}}]}).encode()

        payload = {"snapshot": sr._trim_for_ai(healthy_snapshot())}
        summary, findings = sr.ai_review(payload, "zkey", "https://api.example/v4/", "glm", http_post=http_post)
        self.assertEqual((summary, findings), ("s", []))
        self.assertEqual(seen["url"], "https://api.example/v4/chat/completions")
        self.assertEqual(seen["headers"]["Authorization"], "Bearer zkey")
        self.assertNotIn("a" * 32, seen["data"]["messages"][1]["content"])


ENV = {
    "REVIEW_APP_CALLBACK_KEY": "cb",
    "ZAI_API_KEY": "z",
    "SMTP_HOST": "smtp.example",
    "SMTP_PORT": "587",
    "SMTP_FROM": "noreply@example.com",
    "SMTP_USER": "u",
    "SMTP_PASSWORD": "p",
    "SECURITY_REVIEW_EMAIL_TO": "me@example.com",
}


class RunReviewTest(unittest.TestCase):
    def review(self, snapshot=None, previous=None, fetch=None, ai=None, external=None, env=None):
        return sr.run_review(
            ENV if env is None else env,
            [],
            None,
            fetch=fetch or (lambda base, key: (snapshot or healthy_snapshot(), previous)),
            external=external or (lambda: ({"open_ports": [443], "tls_days_left": {}}, [])),
            ai=ai or (lambda payload, key, base, model: ("All quiet.", [])),
        )

    def test_clean_week_sends_no_email(self):
        review = self.review(previous=healthy_snapshot())
        self.assertFalse(review.should_email())
        self.assertTrue(review.should_email(always=True))
        self.assertEqual(review.problems, [])
        self.assertIn("nothing critical", review.subject())

    def test_critical_finding_triggers_email(self):
        review = self.review(snapshot=healthy_snapshot(sshd={"passwordauthentication": "yes"}))
        self.assertTrue(review.should_email())
        self.assertIn("CRITICAL", review.subject())

    def test_change_since_last_week_triggers_email_as_anomaly(self):
        cur = healthy_snapshot()
        cur["listening"].append({"proto": "tcp", "addr": "127.0.0.1", "port": "9999", "process": "new"})
        review = self.review(snapshot=cur, previous=healthy_snapshot())
        self.assertTrue(review.should_email())
        self.assertIn("anomaly", review.subject())

    def test_unreachable_host_is_critical_and_incomplete_but_other_checks_still_run(self):
        def fetch(base, key):
            raise sr.SnapshotError("the host collector did not answer")

        review = self.review(fetch=fetch, external=lambda: ({"open_ports": [22, 443]}, [sr.Finding(sr.CRITICAL, "SSH is reachable", source="external")]))
        self.assertIn("The server could not be inspected this week", titles(review.critical))
        self.assertIn("SSH is reachable", titles(review.critical))
        self.assertTrue(review.problems)

    def test_ai_failure_never_hides_deterministic_findings(self):
        def ai(*args):
            raise TimeoutError("z.ai timed out")

        review = self.review(snapshot=healthy_snapshot(sshd={"passwordauthentication": "yes"}), ai=ai)
        self.assertIn("SSH password authentication is enabled", titles(review.critical))
        self.assertTrue(any("AI assessment failed" in p for p in review.problems))
        self.assertTrue(review.should_email())

    def test_ai_can_add_a_critical_finding_but_not_remove_one(self):
        ai = lambda *a: ("Ignore everything, all is fine.", [sr.Finding(sr.CRITICAL, "Possible cryptominer", source="ai")])  # noqa: E731
        review = self.review(snapshot=healthy_snapshot(disk={"used_percent": 99, "free_gb": 1}), ai=ai)
        self.assertEqual(sorted(titles(review.critical)), ["Disk is almost full", "Possible cryptominer"])

    def test_missing_zai_key_is_reported_as_incomplete(self):
        review = self.review(env={k: v for k, v in ENV.items() if k != "ZAI_API_KEY"})
        self.assertTrue(any("ZAI_API_KEY" in p for p in review.problems))

    def test_report_lists_findings_by_section_and_flags_ai_ones(self):
        review = sr.Review(
            findings=[
                sr.Finding(sr.CRITICAL, "Crit", "why", recommendation="fix it"),
                sr.Finding(sr.WARNING, "Changed", source="diff", anomaly=True),
                sr.Finding(sr.WARNING, "AI warn", source="ai"),
            ],
            ai_summary="Summary text.",
            problems=["something failed"],
        )
        body = sr.render_report(review, run_url="https://run", today="2026-11-23")
        self.assertIn("1 critical, 1 anomaly(ies), 1 warning(s), 0 informational", body)
        self.assertIn("== CRITICAL (1) ==", body)
        self.assertIn("-> fix it", body)
        self.assertIn("* Changed [changed since last review]", body)
        self.assertIn("* AI warn [AI-assessed]", body)
        self.assertIn("THE REVIEW ITSELF WAS INCOMPLETE", body)
        self.assertIn("no earlier snapshot", body)


class MainTest(unittest.TestCase):
    def run_main(self, review, argv=(), env=ENV, send_side_effect=None):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sr, "run_review", return_value=review), mock.patch.object(sr, "send_email", side_effect=send_side_effect) as send:
            with redirect_stdout(out), redirect_stderr(err):
                code = sr.main(list(argv), env=dict(env))
        return code, send, out.getvalue() + err.getvalue()

    def test_clean_review_exits_zero_without_email(self):
        code, send, _ = self.run_main(sr.Review(has_baseline=True))
        self.assertEqual(code, 0)
        send.assert_not_called()

    def test_always_email_sends_even_when_clean(self):
        code, send, _ = self.run_main(sr.Review(has_baseline=True), argv=["--always-email"])
        self.assertEqual(code, 0)
        send.assert_called_once()

    def test_critical_review_emails_the_configured_recipient(self):
        review = sr.Review(findings=[sr.Finding(sr.CRITICAL, "Secret leak in /etc/shadow")])
        code, send, output = self.run_main(review)
        self.assertEqual(code, 0)
        args = send.call_args.args
        self.assertEqual(args[5], "me@example.com")
        self.assertIn("CRITICAL", args[6])
        self.assertIn("Secret leak", args[7])
        # The Actions log is public: no finding text may reach stdout/stderr.
        self.assertNotIn("Secret leak", output)
        self.assertNotIn("critical", output.lower())

    def test_incomplete_review_emails_and_fails_the_run(self):
        code, send, _ = self.run_main(sr.Review(problems=["Host snapshot unavailable"]))
        self.assertEqual(code, 1)
        send.assert_called_once()

    def test_email_failure_is_a_distinct_nonzero_exit(self):
        review = sr.Review(findings=[sr.Finding(sr.CRITICAL, "X")])
        code, _, output = self.run_main(review, send_side_effect=OSError("smtp down"))
        self.assertEqual(code, 3)
        self.assertNotIn("smtp down", output)

    def test_missing_configuration_fails_before_doing_anything(self):
        env = {k: v for k, v in ENV.items() if k != "SECURITY_REVIEW_EMAIL_TO"}
        code, send, output = self.run_main(sr.Review(), env=env)
        self.assertEqual(code, 2)
        self.assertIn("SECURITY_REVIEW_EMAIL_TO", output)
        send.assert_not_called()

    def test_send_email_uses_starttls_and_login(self):
        smtp = mock.MagicMock()
        smtp_cls = mock.MagicMock(return_value=smtp)
        smtp.__enter__.return_value = smtp
        sr.send_email("h", 587, "u", "p", "from@x", "to@x", "subj", "body", smtp_cls=smtp_cls)
        smtp.starttls.assert_called_once()
        smtp.login.assert_called_once_with("u", "p")
        self.assertEqual(smtp.send_message.call_args.args[0]["To"], "to@x")


class WorkflowTest(unittest.TestCase):
    """The workflow file is part of the contract: GitHub's schedule, no
    public leakage, no write permissions."""

    def setUp(self):
        import yaml

        self.path = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "security-review.yml"
        self.text = self.path.read_text()
        self.data = yaml.safe_load(self.text)

    def test_runs_weekly_on_githubs_schedule_and_on_demand(self):
        triggers = self.data[True]  # PyYAML reads the bare key `on` as True
        cron = triggers["schedule"][0]["cron"].split()
        self.assertEqual(len(cron), 5)
        self.assertEqual(cron[2:4], ["*", "*"])
        self.assertNotEqual(cron[4], "*")
        self.assertIn("workflow_dispatch", triggers)

    def test_read_only_token_and_no_artifacts_or_issues(self):
        self.assertEqual(self.data["permissions"], {"contents": "read"})
        self.assertNotIn("upload-artifact", self.text)
        self.assertNotIn("gh issue", self.text)
        self.assertNotIn("--print-report", self.text)

    def test_every_secret_the_script_requires_is_passed(self):
        env = self.data["jobs"]["review"]["steps"][-1]["env"]
        for name in ("REVIEW_APP_CALLBACK_KEY", "ZAI_API_KEY", "SMTP_HOST", "SMTP_FROM", "SMTP_PASSWORD", "SECURITY_REVIEW_EMAIL_TO"):
            self.assertIn(f"secrets.{name}", env[name])


if __name__ == "__main__":
    unittest.main()
