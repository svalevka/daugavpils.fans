#!/usr/bin/env python3
"""
Host security snapshot collector for the weekly security review (see
documentation/SECURITY_REVIEW.md).

Runs on `cherry` as root, but is never scheduled here: it is started by
daugavpils-fans-security-snapshot.path when review_app (asked by the
GitHub-scheduled security-review.yml workflow, over the authenticated
callback API) writes a fresh request nonce into its data volume. GitHub
owns the schedule; this script only answers. The nonce is echoed back in
the snapshot so the workflow can tell a fresh answer from a stale file -
a collector that silently stopped working is therefore detected by the
workflow, not hidden by it.

Deliberately read-only and facts-only: it collects, it never judges and
never changes anything. All severity decisions happen in
tools/security_review.py, in CI, off this box. Stdlib only, so it runs
with the system python3 and needs no venv.

Never collected: secret values (env files are only stat()ed, never
read), full process command lines (other services on this shared box
may carry secrets in argv), or visitor IP addresses from the web logs.

Trust boundary: the request file lives in a directory owned by the
review-app container's unprivileged user, so everything about it is
untrusted input to a root process - it is opened with O_NOFOLLOW, must be
a regular file, and its content is only ever used if it is exactly 32 hex
characters. Output goes to a separate root-owned directory the container
can only read.
"""
from __future__ import annotations

import base64
import binascii
import glob
import grp
import hashlib
import json
import os
import pwd
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

SCHEMA_VERSION = 1
NONCE_RE = re.compile(r"^[0-9a-f]{32}$")

BASE_DIR = Path(os.environ.get("BASE_DIR", "/opt/daugavpils-fans"))
REQUEST_FILE = Path(os.environ.get("SECURITY_REQUEST_FILE", str(BASE_DIR / "review-app-data" / "security-review-request")))
OUT_DIR = Path(os.environ.get("SECURITY_OUT_DIR", str(BASE_DIR / "security-review")))
# The review-app container's group (see review_app/Dockerfile) - the only
# non-root reader of the snapshot.
OUT_GID = int(os.environ.get("SECURITY_OUT_GID", "10001"))
NGINX_CONTAINER = os.environ.get("NGINX_CONTAINER", "daugavpils-fans-nginx")
REVIEW_DB = Path(os.environ.get("REVIEW_DB", str(BASE_DIR / "review-app-data" / "review.db")))
# A previous snapshot is only kept as the comparison baseline once it is
# at least this old, so a retried run doesn't overwrite last week's
# baseline with one taken minutes ago.
BASELINE_MIN_AGE_SECONDS = 24 * 3600

DAUGAVPILS_UNITS = [
    "daugavpils-fans-sync.timer",
    "daugavpils-fans-sync.service",
    "daugavpils-fans-cert-renew.timer",
    "daugavpils-fans-cert-renew.service",
    "daugavpils-fans-update-containers.timer",
    "daugavpils-fans-update-containers.service",
    "daugavpils-fans-rebuild.path",
    "daugavpils-fans-rebuild.service",
    "daugavpils-fans-security-snapshot.path",
]


class RequestError(Exception):
    pass


def read_nonce(request_file: Path) -> str:
    """The request file is attacker-influenceable (see module docstring)."""
    try:
        fd = os.open(request_file, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise RequestError(f"cannot open request file: {exc.strerror}") from exc
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise RequestError("request file is not a regular file")
        raw = os.read(fd, 128)
    finally:
        os.close(fd)
    nonce = raw.decode("ascii", errors="replace").strip()
    if not NONCE_RE.match(nonce):
        raise RequestError("request file does not contain a valid nonce")
    return nonce


class Collector:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def run(self, cmd: list[str], timeout: int = 60) -> str | None:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, errors="replace")
        except FileNotFoundError:
            self.errors.append(f"{cmd[0]}: not installed")
            return None
        except subprocess.TimeoutExpired:
            self.errors.append(f"{cmd[0]}: timed out after {timeout}s")
            return None
        except OSError as exc:
            self.errors.append(f"{cmd[0]}: {exc.strerror}")
            return None
        return proc.stdout

    def section(self, name: str, fn):
        """One broken collector must never cost the whole snapshot."""
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - deliberately broad, see docstring
            self.errors.append(f"{name}: {type(exc).__name__}: {exc}")
            return None


# ---------------------------------------------------------------- parsers


def parse_ss(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        addr, _, port = parts[4].rpartition(":")
        match = re.search(r'\(\("([^"]+)"', line)
        entry = {"proto": parts[0], "addr": addr, "port": port, "process": match.group(1) if match else ""}
        if entry not in out:
            out.append(entry)
    return out


SSHD_KEYS = (
    "port",
    "permitrootlogin",
    "passwordauthentication",
    "pubkeyauthentication",
    "kbdinteractiveauthentication",
    "permitemptypasswords",
    "allowusers",
    "allowgroups",
    "x11forwarding",
    "authorizedkeysfile",
)


def parse_sshd_config(text: str) -> dict:
    out: dict[str, str] = {}
    for line in text.splitlines():
        key, _, value = line.partition(" ")
        if key in SSHD_KEYS:
            out[key] = value.strip()
    return out


def parse_passwd(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        parts = line.split(":")
        if len(parts) < 7:
            continue
        out.append({"name": parts[0], "uid": int(parts[2]), "home": parts[5], "shell": parts[6]})
    return out


def parse_shadow(text: str) -> dict:
    """Never returns hashes - only which accounts have an empty password
    and which have a usable (non-locked) one."""
    empty, with_password = [], []
    for line in text.splitlines():
        parts = line.split(":")
        if len(parts) < 2:
            continue
        if parts[1] == "":
            empty.append(parts[0])
        elif not parts[1].startswith(("!", "*")):
            with_password.append(parts[0])
    return {"empty_password": sorted(empty), "password_set": sorted(with_password)}


def parse_authorized_keys(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        for i, part in enumerate(parts):
            if part.startswith(("ssh-", "ecdsa-", "sk-")) and i + 1 < len(parts):
                try:
                    blob = base64.b64decode(parts[i + 1], validate=True)
                except (binascii.Error, ValueError):
                    break
                fingerprint = base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")
                out.append(
                    {
                        "type": part,
                        "fingerprint": f"SHA256:{fingerprint}",
                        "comment": " ".join(parts[i + 2 :])[:80],
                        "has_options": i > 0,
                    }
                )
                break
    return out


def parse_apt_upgradable(text: str) -> dict:
    packages, security = [], []
    for line in text.splitlines():
        if "/" not in line or "[upgradable" not in line:
            continue
        name, _, rest = line.partition("/")
        packages.append(name)
        if "-security" in rest.split(" ", 1)[0]:
            security.append(name)
    return {"upgradable": len(packages), "security": len(security), "security_packages": security[:60]}


ACCEPTED_RE = re.compile(r"Accepted (\S+) for (\S+) from (\S+)")


def parse_ssh_journal(text: str) -> dict:
    failed = invalid = 0
    accepted: Counter = Counter()
    for line in text.splitlines():
        if "Failed password" in line or "Connection closed by authenticating user" in line:
            failed += 1
        if "Invalid user" in line:
            invalid += 1
        match = ACCEPTED_RE.search(line)
        if match:
            accepted[(match.group(2), match.group(3), match.group(1))] += 1
    return {
        "failed_auth": failed,
        "invalid_user": invalid,
        "accepted": [
            {"user": user, "from": src, "method": method, "count": count}
            for (user, src, method), count in sorted(accepted.items())
        ][:100],
    }


ACCESS_RE = re.compile(r'"(?:[A-Z]+) (\S+) [^"]*" (\d{3}) ')


def summarize_access_log(lines) -> dict:
    """Aggregate only - never visitor IPs (the project stores none, and
    this summary leaves the box)."""
    total = 0
    by_status: Counter = Counter()
    error_paths: Counter = Counter()
    for line in lines:
        match = ACCESS_RE.search(line)
        if not match:
            continue
        total += 1
        status = match.group(2)
        by_status[status] += 1
        if status[0] == "4" and status != "429":
            error_paths[match.group(1).split("?", 1)[0][:100]] += 1
    return {
        "requests": total,
        "by_status": dict(sorted(by_status.items())),
        "top_4xx_paths": [{"path": p, "count": c} for p, c in error_paths.most_common(25)],
    }


# ------------------------------------------------------------- collectors


def sha256_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def stat_entry(path: str) -> dict | None:
    try:
        st = os.lstat(path)
    except OSError:
        return None
    try:
        owner = pwd.getpwuid(st.st_uid).pw_name
    except KeyError:
        owner = str(st.st_uid)
    try:
        group = grp.getgrgid(st.st_gid).gr_name
    except KeyError:
        group = str(st.st_gid)
    kind = "dir" if stat.S_ISDIR(st.st_mode) else "link" if stat.S_ISLNK(st.st_mode) else "file"
    return {"path": path, "owner": owner, "group": group, "mode": oct(stat.S_IMODE(st.st_mode))[2:].zfill(4), "kind": kind}


def collect_file_modes(base: Path) -> list[dict]:
    paths = [
        str(base),
        str(base / "review-app.env"),
        str(base / "cloudflare.ini"),
        str(base / "docker-compose.yml"),
        str(base / "nginx" / "daugavpils.conf"),
        str(base / "review-app-data"),
        str(base / "review-app-data" / "review.db"),
        str(base / "review-app-data" / "youtube-cookies.txt"),
        str(base / "security-review"),
        "/etc/shadow",
        "/etc/sudoers",
        "/etc/ssh/sshd_config",
        "/root/.ssh",
        "/root/.ssh/authorized_keys",
        "/var/run/docker.sock",
    ]
    paths += sorted(glob.glob(str(base / "*.sh")))
    paths += sorted(glob.glob(str(base / "*.py")))
    paths += sorted(glob.glob(str(base / "certbot" / "conf" / "live" / "*" / "privkey.pem")))
    paths += sorted(glob.glob(str(base / "certbot" / "conf" / "archive" / "*" / "privkey*.pem")))
    paths += sorted(glob.glob("/etc/systemd/system/daugavpils-fans-*"))
    return [entry for entry in (stat_entry(p) for p in paths) if entry]


def collect_accounts(c: Collector) -> dict:
    users = parse_passwd(Path("/etc/passwd").read_text())
    login_shells = [u for u in users if not u["shell"].endswith(("nologin", "false", "sync"))]
    keys = {}
    for user in login_shells:
        key_file = Path(user["home"]) / ".ssh" / "authorized_keys"
        try:
            keys[user["name"]] = parse_authorized_keys(key_file.read_text(errors="replace"))
        except OSError:
            continue
    groups = {}
    for name in ("sudo", "docker", "adm", "root", "wheel"):
        try:
            groups[name] = sorted(grp.getgrnam(name).gr_mem)
        except KeyError:
            continue
    sudoers = []
    for path in [Path("/etc/sudoers"), *sorted(Path("/etc/sudoers.d").glob("*"))]:
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        sudoers.append(
            {
                "path": str(path),
                "sha256": hashlib.sha256(text.encode()).hexdigest(),
                "nopasswd": any("NOPASSWD" in ln and not ln.lstrip().startswith("#") for ln in text.splitlines()),
            }
        )
    return {
        "login_users": [{"name": u["name"], "uid": u["uid"], "shell": u["shell"]} for u in login_shells],
        "uid0": sorted(u["name"] for u in users if u["uid"] == 0),
        "shadow": parse_shadow(Path("/etc/shadow").read_text()),
        "groups": groups,
        "authorized_keys": keys,
        "sudoers": sudoers,
    }


def collect_docker(c: Collector) -> list[dict] | None:
    ids = c.run(["docker", "ps", "-aq"])
    if ids is None:
        return None
    ids = ids.split()
    if not ids:
        return []
    raw = c.run(["docker", "inspect", *ids])
    if not raw:
        return None
    out = []
    for item in json.loads(raw):
        host = item.get("HostConfig") or {}
        out.append(
            {
                "name": (item.get("Name") or "").lstrip("/"),
                "image": (item.get("Config") or {}).get("Image"),
                "image_id": (item.get("Image") or "")[:19],
                "created": item.get("Created"),
                "status": (item.get("State") or {}).get("Status"),
                "restart_count": item.get("RestartCount"),
                "user": (item.get("Config") or {}).get("User") or "",
                "privileged": bool(host.get("Privileged")),
                "cap_add": host.get("CapAdd") or [],
                "network_mode": host.get("NetworkMode"),
                "pid_mode": host.get("PidMode") or "",
                "published_ports": sorted(
                    f"{b.get('HostIp') or '*'}:{b.get('HostPort')}->{port}"
                    for port, bindings in (host.get("PortBindings") or {}).items()
                    for b in (bindings or [])
                ),
                "mounts": sorted(
                    f"{m.get('Source')}:{m.get('Destination')}:{'rw' if m.get('RW') else 'ro'}"
                    for m in (item.get("Mounts") or [])
                ),
            }
        )
    return sorted(out, key=lambda d: d["name"])


def collect_systemd(c: Collector) -> dict:
    failed = c.run(["systemctl", "--failed", "--no-legend", "--plain"]) or ""
    enabled = c.run(["systemctl", "list-unit-files", "--state=enabled", "--no-legend", "--plain"]) or ""
    units = {}
    for unit in DAUGAVPILS_UNITS:
        shown = c.run(["systemctl", "show", unit, "-p", "LoadState,ActiveState,UnitFileState,Result,ExecMainStatus"]) or ""
        units[unit] = dict(ln.split("=", 1) for ln in shown.splitlines() if "=" in ln)
    return {
        "failed": sorted(ln.split()[0] for ln in failed.splitlines() if ln.split()),
        "enabled": sorted(ln.split()[0] for ln in enabled.splitlines() if ln.split()),
        "daugavpils_units": units,
    }


def collect_cron() -> list[dict]:
    out = []
    patterns = ["/etc/crontab", "/etc/cron.d/*", "/etc/cron.hourly/*", "/etc/cron.daily/*", "/etc/cron.weekly/*", "/etc/cron.monthly/*", "/var/spool/cron/crontabs/*"]
    for pattern in patterns:
        for path in sorted(glob.glob(pattern)):
            digest = sha256_file(Path(path))
            if digest:
                out.append({"path": path, "sha256": digest})
    return out


def find_paths(c: Collector, args: list[str], limit: int, timeout: int = 180) -> dict:
    text = c.run(["find", *args], timeout=timeout)
    if text is None:
        return {"paths": [], "truncated": False, "failed": True}
    paths = sorted(p for p in text.splitlines() if p)
    return {"paths": paths[:limit], "truncated": len(paths) > limit}


def collect_nginx_log(c: Collector) -> dict | None:
    try:
        proc = subprocess.Popen(
            ["docker", "logs", "--since", "168h", NGINX_CONTAINER],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            errors="replace",
        )
    except OSError as exc:
        c.errors.append(f"docker logs: {exc.strerror}")
        return None
    try:
        return summarize_access_log(proc.stdout)
    finally:
        proc.stdout.close()
        proc.wait(timeout=30)


def collect_app_anomalies(db_path: Path) -> dict | None:
    if not db_path.exists():
        return None
    # immutable=1: a plain read-only open of a WAL-mode database still
    # has to create its -shm file next to it, which this service's
    # read-only sandbox forbids ("unable to open database file"). The
    # cost is that rows still only in the WAL can be missed - fine for a
    # 7-day summary.
    conn = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True, timeout=10)
    try:
        rows = conn.execute(
            "SELECT severity, anomaly_type, COUNT(*), SUM(acknowledged_at IS NULL) FROM anomaly_events "
            "WHERE created_at >= datetime('now', '-7 days') GROUP BY severity, anomaly_type ORDER BY 1, 2"
        ).fetchall()
    finally:
        conn.close()
    return {
        "last_7_days": [
            {"severity": sev, "type": kind, "count": count, "unacknowledged": unack or 0}
            for sev, kind, count, unack in rows
        ]
    }


def collect(nonce: str, base: Path = BASE_DIR) -> dict:
    c = Collector()
    started = time.time()
    snap: dict = {
        "schema_version": SCHEMA_VERSION,
        "nonce": nonce,
        "collected_at": int(started),
        "hostname": os.uname().nodename,
    }

    def os_info():
        release = dict(
            ln.split("=", 1) for ln in Path("/etc/os-release").read_text().splitlines() if "=" in ln
        )
        return {
            "pretty_name": release.get("PRETTY_NAME", "").strip('"'),
            "kernel": os.uname().release,
            "uptime_days": round(float(Path("/proc/uptime").read_text().split()[0]) / 86400, 1),
            "reboot_required": Path("/var/run/reboot-required").exists(),
        }

    def updates():
        info = parse_apt_upgradable(c.run(["apt", "list", "--upgradable"], timeout=120) or "")
        try:
            auto = Path("/etc/apt/apt.conf.d/20auto-upgrades").read_text()
        except OSError:
            auto = ""
        info["unattended_upgrades_enabled"] = bool(re.search(r'Unattended-Upgrade\s+"1"', auto))
        return info

    def firewall():
        # Read ufw's own files rather than running `ufw status`: that
        # needs to take locks under /run, which this service's
        # ProtectSystem=strict sandbox (rightly) does not allow.
        try:
            enabled = re.search(r"^ENABLED=(\w+)", Path("/etc/ufw/ufw.conf").read_text(), re.M)
            ufw_status = "Status: active" if enabled and enabled.group(1).lower() == "yes" else "Status: inactive"
        except OSError:
            ufw_status = "unknown"
        rules = []
        for name in ("user.rules", "user6.rules"):
            try:
                text = Path("/etc/ufw", name).read_text()
            except OSError:
                continue
            rules += [ln[len("### tuple ### "):].strip() for ln in text.splitlines() if ln.startswith("### tuple ### ")]
        f2b = c.run(["fail2ban-client", "status"]) or ""
        jails = re.search(r"Jail list:\s*(.*)", f2b)
        return {
            "ufw_status": ufw_status,
            "ufw_rules": rules[:60],
            "fail2ban_active": (c.run(["systemctl", "is-active", "fail2ban"]) or "").strip(),
            "fail2ban_jails": [j.strip() for j in jails.group(1).split(",")] if jails and jails.group(1).strip() else [],
        }

    def disk():
        usage = shutil.disk_usage("/")
        return {"total_gb": round(usage.total / 1e9, 1), "free_gb": round(usage.free / 1e9, 1), "used_percent": round(100 * usage.used / usage.total, 1)}

    snap["os"] = c.section("os", os_info)
    snap["updates"] = c.section("updates", updates)
    snap["listening"] = c.section("listening", lambda: parse_ss(c.run(["ss", "-tulpnH"]) or ""))
    snap["firewall"] = c.section("firewall", firewall)
    snap["sshd"] = c.section("sshd", lambda: parse_sshd_config(c.run(["sshd", "-T"]) or ""))
    snap["accounts"] = c.section("accounts", lambda: collect_accounts(c))
    snap["ssh_auth_7d"] = c.section(
        "ssh_auth",
        lambda: parse_ssh_journal(
            c.run(["journalctl", "-u", "ssh", "-u", "sshd", "--since", "-7d", "--no-pager", "-o", "cat"], timeout=120) or ""
        ),
    )
    snap["file_modes"] = c.section("file_modes", lambda: collect_file_modes(base))
    snap["suid_sgid"] = c.section(
        "suid_sgid",
        lambda: find_paths(c, ["/", "-xdev", "-type", "f", "(", "-perm", "-4000", "-o", "-perm", "-2000", ")"], 400),
    )
    snap["world_writable"] = c.section(
        "world_writable",
        lambda: find_paths(c, ["/etc", "/usr/local", str(base), "-xdev", "-type", "f", "-perm", "-0002"], 100),
    )
    snap["recently_modified"] = c.section(
        "recently_modified",
        lambda: find_paths(
            c,
            ["/etc", "/usr/local/bin", "/usr/local/sbin", "/root/.ssh", "-xdev", "-type", "f", "-mtime", "-8"],
            200,
        ),
    )
    snap["docker"] = c.section("docker", lambda: collect_docker(c))
    snap["systemd"] = c.section("systemd", lambda: collect_systemd(c))
    snap["cron"] = c.section("cron", collect_cron)
    snap["disk"] = c.section("disk", disk)
    snap["nginx_7d"] = c.section("nginx_log", lambda: collect_nginx_log(c))
    snap["app_anomalies"] = c.section("app_anomalies", lambda: collect_app_anomalies(REVIEW_DB))
    snap["collector_errors"] = c.errors
    snap["collect_seconds"] = round(time.time() - started, 1)
    return snap


# ----------------------------------------------------------------- output


def write_snapshot(snapshot: dict, out_dir: Path, gid: int | None = OUT_GID, now: float | None = None) -> None:
    """out_dir is root-owned, so nothing here follows attacker-placed
    paths; the write is still temp-file + rename so a reader never sees a
    half-written snapshot."""
    now = time.time() if now is None else now
    out_dir.mkdir(mode=0o750, exist_ok=True)
    current = out_dir / "snapshot.json"
    try:
        previous_collected = json.loads(current.read_text()).get("collected_at", 0)
        if now - previous_collected >= BASELINE_MIN_AGE_SECONDS:
            os.replace(current, out_dir / "previous.json")
    except (OSError, ValueError, AttributeError):
        pass

    tmp = out_dir / ".snapshot.json.tmp"
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640)
    with os.fdopen(fd, "w") as handle:
        json.dump(snapshot, handle, sort_keys=True)
        if gid is not None:
            os.fchown(handle.fileno(), -1, gid)
    os.replace(tmp, current)


def main() -> int:
    try:
        nonce = read_nonce(REQUEST_FILE)
    except RequestError as exc:
        print(f"security-snapshot: ignoring request: {exc}", file=sys.stderr)
        return 1
    snapshot = collect(nonce)
    write_snapshot(snapshot, OUT_DIR)
    if OUT_GID is not None:
        for name in (".", "previous.json"):
            try:
                os.chown(OUT_DIR / name, 0, OUT_GID)
            except OSError:
                pass
    print(
        f"security-snapshot: wrote snapshot for nonce {nonce[:8]}... in {snapshot['collect_seconds']}s "
        f"({len(snapshot['collector_errors'])} collector error(s))"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
