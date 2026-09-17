# Automated Anomaly Detection & Maintainer Alerting

This document describes the design, operational thresholds, alerting rules, and runbooks for the automated anomaly detection system on `daugavpils.fans` (GitHub issue #47).

---

## 1. Overview & Architecture

The anomaly detection engine audits system telemetry stored locally in `review.db` and on server storage volumes (`review-app-data/`). It monitors for statistical deviations, adversarial behavior, AI approval drift, traffic shifts, and pipeline failures.

Key principles:
- **Zero external tracking**: Strictly analyzes privacy-preserving metrics (`visitor_hash`, aggregated event counters, proposal logs). No raw IP addresses or persistent cross-site cookies are stored.
- **Deduplication**: Alerts for the same root cause (e.g. repeated stuck workflows or continuous traffic spikes) are automatically deduplicated over configurable time windows (6 to 24 hours).
- **Multi-tiered alerting**:
  - `CRITICAL`: Immediate email alert dispatched to `MAINTAINER_EMAIL`.
  - `WARNING` & `INFO`: Tracked in database and surfaced on the Admin Dashboard (`/admin/anomalies/`) and in the daily health digest.

---

## 2. Detection Domains & Thresholds

### Domain 1: AI Evaluation Drift & Dodgy Submissions
Module: `anomaly_detector.check_ai_anomalies`

| Anomaly Type | Severity | Condition & Threshold | Rationale |
| :--- | :--- | :--- | :--- |
| `borderline_approval` | `WARNING` | AI auto-approved proposal with confidence near minimum cutoff: $0.80 \le \text{conf} < 0.85$ (text/media) or $0.90 \le \text{conf} < 0.93$ (albums/bands). | Identifies submissions that barely met auto-approval criteria for human verification. |
| `approval_velocity` | `CRITICAL` | $> 3$ auto-approvals in 1 hour or $> 5$ auto-approvals in 24 hours. | Protects against automated bot submission attacks exploiting LLM acceptance. |
| `pre_filter_conflict` | `CRITICAL` | Proposal was marked `approve` by LLM but triggers heuristic pre-filters (prompt injection, URL allowlist violation, audio slop syntax, length ceiling, homoglyph). | Catches prompt injection bypasses or subtle vandalism. |
| `ai_service_degradation`| `WARNING` | $\ge 3$ evaluation timeouts/errors or $> 25\%$ failure rate in past 24 hours. | Identifies upstream LLM API outages or rate limits. |
| `prompt_injection_probing` | `WARNING` | $\ge 2$ prompt injection or AI audio slop submissions from the same IP within 24 hours. | Flags adversarial actors attempting jailbreaks or LLM prompt exploitation. |

---

### Domain 2: Traffic & Visitor Analytics
Module: `anomaly_detector.check_traffic_anomalies`

Metrics are derived from `analytics_events`. 24-hour windows are evaluated against a 7-day daily rolling baseline (days -8 to -1).

| Anomaly Type | Severity | Condition & Threshold | Rationale |
| :--- | :--- | :--- | :--- |
| `traffic_surge` | `WARNING` | 24h pageviews $> 5\times$ 7-day daily baseline (minimum baseline $\ge 20$). | Catches viral traffic bursts, web crawler storms, or DDoS attempts. |
| `traffic_drop` | `WARNING` | 24h pageviews $< 5\%$ of baseline (when baseline $\ge 50$). | Detects DNS misconfigurations, routing drops, or CDN connectivity issues. |
| `visitor_clustering` | `WARNING` | Single `visitor_hash` represents $> 50\%$ of 24h pageviews (when total views $\ge 30$). | Catches automated scraping loops or aggressive site crawlers. |
| `referrer_flood` | `WARNING` | Single external referrer accounts for $> 60\%$ of external visits (when total external $\ge 30$). | Flags referrer spam or malicious link campaigns. |
| `media_error_spike` | `CRITICAL` / `WARNING` | $\ge 5$ `media_error` events in 24h AND error rate $\ge 20\%$ of all playback events (`CRITICAL` if $\ge 50\%$ or $\ge 15$ errors). | Alerts to archive.org CDN downtime, broken media files, or blocked audio streaming. |

---

### Domain 3: Submission & Authentication
Module: `anomaly_detector.check_submission_and_auth_anomalies`

Logs audited: `submission_log`, `login_request_log`, `admin_login_request_log`.

| Anomaly Type | Severity | Condition & Threshold | Rationale |
| :--- | :--- | :--- | :--- |
| `submission_flood` | `WARNING` | Single IP $\ge 15$ submissions in 1h, or total submissions across all IPs $\ge 50$ in 1h. | Identifies scripted form spam hitting the public submission endpoints. |
| `auth_probing` | `WARNING` | Single IP $\ge 5$ login attempts in 1h, or total login requests $\ge 15$ in 1h. | Flags credential enumeration or magic link inbox spamming. |
| `throttle_saturation` | `INFO` | Band or album daily publication limit reached (3 albums or 1 band per 24h) while approved items wait in queue. | Informs maintainers that approved submissions are waiting for rolling throttles to clear. |

---

### Domain 4: Operational & Server Integrity
Module: `anomaly_detector.check_operational_anomalies`

| Anomaly Type | Severity | Condition & Threshold | Rationale |
| :--- | :--- | :--- | :--- |
| `workflow_failure` | `CRITICAL` | Proposal in `apply_failed` or `publish_failed` status, or stuck in `applying`/`publishing` for $> 1$ hour. | Indicates GitHub Actions dispatch failure, git rebase conflict, or archive.org upload crash. |
| `orphaned_uploads` | `WARNING` | Unreferenced files located in `uploads/` directory that are not part of any pending/approved proposal. | Prevents disk leakage from aborted or rejected uploads. |
| `database_growth` | `WARNING` / `CRITICAL` | SQLite database file size exceeds 100 MB (`WARNING`) or 500 MB (`CRITICAL`). | Guards against unbounded growth of SQLite database. |
| `low_disk_space` | `WARNING` / `CRITICAL` | Free disk space on volume hosting `review.db` falls below 1 GB (`WARNING`) or 500 MB (`CRITICAL`). | Prevents disk exhaustion on server. |

---

## 3. Operations & CLI Usage

Run the audit on `cherry` or locally:

```bash
# Basic audit (returns exit code 1 if critical anomalies exist, otherwise 0)
python review_app/manage.py check-anomalies

# Audit with immediate maintainer notification on CRITICAL events
python review_app/manage.py check-anomalies --notify

# Audit with daily health digest email sent to MAINTAINER_EMAIL
python review_app/manage.py check-anomalies --notify --digest
```

### Scheduled Execution via Systemd

On the production server (`cherry`), a systemd timer or cron job executes the audit hourly:

```ini
# /etc/systemd/system/daugavpils-fans-anomalies.service
[Unit]
Description=daugavpils.fans Anomaly Detection Audit
After=network.target

[Service]
Type=oneshot
User=daugavpils-fans
WorkingDirectory=/opt/daugavpils-fans
EnvironmentFile=/opt/daugavpils-fans/review-app-data/review-app.env
ExecStart=/opt/daugavpils-fans/.venv/bin/python review_app/manage.py check-anomalies --notify

# /etc/systemd/system/daugavpils-fans-anomalies.timer
[Unit]
Description=Hourly daugavpils.fans anomaly audit timer

[Timer]
OnCalendar=hourly
Persistent=true

[Install]
WantedBy=timers.target
```

---

## 4. Admin Dashboard UI

Maintainers and curators can inspect system health and active anomalies at:
`https://daugavpils.fans/admin/anomalies/`

Features:
- **System Health Cards**: Live 24h summary of active critical/warning anomalies, 24h pageviews, media errors, database size, and server free disk space.
- **Status Filter**: Toggle between Active, All History, and Acknowledged anomalies.
- **Acknowledgment Action**: Curators can acknowledge reviewed anomalies, attaching their user account and timestamp to mark them resolved.
