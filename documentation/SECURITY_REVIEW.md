# Weekly security review

Every Monday at 05:17 UTC, GitHub Actions reviews the security of the
`cherry` server and the daugavpils.fans application, and emails the
maintainer **only** when something needs attention:

- a **critical** issue,
- an **anomaly** - something on the server changed since last week's
  review, or the AI reviewer saw a pattern worth a human look,
- or the review itself **could not be completed**.

A quiet week produces no email. To get the full report anyway (all
warnings and informational notes included), run the workflow by hand
with `always_email` ticked:

```bash
gh workflow run security-review.yml -f always_email=true
```

## Why GitHub owns the schedule

A timer on `cherry` can stop firing without anyone noticing, and
`cherry` is also the thing being reviewed - a broken or compromised box
should not be in charge of deciding whether it gets reviewed. So nothing
on the server is scheduled. GitHub's cron starts every review, and a
server that does not answer is itself reported as a critical finding.

GitHub Actions still has **no SSH access** to `cherry` (the firewall is
never opened to GitHub's runners - see `webapp/deploy/README.md`). The
review reaches the host like this instead:

```
GitHub cron
  └─ tools/security_review.py  (on the runner)
       │ 1. POST /api/security-review/request      (bearer: REVIEW_APP_CALLBACK_KEY)
       ▼
     review_app container ── writes a one-time nonce to
       │                      review-app-data/security-review-request
       ▼
     systemd path unit on the host notices the file changed
       └─ security_snapshot.py runs as root, read-only, and writes
          /opt/daugavpils-fans/security-review/snapshot.json
          (root-owned; mounted read-only into the container)
       │ 2. GET /api/security-review/snapshot  - polled until the snapshot
       ▼    carries the same nonce (proof it is fresh, not last week's file)
     runner: rules + diff against last week + outside-in probes
             + dependency audit + z.ai assessment  →  email
```

Pieces, and where each lives:

| Piece | File | Runs |
|---|---|---|
| Schedule, dependency audit | `.github/workflows/security-review.yml` | GitHub Actions |
| Rules, diff, probes, AI call, email | `tools/security_review.py` | GitHub Actions |
| Request / snapshot endpoints | `review_app/api.py` (`/api/security-review/*`) | review-app container |
| Host fact collector | `webapp/deploy/security_snapshot.py` | `cherry`, as root, on request |
| Trigger | `webapp/deploy/daugavpils-fans-security-snapshot.{path,service}` | `cherry` systemd |

## What is checked

**On the host** (collected by `security_snapshot.py`, judged in CI):
pending security updates, whether unattended upgrades are on, pending
reboot; listening sockets; ufw and fail2ban state; effective sshd
settings; login-capable users, uid-0 accounts, empty passwords,
sudo/docker group members, SSH authorized-key fingerprints, sudoers file
hashes; SSH logins and failures over 7 days; ownership and permissions of
secrets, root-run scripts and systemd units; setuid binaries;
world-writable files; recently modified files under `/etc`; every
container's privileges, mounts and published ports; failed systemd units
and the state of the site's own timers; cron files; disk space; a
7-day summary of nginx responses; and review-app's own anomaly log
(`documentation/ANOMALY_DETECTION.md`).

**From the outside** (from the runner, an address on no allowlist): TLS
validity and days left for all three hostnames; security headers; that
files such as `/.git/config`, `/.env`, `/review.db` are not
downloadable; that `/api/backup` and friends refuse unauthenticated
callers; and a scan of ~20 common ports - anything but 80/443 answering
is critical, including SSH.

**In the code**: `pip-audit` against the three pinned requirement files,
and `bandit` (high severity + high confidence only).

**Changes since last week**: the collector keeps the previous snapshot
as a baseline. New or removed listening sockets, users, group
memberships, authorized keys, sudoers versions, setuid binaries,
containers, published ports, enabled systemd units, cron files, sshd
settings, and permission changes on tracked files are all reported as
anomalies. Changes you made yourself show up too - confirming them is
the point. Daily container image refreshes are deliberately not counted.

**AI assessment**: the snapshot, last week's snapshot, the probe results
and the rule findings go to the z.ai model for a second opinion. See the
next section for what it is and is not trusted with.

## Design rules

- **The AI can only add findings, never remove them.** Deterministic
  rules decide on their own. A z.ai outage, a malformed answer, or text
  planted in a log line to manipulate the model cannot talk a real
  finding out of the report. If the AI step fails, the report says so
  and the run is marked incomplete.
- **Nothing about the server is ever printed in CI.** This repository is
  public, so its Actions logs are too. Findings go to the email only -
  no log output, no artifacts, no issues. Keep it that way when editing
  the workflow (`tools/test_security_review.py` enforces part of this).
- **The collector only reads.** It never changes the system, never reads
  secret *values* (env files are only `stat()`ed; password hashes and
  key material are reduced to presence/fingerprints), never records full
  process command lines (other services share this box), and never
  includes visitor IP addresses from web logs. SSH login source
  addresses *are* included - that is the maintainer's own access trail.
- **The request file is untrusted.** It lives in a directory the
  container's unprivileged user owns, and a root process reads it. It is
  opened without following symlinks, must be a regular file, and is used
  only if it is exactly 32 hex characters. The collector's output goes
  to a separate root-owned directory the container can only read.
- **What leaves the box**: the snapshot travels to the GitHub runner
  over TLS and, trimmed, to z.ai. It describes the server's
  configuration; treat z.ai as a party that can see that description.

## Reading the email

Subject lines:

- `CRITICAL: N critical issue(s)` - act now.
- `N anomaly(ies) detected` - something changed; confirm it was you.
- `weekly review INCOMPLETE` - part of the review did not run; the body
  says which part. The workflow run is also marked failed.
- `weekly review - nothing critical` - only when run with `always_email`.

Findings tagged `[changed since last review]` come from the baseline
comparison; `[AI-assessed]` ones come from the model and deserve a
second look before acting.

## When the review cannot reach cherry

"The server could not be inspected this week" means the runner asked for
a snapshot and none came back with the right nonce within 7 minutes.
Outside-in checks still ran. On `cherry`, check in this order:

```bash
systemctl status daugavpils-fans-security-snapshot.path     # must be active (waiting)
journalctl -u daugavpils-fans-security-snapshot.service -n 30
ls -la /opt/daugavpils-fans/security-review/                 # root:10001, 0750
docker compose -f /opt/daugavpils-fans/docker-compose.yml ps # review-app up?
```

To exercise the host half by hand (written as the container's user, uid
10001 - a root-owned request file would lock review-app out of it):

```bash
python3 -c "import secrets; print(secrets.token_hex(16))" \
  | sudo setpriv --reuid 10001 --regid 10001 --clear-groups tee /opt/daugavpils-fans/review-app-data/security-review-request
journalctl -u daugavpils-fans-security-snapshot.service -f
```

## Configuration

GitHub repository secrets the workflow reads: `REVIEW_APP_CALLBACK_KEY`
(already used by the backup workflow), `ZAI_API_KEY`, `ZAI_BASE_URL`,
`ZAI_MODEL`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM`, `SMTP_USER`,
`SMTP_PASSWORD`, `SECURITY_REVIEW_EMAIL_TO`. The z.ai and SMTP values
are the same ones `review-app.env` holds on `cherry`; if you rotate
either there, update the GitHub secret too.

What counts as expected (public ports, wildcard listeners, which systemd
units must be active, which paths must not be downloadable) is a set of
constants at the top of `tools/security_review.py`. Change them there,
with a test.

Installing the host side: see "Weekly security review" in
`webapp/deploy/README.md`.
