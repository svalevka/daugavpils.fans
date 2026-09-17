"""
Automated anomaly detection and maintainer alerting engine (GitHub issue #47).
Monitors AI evaluation drift, suspicious submissions, visitor traffic surges,
authentication probing, workflow dispatch failures, and server health indicators.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ai_agent  # noqa: E402
import mail  # noqa: E402
from config import SmtpConfig  # noqa: E402

logger = logging.getLogger(__name__)

SEVERITY_CRITICAL = "CRITICAL"
SEVERITY_WARNING = "WARNING"
SEVERITY_INFO = "INFO"

CATEGORY_AI = "ai_approval"
CATEGORY_TRAFFIC = "traffic"
CATEGORY_AUTH = "auth"
CATEGORY_OPERATIONAL = "operational"


@dataclass(frozen=True)
class AnomalyEvent:
    category: str
    anomaly_type: str
    severity: str
    title: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    proposal_id: int | None = None
    proposal_type: str | None = None


# ---------------------------------------------------------------------------
# Database Helpers & Deduplication
# ---------------------------------------------------------------------------

def is_duplicate_anomaly(
    conn: sqlite3.Connection,
    anomaly_type: str,
    proposal_type: str | None = None,
    proposal_id: int | None = None,
    window_hours: int = 6,
) -> bool:
    """Checks whether an active/recent anomaly of the same type already exists
    to prevent spamming alerts on every tick."""
    if proposal_id is not None and proposal_type is not None:
        row = conn.execute(
            """
            SELECT id FROM anomaly_events
            WHERE anomaly_type = ?
              AND proposal_type = ?
              AND proposal_id = ?
              AND (acknowledged_at IS NULL OR created_at >= datetime('now', ?))
            LIMIT 1
            """,
            (anomaly_type, proposal_type, proposal_id, f"-{window_hours} hours"),
        ).fetchone()
        return row is not None

    # Global or aggregate anomaly: check within window
    row = conn.execute(
        """
        SELECT id FROM anomaly_events
        WHERE anomaly_type = ?
          AND (acknowledged_at IS NULL OR created_at >= datetime('now', ?))
        LIMIT 1
        """,
        (anomaly_type, f"-{window_hours} hours"),
    ).fetchone()
    return row is not None


def record_anomaly(
    conn: sqlite3.Connection,
    event: AnomalyEvent,
    *,
    notify_critical: bool = False,
    smtp_config: SmtpConfig | None = None,
    maintainer_email: str | None = None,
    dashboard_url: str | None = None,
) -> int | None:
    """Inserts an anomaly record if not duplicated, and sends an immediate alert
    if it is CRITICAL and notification is configured."""
    # Deduplication check
    window_hours = 24 if event.severity == SEVERITY_INFO else 6
    if is_duplicate_anomaly(conn, event.anomaly_type, event.proposal_type, event.proposal_id, window_hours=window_hours):
        return None

    details_str = json.dumps(event.details, ensure_ascii=False) if event.details else None
    cur = conn.execute(
        """
        INSERT INTO anomaly_events (
            category, anomaly_type, severity, title, message,
            details_json, proposal_id, proposal_type
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event.category,
            event.anomaly_type,
            event.severity,
            event.title,
            event.message,
            details_str,
            event.proposal_id,
            event.proposal_type,
        ),
    )
    conn.commit()
    anomaly_id = cur.lastrowid

    if event.severity == SEVERITY_CRITICAL and notify_critical and smtp_config and maintainer_email:
        anomaly_dict = {
            "id": anomaly_id,
            "category": event.category,
            "anomaly_type": event.anomaly_type,
            "severity": event.severity,
            "title": event.title,
            "message": event.message,
            "details": event.details,
            "proposal_id": event.proposal_id,
            "proposal_type": event.proposal_type,
        }
        try:
            mail.send_anomaly_alert(smtp_config, maintainer_email, anomaly_dict, dashboard_url=dashboard_url)
        except Exception:
            logger.exception("Failed to send critical anomaly email alert")

    return anomaly_id


def acknowledge_anomaly(conn: sqlite3.Connection, anomaly_id: int, user_id: int | None = None) -> bool:
    cur = conn.execute(
        """
        UPDATE anomaly_events
        SET acknowledged_at = datetime('now'),
            acknowledged_by = ?
        WHERE id = ? AND acknowledged_at IS NULL
        """,
        (user_id, anomaly_id),
    )
    conn.commit()
    return cur.rowcount > 0


def get_anomalies(
    conn: sqlite3.Connection,
    *,
    active_only: bool = False,
    severity: str | None = None,
    category: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []

    if active_only:
        clauses.append("acknowledged_at IS NULL")
    if severity:
        clauses.append("severity = ?")
        params.append(severity)
    if category:
        clauses.append("category = ?")
        params.append(category)

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    query = f"""
        SELECT a.id, a.created_at, a.category, a.anomaly_type, a.severity,
               a.title, a.message, a.details_json, a.proposal_id, a.proposal_type,
               a.acknowledged_at, a.acknowledged_by,
               u.display_name as acknowledged_by_name
        FROM anomaly_events a
        LEFT JOIN approvers u ON a.acknowledged_by = u.id
        {where_sql}
        ORDER BY
            CASE a.severity WHEN 'CRITICAL' THEN 1 WHEN 'WARNING' THEN 2 ELSE 3 END ASC,
            a.created_at DESC
        LIMIT ?
    """
    params.append(limit)
    rows = conn.execute(query, params).fetchall()

    results: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        if d.get("details_json"):
            try:
                d["details"] = json.loads(d["details_json"])
            except Exception:
                d["details"] = {}
        else:
            d["details"] = {}
        results.append(d)
    return results


# ---------------------------------------------------------------------------
# Domain 1: AI & Submission Anomalies
# ---------------------------------------------------------------------------

def check_ai_anomalies(conn: sqlite3.Connection) -> list[AnomalyEvent]:
    anomalies: list[AnomalyEvent] = []

    # 1. Borderline Approval Detection
    # Text and Media proposals: 0.80 <= confidence < 0.85
    # Album and Band proposals: 0.90 <= confidence < 0.93
    tables_config = [
        ("proposals", "edit", 0.80, 0.85),
        ("media_proposals", "media", 0.80, 0.85),
        ("album_proposals", "album", 0.90, 0.93),
        ("band_proposals", "band", 0.90, 0.93),
    ]

    for table, p_type, lower_bound, upper_bound in tables_config:
        query = f"""
            SELECT id, ai_confidence, ai_reasoning, status, created_at
            FROM {table}
            WHERE ai_decision = 'approve'
              AND ai_confidence >= ?
              AND ai_confidence < ?
              AND status IN ('approved', 'applied', 'published', 'publishing', 'applying')
              AND created_at >= datetime('now', '-7 days')
        """
        rows = conn.execute(query, (lower_bound, upper_bound)).fetchall()
        for r in rows:
            conf = float(r["ai_confidence"])
            anomalies.append(
                AnomalyEvent(
                    category=CATEGORY_AI,
                    anomaly_type="borderline_approval",
                    severity=SEVERITY_WARNING,
                    title=f"Borderline AI approval for {p_type} #{r['id']}",
                    message=(
                        f"Autonomous approval confidence ({conf:.2f}) is near the threshold "
                        f"[{lower_bound:.2f}-{upper_bound:.2f}). Please verify submission."
                    ),
                    details={
                        "proposal_id": r["id"],
                        "proposal_type": p_type,
                        "confidence": conf,
                        "reasoning": r["ai_reasoning"],
                        "status": r["status"],
                    },
                    proposal_id=r["id"],
                    proposal_type=p_type,
                )
            )

    # 2. Autonomous Approval Velocity Spikes
    # Alert if > 3 approvals in 1 hour or > 5 approvals in 24 hours across all tables
    velocity_queries = [
        ("-1 hour", 3, "1 hour", SEVERITY_CRITICAL),
        ("-24 hours", 5, "24 hours", SEVERITY_CRITICAL),
    ]
    for window, limit, window_label, sev in velocity_queries:
        total_approvals = 0
        approval_details: list[dict[str, Any]] = []
        for table, p_type, _, _ in tables_config:
            count_rows = conn.execute(
                f"""
                SELECT id, ai_confidence, ai_evaluated_at
                FROM {table}
                WHERE ai_decision = 'approve'
                  AND datetime(COALESCE(ai_evaluated_at, created_at)) >= datetime('now', ?)
                """,
                (window,),
            ).fetchall()
            total_approvals += len(count_rows)
            for cr in count_rows:
                approval_details.append({"table": table, "id": cr["id"], "type": p_type})

        if total_approvals > limit:
            anomalies.append(
                AnomalyEvent(
                    category=CATEGORY_AI,
                    anomaly_type="approval_velocity",
                    severity=sev,
                    title=f"Rapid AI approval velocity spike ({total_approvals} in {window_label})",
                    message=(
                        f"Autonomous approval velocity ({total_approvals} approvals) exceeded normal limit "
                        f"({limit} in {window_label}). Potential bot campaign exploiting AI acceptance."
                    ),
                    details={
                        "approvals_count": total_approvals,
                        "threshold": limit,
                        "window": window_label,
                        "proposals": approval_details[:20],
                    },
                )
            )

    # 3. Pre-Filter vs Model Disagreement (Approved despite heuristic warning)
    # Check text proposals
    text_rows = conn.execute(
        """
        SELECT id, band_slug, release_slug, target, list_index, field, proposed_value,
               submitter_name, submitter_contact, ai_confidence, ai_reasoning
        FROM proposals
        WHERE ai_decision = 'approve'
          AND created_at >= datetime('now', '-7 days')
        """
    ).fetchall()
    for r in text_rows:
        p_dict = dict(r)
        proposed_raw = r["proposed_value"]
        try:
            p_dict["proposed_value"] = json.loads(proposed_raw)
        except Exception:
            p_dict["proposed_value"] = proposed_raw

        warning = ai_agent.check_deterministic_text_proposal(p_dict)
        if not warning:
            # Check prompt injection heuristic
            text_to_check = f"{p_dict.get('proposed_value', '')} {p_dict.get('submitter_name', '')} {p_dict.get('submitter_contact', '')}"
            inj = ai_agent.detect_prompt_injection(text_to_check)
            if inj:
                warning = f"Prompt injection pattern detected ({inj})"

        if warning:
            anomalies.append(
                AnomalyEvent(
                    category=CATEGORY_AI,
                    anomaly_type="pre_filter_conflict",
                    severity=SEVERITY_CRITICAL,
                    title=f"Pre-filter conflict on approved text proposal #{r['id']}",
                    message=f"Model approved proposal #{r['id']} despite pre-filter violation: {warning}",
                    details={
                        "proposal_id": r["id"],
                        "proposal_type": "edit",
                        "field": r["field"],
                        "violation": warning,
                        "reasoning": r["ai_reasoning"],
                    },
                    proposal_id=r["id"],
                    proposal_type="edit",
                )
            )

    # Check media proposals
    media_rows = conn.execute(
        """
        SELECT id, band_slug, release_slug, media_type, caption, submitter_name,
               submitter_contact, source_url, youtube_title, ai_confidence, ai_reasoning
        FROM media_proposals
        WHERE ai_decision = 'approve'
          AND created_at >= datetime('now', '-7 days')
        """
    ).fetchall()
    for r in media_rows:
        p_dict = dict(r)
        warning = ai_agent.check_deterministic_media_proposal(p_dict)
        if not warning:
            text_to_check = f"{p_dict.get('caption', '')} {p_dict.get('youtube_title', '')} {p_dict.get('submitter_name', '')}"
            inj = ai_agent.detect_prompt_injection(text_to_check)
            if inj:
                warning = f"Prompt injection pattern detected ({inj})"

        if warning:
            anomalies.append(
                AnomalyEvent(
                    category=CATEGORY_AI,
                    anomaly_type="pre_filter_conflict",
                    severity=SEVERITY_CRITICAL,
                    title=f"Pre-filter conflict on approved media proposal #{r['id']}",
                    message=f"Model approved media proposal #{r['id']} despite pre-filter violation: {warning}",
                    details={
                        "proposal_id": r["id"],
                        "proposal_type": "media",
                        "violation": warning,
                        "reasoning": r["ai_reasoning"],
                    },
                    proposal_id=r["id"],
                    proposal_type="media",
                )
            )

    # Check album proposals
    album_rows = conn.execute(
        """
        SELECT id, name, release_slug, band_slug, description, description_en,
               tracks_json, submitter_name, submitter_contact, ai_confidence, ai_reasoning
        FROM album_proposals
        WHERE ai_decision = 'approve'
          AND created_at >= datetime('now', '-7 days')
        """
    ).fetchall()
    for r in album_rows:
        p_dict = dict(r)
        try:
            p_dict["tracks"] = json.loads(r["tracks_json"])
        except Exception:
            p_dict["tracks"] = []
        warning = ai_agent.check_deterministic_album_proposal(p_dict)
        if not warning:
            text_to_check = f"{p_dict.get('name', '')} {p_dict.get('description', '')} {p_dict.get('description_en', '')}"
            inj = ai_agent.detect_prompt_injection(text_to_check)
            if inj:
                warning = f"Prompt injection pattern detected ({inj})"

        if warning:
            anomalies.append(
                AnomalyEvent(
                    category=CATEGORY_AI,
                    anomaly_type="pre_filter_conflict",
                    severity=SEVERITY_CRITICAL,
                    title=f"Pre-filter conflict on approved album proposal #{r['id']}",
                    message=f"Model approved album proposal #{r['id']} despite pre-filter violation: {warning}",
                    details={
                        "proposal_id": r["id"],
                        "proposal_type": "album",
                        "violation": warning,
                        "reasoning": r["ai_reasoning"],
                    },
                    proposal_id=r["id"],
                    proposal_type="album",
                )
            )

    # Check band proposals
    band_rows = conn.execute(
        """
        SELECT id, name, band_slug, description, description_en, has_release,
               release_name, release_description, release_tracks_json,
               submitter_name, submitter_contact, ai_confidence, ai_reasoning
        FROM band_proposals
        WHERE ai_decision = 'approve'
          AND created_at >= datetime('now', '-7 days')
        """
    ).fetchall()
    for r in band_rows:
        p_dict = dict(r)
        p_dict["band_name"] = r["name"]
        if r["has_release"] and r["release_tracks_json"]:
            try:
                p_dict["tracks"] = json.loads(r["release_tracks_json"])
            except Exception:
                p_dict["tracks"] = []
        warning = ai_agent.check_deterministic_band_proposal(p_dict)
        if not warning:
            text_to_check = f"{p_dict.get('name', '')} {p_dict.get('description', '')} {p_dict.get('release_name', '')}"
            inj = ai_agent.detect_prompt_injection(text_to_check)
            if inj:
                warning = f"Prompt injection pattern detected ({inj})"

        if warning:
            anomalies.append(
                AnomalyEvent(
                    category=CATEGORY_AI,
                    anomaly_type="pre_filter_conflict",
                    severity=SEVERITY_CRITICAL,
                    title=f"Pre-filter conflict on approved band proposal #{r['id']}",
                    message=f"Model approved band proposal #{r['id']} despite pre-filter violation: {warning}",
                    details={
                        "proposal_id": r["id"],
                        "proposal_type": "band",
                        "violation": warning,
                        "reasoning": r["ai_reasoning"],
                    },
                    proposal_id=r["id"],
                    proposal_type="band",
                )
            )

    # 4. AI Service Degradation / Error Spikes
    # Count failed evaluations in past 24h
    total_evals = 0
    failed_evals = 0
    error_reasons: list[str] = []
    for table, p_type, _, _ in tables_config:
        rows = conn.execute(
            f"""
            SELECT ai_decision, ai_reasoning
            FROM {table}
            WHERE datetime(COALESCE(ai_evaluated_at, created_at)) >= datetime('now', '-24 hours')
              AND ai_reasoning IS NOT NULL
            """
        ).fetchall()
        for r in rows:
            total_evals += 1
            reason = r["ai_reasoning"].lower()
            if (
                "error" in reason
                or "timeout" in reason
                or "timed out" in reason
                or "connection" in reason
                or "service unavailable" in reason
                or "failed" in reason
            ):
                failed_evals += 1
                error_reasons.append(r["ai_reasoning"])

    if failed_evals >= 3 or (total_evals >= 4 and (failed_evals / total_evals) > 0.25):
        fail_pct = (failed_evals / total_evals * 100) if total_evals else 100.0
        anomalies.append(
            AnomalyEvent(
                category=CATEGORY_AI,
                anomaly_type="ai_service_degradation",
                severity=SEVERITY_WARNING,
                title=f"AI service degradation ({failed_evals}/{total_evals} failed in 24h)",
                message=(
                    f"Elevated AI evaluation failure rate: {failed_evals} out of {total_evals} "
                    f"evaluations ({fail_pct:.1f}%) failed or timed out in the past 24 hours."
                ),
                details={
                    "total_evaluations": total_evals,
                    "failed_evaluations": failed_evals,
                    "failure_rate": fail_pct,
                    "sample_errors": error_reasons[:5],
                },
            )
        )

    # 5. Prompt Injection & Slop Probing by IP
    # Proposals in past 24h with prompt injection or AI audio slop tags
    ip_probes: dict[str, list[dict[str, Any]]] = {}
    for table, p_type, _, _ in tables_config:
        rows = conn.execute(
            f"""
            SELECT id, submitter_ip, ai_reasoning, review_notes
            FROM {table}
            WHERE datetime(created_at) >= datetime('now', '-24 hours')
              AND (
                ai_reasoning LIKE '%prompt injection%'
                OR ai_reasoning LIKE '%adversarial%'
                OR ai_reasoning LIKE '%ai audio slop%'
                OR ai_reasoning LIKE '%suno%'
                OR ai_reasoning LIKE '%udio%'
                OR review_notes LIKE '%prompt injection%'
                OR review_notes LIKE '%audio slop%'
              )
            """
        ).fetchall()
        for r in rows:
            ip = r["submitter_ip"]
            if ip:
                ip_probes.setdefault(ip, []).append(
                    {"id": r["id"], "type": p_type, "reason": r["ai_reasoning"] or r["review_notes"]}
                )

    for ip, events in ip_probes.items():
        if len(events) >= 2:
            anomalies.append(
                AnomalyEvent(
                    category=CATEGORY_AI,
                    anomaly_type="prompt_injection_probing",
                    severity=SEVERITY_WARNING,
                    title=f"Repeated prompt injection or slop probing from IP {ip}",
                    message=f"IP {ip} submitted {len(events)} adversarial or AI slop proposals within 24 hours.",
                    details={"ip": ip, "proposals_count": len(events), "proposals": events},
                )
            )

    return anomalies


# ---------------------------------------------------------------------------
# Domain 2: Traffic & Visitor Analytics Anomalies
# ---------------------------------------------------------------------------

def check_traffic_anomalies(conn: sqlite3.Connection) -> list[AnomalyEvent]:
    anomalies: list[AnomalyEvent] = []

    # 1. 24h Traffic Surge and Drop-off
    p24_pageviews = conn.execute(
        """
        SELECT COUNT(*) FROM analytics_events
        WHERE event_type = 'pageview'
          AND created_at >= datetime('now', '-24 hours')
        """
    ).fetchone()[0]

    # Baseline: daily average over previous 7 days (days -8 to -1)
    baseline_pageviews_row = conn.execute(
        """
        SELECT COUNT(*) * 1.0 / 7.0 FROM analytics_events
        WHERE event_type = 'pageview'
          AND created_at >= datetime('now', '-8 days')
          AND created_at < datetime('now', '-1 day')
        """
    ).fetchone()
    daily_baseline = float(baseline_pageviews_row[0] or 0.0)

    # Traffic Surge: > 5x baseline when baseline >= 20
    if daily_baseline >= 20.0 and p24_pageviews > (5.0 * daily_baseline):
        ratio = p24_pageviews / daily_baseline
        anomalies.append(
            AnomalyEvent(
                category=CATEGORY_TRAFFIC,
                anomaly_type="traffic_surge",
                severity=SEVERITY_WARNING,
                title=f"Sudden traffic surge ({p24_pageviews} views, {ratio:.1f}x baseline)",
                message=(
                    f"24-hour pageviews ({p24_pageviews}) surged to {ratio:.1f}x of the "
                    f"7-day daily baseline ({daily_baseline:.1f})."
                ),
                details={
                    "pageviews_24h": p24_pageviews,
                    "daily_baseline_7d": daily_baseline,
                    "multiplier": ratio,
                },
            )
        )

    # Traffic Drop-off: < 5% of baseline when baseline >= 50
    if daily_baseline >= 50.0 and p24_pageviews < (0.05 * daily_baseline):
        anomalies.append(
            AnomalyEvent(
                category=CATEGORY_TRAFFIC,
                anomaly_type="traffic_drop",
                severity=SEVERITY_WARNING,
                title=f"Sudden traffic drop-off ({p24_pageviews} views vs {daily_baseline:.1f} baseline)",
                message=(
                    f"24-hour pageviews ({p24_pageviews}) dropped well below expected baseline "
                    f"({daily_baseline:.1f}). Potential DNS, routing, or CDN outage."
                ),
                details={
                    "pageviews_24h": p24_pageviews,
                    "daily_baseline_7d": daily_baseline,
                },
            )
        )

    # 2. Visitor Hash Clustering (> 50% from single hash when total >= 30)
    if p24_pageviews >= 30:
        top_visitor = conn.execute(
            """
            SELECT visitor_hash, COUNT(*) as cnt
            FROM analytics_events
            WHERE event_type = 'pageview'
              AND created_at >= datetime('now', '-24 hours')
            GROUP BY visitor_hash
            ORDER BY cnt DESC
            LIMIT 1
            """
        ).fetchone()
        if top_visitor and (top_visitor["cnt"] / p24_pageviews) > 0.50:
            pct = (top_visitor["cnt"] / p24_pageviews) * 100
            anomalies.append(
                AnomalyEvent(
                    category=CATEGORY_TRAFFIC,
                    anomaly_type="visitor_clustering",
                    severity=SEVERITY_WARNING,
                    title=f"Extreme visitor concentration ({pct:.1f}% from single visitor)",
                    message=(
                        f"Single visitor hash accounted for {top_visitor['cnt']} of {p24_pageviews} "
                        f"pageviews ({pct:.1f}%) in 24 hours. Potential crawler or scraper loop."
                    ),
                    details={
                        "visitor_hash": top_visitor["visitor_hash"][:16] + "...",
                        "count": top_visitor["cnt"],
                        "total_pageviews": p24_pageviews,
                        "percentage": pct,
                    },
                )
            )

    # 3. Spam or Crawler Referrer Flood (> 60% of external referrers when external >= 30)
    external_ref_total = conn.execute(
        """
        SELECT COUNT(*) FROM analytics_events
        WHERE event_type = 'pageview'
          AND created_at >= datetime('now', '-24 hours')
          AND referrer_domain NOT IN ('Direct', 'Internal', '', 'daugavpils.fans')
          AND referrer_domain IS NOT NULL
        """
    ).fetchone()[0]

    if external_ref_total >= 30:
        top_ref = conn.execute(
            """
            SELECT referrer_domain, COUNT(*) as cnt
            FROM analytics_events
            WHERE event_type = 'pageview'
              AND created_at >= datetime('now', '-24 hours')
              AND referrer_domain NOT IN ('Direct', 'Internal', '', 'daugavpils.fans')
              AND referrer_domain IS NOT NULL
            GROUP BY referrer_domain
            ORDER BY cnt DESC
            LIMIT 1
            """
        ).fetchone()
        if top_ref and (top_ref["cnt"] / external_ref_total) > 0.60:
            pct = (top_ref["cnt"] / external_ref_total) * 100
            anomalies.append(
                AnomalyEvent(
                    category=CATEGORY_TRAFFIC,
                    anomaly_type="referrer_flood",
                    severity=SEVERITY_WARNING,
                    title=f"Spam or crawler referrer surge from {top_ref['referrer_domain']}",
                    message=(
                        f"Unusual concentration from referrer '{top_ref['referrer_domain']}': "
                        f"{top_ref['cnt']}/{external_ref_total} ({pct:.1f}%) external visits in 24 hours."
                    ),
                    details={
                        "referrer_domain": top_ref["referrer_domain"],
                        "count": top_ref["cnt"],
                        "total_external": external_ref_total,
                        "percentage": pct,
                    },
                )
            )

    # 4. Media Playback Error Spike
    media_errors = conn.execute(
        """
        SELECT COUNT(*) FROM analytics_events
        WHERE event_type = 'media_error'
          AND created_at >= datetime('now', '-24 hours')
        """
    ).fetchone()[0]

    total_media_events = conn.execute(
        """
        SELECT COUNT(*) FROM analytics_events
        WHERE event_type IN ('track_play', 'video_play', 'media_error')
          AND created_at >= datetime('now', '-24 hours')
        """
    ).fetchone()[0]

    if media_errors >= 5 and total_media_events > 0:
        error_rate = media_errors / total_media_events
        if error_rate >= 0.20:
            sev = SEVERITY_CRITICAL if (error_rate >= 0.50 or media_errors >= 15) else SEVERITY_WARNING
            anomalies.append(
                AnomalyEvent(
                    category=CATEGORY_TRAFFIC,
                    anomaly_type="media_error_spike",
                    severity=sev,
                    title=f"Elevated media playback error rate ({media_errors} errors, {error_rate * 100:.1f}%)",
                    message=(
                        f"Media playback error rate reached {error_rate * 100:.1f}% "
                        f"({media_errors} errors out of {total_media_events} playback attempts) in 24 hours. "
                        f"Potential archive.org CDN outage or broken audio/video files."
                    ),
                    details={
                        "media_errors": media_errors,
                        "total_playback_attempts": total_media_events,
                        "error_rate": error_rate * 100,
                    },
                )
            )

    return anomalies


# ---------------------------------------------------------------------------
# Domain 3: Submission & Authentication Anomalies
# ---------------------------------------------------------------------------

def check_submission_and_auth_anomalies(conn: sqlite3.Connection) -> list[AnomalyEvent]:
    anomalies: list[AnomalyEvent] = []

    # 1. Submission Floods (hourly spikes in submission_log)
    sub_1h_total = conn.execute(
        """
        SELECT COUNT(*) FROM submission_log
        WHERE submitted_at >= datetime('now', '-1 hour')
        """
    ).fetchone()[0]

    if sub_1h_total >= 50:
        anomalies.append(
            AnomalyEvent(
                category=CATEGORY_AUTH,
                anomaly_type="submission_flood",
                severity=SEVERITY_WARNING,
                title=f"Global submission flood ({sub_1h_total} submissions in 1 hour)",
                message=f"Unusually high submission rate across all IPs: {sub_1h_total} submissions in 1 hour.",
                details={"submissions_1h": sub_1h_total, "threshold": 50},
            )
        )

    top_sub_ip = conn.execute(
        """
        SELECT ip, COUNT(*) as cnt FROM submission_log
        WHERE submitted_at >= datetime('now', '-1 hour')
        GROUP BY ip
        ORDER BY cnt DESC
        LIMIT 1
        """
    ).fetchone()
    if top_sub_ip and top_sub_ip["cnt"] >= 15:
        anomalies.append(
            AnomalyEvent(
                category=CATEGORY_AUTH,
                anomaly_type="submission_flood",
                severity=SEVERITY_WARNING,
                title=f"Submission flood from IP {top_sub_ip['ip']} ({top_sub_ip['cnt']} in 1h)",
                message=f"Single IP {top_sub_ip['ip']} generated {top_sub_ip['cnt']} submissions in 1 hour.",
                details={"ip": top_sub_ip["ip"], "count": top_sub_ip["cnt"], "threshold": 15},
            )
        )

    # 2. Credential / Magic Link Probing
    login_1h_total = conn.execute(
        """
        SELECT (
            (SELECT COUNT(*) FROM login_request_log WHERE requested_at >= datetime('now', '-1 hour')) +
            (SELECT COUNT(*) FROM admin_login_request_log WHERE requested_at >= datetime('now', '-1 hour'))
        )
        """
    ).fetchone()[0]

    if login_1h_total >= 15:
        anomalies.append(
            AnomalyEvent(
                category=CATEGORY_AUTH,
                anomaly_type="auth_probing",
                severity=SEVERITY_WARNING,
                title=f"Login magic link probing surge ({login_1h_total} attempts in 1 hour)",
                message=f"Elevated login requests across all IPs: {login_1h_total} requests in 1 hour.",
                details={"login_requests_1h": login_1h_total, "threshold": 15},
            )
        )

    top_login_ip = conn.execute(
        """
        SELECT ip, SUM(cnt) as total_cnt FROM (
            SELECT ip, COUNT(*) as cnt FROM login_request_log
            WHERE requested_at >= datetime('now', '-1 hour')
            GROUP BY ip
            UNION ALL
            SELECT ip, COUNT(*) as cnt FROM admin_login_request_log
            WHERE requested_at >= datetime('now', '-1 hour')
            GROUP BY ip
        )
        GROUP BY ip
        ORDER BY total_cnt DESC
        LIMIT 1
        """
    ).fetchone()
    if top_login_ip and top_login_ip["total_cnt"] >= 5:
        anomalies.append(
            AnomalyEvent(
                category=CATEGORY_AUTH,
                anomaly_type="auth_probing",
                severity=SEVERITY_WARNING,
                title=f"Login magic link probing from IP {top_login_ip['ip']} ({top_login_ip['total_cnt']} in 1h)",
                message=f"Single IP {top_login_ip['ip']} made {top_login_ip['total_cnt']} login requests in 1 hour.",
                details={"ip": top_login_ip["ip"], "count": top_login_ip["total_cnt"], "threshold": 5},
            )
        )

    # 3. Rolling Throttle Saturation (Albums & Bands)
    # 3 albums in 24h limit, 1 band in 24h limit
    published_albums_24h = conn.execute(
        """
        SELECT COUNT(*) FROM album_proposals
        WHERE status = 'published'
          AND published_at >= datetime('now', '-24 hours')
        """
    ).fetchone()[0]
    pending_approved_albums = conn.execute(
        """
        SELECT COUNT(*) FROM album_proposals
        WHERE status = 'approved'
        """
    ).fetchone()[0]

    if published_albums_24h >= 3 and pending_approved_albums > 0:
        anomalies.append(
            AnomalyEvent(
                category=CATEGORY_AUTH,
                anomaly_type="throttle_saturation",
                severity=SEVERITY_INFO,
                title=f"Album publication limit saturated ({published_albums_24h}/3 in 24h)",
                message=(
                    f"Rolling 24-hour limit reached ({published_albums_24h}/3 albums published). "
                    f"{pending_approved_albums} approved album(s) waiting in queue."
                ),
                details={
                    "published_24h": published_albums_24h,
                    "limit": 3,
                    "pending_approved": pending_approved_albums,
                },
            )
        )

    published_bands_24h = conn.execute(
        """
        SELECT COUNT(*) FROM band_proposals
        WHERE status = 'published'
          AND published_at >= datetime('now', '-24 hours')
        """
    ).fetchone()[0]
    pending_approved_bands = conn.execute(
        """
        SELECT COUNT(*) FROM band_proposals
        WHERE status = 'approved'
        """
    ).fetchone()[0]

    if published_bands_24h >= 1 and pending_approved_bands > 0:
        anomalies.append(
            AnomalyEvent(
                category=CATEGORY_AUTH,
                anomaly_type="throttle_saturation",
                severity=SEVERITY_INFO,
                title=f"Band publication limit saturated ({published_bands_24h}/1 in 24h)",
                message=(
                    f"Rolling 24-hour limit reached ({published_bands_24h}/1 band published). "
                    f"{pending_approved_bands} approved band(s) waiting in queue."
                ),
                details={
                    "published_24h": published_bands_24h,
                    "limit": 1,
                    "pending_approved": pending_approved_bands,
                },
            )
        )

    return anomalies


# ---------------------------------------------------------------------------
# Domain 4: Operational & Storage Integrity Anomalies
# ---------------------------------------------------------------------------

def check_operational_anomalies(
    conn: sqlite3.Connection,
    *,
    database_path: Path | None = None,
    uploads_path: Path | None = None,
) -> list[AnomalyEvent]:
    anomalies: list[AnomalyEvent] = []

    # 1. Workflow Dispatch Failures
    # Check all four proposal tables
    proposal_tables = [
        ("proposals", "edit", "apply_error"),
        ("media_proposals", "media", "publish_error"),
        ("album_proposals", "album", "publish_error"),
        ("band_proposals", "band", "publish_error"),
    ]

    for table, p_type, err_col in proposal_tables:
        # Failed dispatches
        failed_rows = conn.execute(
            f"""
            SELECT id, status, {err_col} as err_msg, github_run_id, created_at
            FROM {table}
            WHERE status IN ('apply_failed', 'publish_failed')
            """
        ).fetchall()
        for r in failed_rows:
            anomalies.append(
                AnomalyEvent(
                    category=CATEGORY_OPERATIONAL,
                    anomaly_type="workflow_failure",
                    severity=SEVERITY_CRITICAL,
                    title=f"Workflow dispatch failed for {p_type} #{r['id']}",
                    message=f"Proposal #{r['id']} marked as '{r['status']}': {r['err_msg'] or 'Workflow execution error'}",
                    details={
                        "proposal_id": r["id"],
                        "proposal_type": p_type,
                        "status": r["status"],
                        "error": r["err_msg"],
                        "github_run_id": r["github_run_id"],
                    },
                    proposal_id=r["id"],
                    proposal_type=p_type,
                )
            )

        # Stuck in in-flight status ('applying' or 'publishing') for > 1 hour
        stuck_rows = conn.execute(
            f"""
            SELECT id, status, github_run_id, created_at
            FROM {table}
            WHERE status IN ('applying', 'publishing')
              AND datetime(created_at) <= datetime('now', '-1 hour')
            """
        ).fetchall()
        for r in stuck_rows:
            anomalies.append(
                AnomalyEvent(
                    category=CATEGORY_OPERATIONAL,
                    anomaly_type="workflow_failure",
                    severity=SEVERITY_CRITICAL,
                    title=f"Proposal {p_type} #{r['id']} stuck in '{r['status']}'",
                    message=f"Proposal #{r['id']} has been in '{r['status']}' status for over 1 hour without resolution.",
                    details={
                        "proposal_id": r["id"],
                        "proposal_type": p_type,
                        "status": r["status"],
                        "github_run_id": r["github_run_id"],
                        "created_at": r["created_at"],
                    },
                    proposal_id=r["id"],
                    proposal_type=p_type,
                )
            )

    # 2. Orphaned Uploads
    if uploads_path and uploads_path.is_dir():
        referenced_files: set[str] = set()

        # media_proposals
        for row in conn.execute("SELECT stored_filename FROM media_proposals WHERE stored_filename IS NOT NULL").fetchall():
            referenced_files.add(row["stored_filename"])

        # album_proposals
        for row in conn.execute(
            "SELECT cover_stored_filename, tracks_json FROM album_proposals WHERE cover_stored_filename IS NOT NULL OR tracks_json IS NOT NULL"
        ).fetchall():
            if row["cover_stored_filename"]:
                referenced_files.add(row["cover_stored_filename"])
            if row["tracks_json"]:
                try:
                    tracks = json.loads(row["tracks_json"])
                    for t in tracks:
                        if isinstance(t, dict) and t.get("stored_filename"):
                            referenced_files.add(t["stored_filename"])
                except Exception:
                    pass

        # band_proposals
        for row in conn.execute(
            """
            SELECT band_photo_stored_filename, release_cover_stored_filename, release_tracks_json
            FROM band_proposals
            WHERE band_photo_stored_filename IS NOT NULL
               OR release_cover_stored_filename IS NOT NULL
               OR release_tracks_json IS NOT NULL
            """
        ).fetchall():
            if row["band_photo_stored_filename"]:
                referenced_files.add(row["band_photo_stored_filename"])
            if row["release_cover_stored_filename"]:
                referenced_files.add(row["release_cover_stored_filename"])
            if row["release_tracks_json"]:
                try:
                    tracks = json.loads(row["release_tracks_json"])
                    for t in tracks:
                        if isinstance(t, dict) and t.get("stored_filename"):
                            referenced_files.add(t["stored_filename"])
                except Exception:
                    pass

        orphaned: list[str] = []
        orphaned_bytes = 0
        try:
            for entry in uploads_path.iterdir():
                if entry.is_file() and not entry.name.startswith("."):
                    if entry.name not in referenced_files:
                        orphaned.append(entry.name)
                        orphaned_bytes += entry.stat().st_size
        except Exception:
            logger.exception("Failed to scan uploads directory for orphaned files")

        if orphaned:
            mb = orphaned_bytes / (1024 * 1024)
            anomalies.append(
                AnomalyEvent(
                    category=CATEGORY_OPERATIONAL,
                    anomaly_type="orphaned_uploads",
                    severity=SEVERITY_WARNING,
                    title=f"Found {len(orphaned)} orphaned upload file(s) ({mb:.1f} MB)",
                    message=(
                        f"{len(orphaned)} unreferenced file(s) found in {uploads_path} "
                        f"consuming {mb:.1f} MB of disk space."
                    ),
                    details={
                        "orphaned_count": len(orphaned),
                        "total_bytes": orphaned_bytes,
                        "total_mb": mb,
                        "sample_files": orphaned[:10],
                    },
                )
            )

    # 3. Database Size Threshold
    if database_path and database_path.is_file():
        try:
            db_size = database_path.stat().st_size
            db_size_mb = db_size / (1024 * 1024)
            # Threshold: > 100 MB warning, > 500 MB critical
            if db_size_mb >= 100.0:
                sev = SEVERITY_CRITICAL if db_size_mb >= 500.0 else SEVERITY_WARNING
                anomalies.append(
                    AnomalyEvent(
                        category=CATEGORY_OPERATIONAL,
                        anomaly_type="database_growth",
                        severity=sev,
                        title=f"Database size reached {db_size_mb:.1f} MB",
                        message=f"SQLite database at {database_path} grew to {db_size_mb:.1f} MB.",
                        details={"database_path": str(database_path), "size_bytes": db_size, "size_mb": db_size_mb},
                    )
                )
        except Exception:
            pass

    # 4. Low Disk Space Threshold
    check_dir = database_path.parent if database_path else (uploads_path if uploads_path else Path.cwd())
    try:
        usage = shutil.disk_usage(check_dir)
        free_mb = usage.free / (1024 * 1024)
        # Threshold: < 1000 MB (1 GB) warning, < 500 MB critical
        if free_mb < 1000.0:
            sev = SEVERITY_CRITICAL if free_mb < 500.0 else SEVERITY_WARNING
            anomalies.append(
                AnomalyEvent(
                    category=CATEGORY_OPERATIONAL,
                    anomaly_type="low_disk_space",
                    severity=sev,
                    title=f"Low disk space on server ({free_mb:.1f} MB free)",
                    message=f"Free disk space on volume hosting {check_dir} is critically low: {free_mb:.1f} MB remaining.",
                    details={
                        "directory": str(check_dir),
                        "free_bytes": usage.free,
                        "total_bytes": usage.total,
                        "free_mb": free_mb,
                    },
                )
            )
    except Exception:
        pass

    return anomalies


# ---------------------------------------------------------------------------
# Comprehensive Audit Runner
# ---------------------------------------------------------------------------

def run_anomaly_detection(
    conn: sqlite3.Connection,
    *,
    database_path: Path | None = None,
    uploads_path: Path | None = None,
    notify: bool = False,
    smtp_config: SmtpConfig | None = None,
    maintainer_email: str | None = None,
    dashboard_url: str | None = None,
) -> list[dict[str, Any]]:
    """Runs all anomaly detection suites, records new anomalies into the database,
    optionally alerts on CRITICAL events, and returns all newly recorded anomalies."""
    all_events: list[AnomalyEvent] = []

    all_events.extend(check_ai_anomalies(conn))
    all_events.extend(check_traffic_anomalies(conn))
    all_events.extend(check_submission_and_auth_anomalies(conn))
    all_events.extend(
        check_operational_anomalies(conn, database_path=database_path, uploads_path=uploads_path)
    )

    recorded_anomalies: list[dict[str, Any]] = []
    for event in all_events:
        anomaly_id = record_anomaly(
            conn,
            event,
            notify_critical=notify,
            smtp_config=smtp_config,
            maintainer_email=maintainer_email,
            dashboard_url=dashboard_url,
        )
        if anomaly_id is not None:
            recorded_anomalies.append(
                {
                    "id": anomaly_id,
                    "category": event.category,
                    "anomaly_type": event.anomaly_type,
                    "severity": event.severity,
                    "title": event.title,
                    "message": event.message,
                    "details": event.details,
                    "proposal_id": event.proposal_id,
                    "proposal_type": event.proposal_type,
                }
            )

    return recorded_anomalies


def get_system_health_summary(
    conn: sqlite3.Connection,
    *,
    database_path: Path | None = None,
    uploads_path: Path | None = None,
) -> dict[str, Any]:
    """Computes a high-level health report for admin dashboard and digests."""
    active_anomalies = get_anomalies(conn, active_only=True)
    crit_count = sum(1 for a in active_anomalies if a.get("severity") == SEVERITY_CRITICAL)
    warn_count = sum(1 for a in active_anomalies if a.get("severity") == SEVERITY_WARNING)
    info_count = sum(1 for a in active_anomalies if a.get("severity") == SEVERITY_INFO)

    p24_pageviews = conn.execute(
        "SELECT COUNT(*) FROM analytics_events WHERE event_type = 'pageview' AND created_at >= datetime('now', '-24 hours')"
    ).fetchone()[0]

    p24_plays = conn.execute(
        "SELECT COUNT(*) FROM analytics_events WHERE event_type IN ('track_play', 'video_play') AND created_at >= datetime('now', '-24 hours')"
    ).fetchone()[0]

    p24_errors = conn.execute(
        "SELECT COUNT(*) FROM analytics_events WHERE event_type = 'media_error' AND created_at >= datetime('now', '-24 hours')"
    ).fetchone()[0]

    p24_approvals = 0
    for tbl in ["proposals", "media_proposals", "album_proposals", "band_proposals"]:
        cnt = conn.execute(
            f"SELECT COUNT(*) FROM {tbl} WHERE ai_decision = 'approve' AND datetime(COALESCE(ai_evaluated_at, created_at)) >= datetime('now', '-24 hours')"
        ).fetchone()[0]
        p24_approvals += cnt

    db_size_mb = (database_path.stat().st_size / (1024 * 1024)) if database_path and database_path.is_file() else 0.0

    disk_free_mb = 0.0
    check_dir = database_path.parent if database_path else (uploads_path if uploads_path else Path.cwd())
    try:
        disk_free_mb = shutil.disk_usage(check_dir).free / (1024 * 1024)
    except Exception:
        pass

    return {
        "active_critical": crit_count,
        "active_warning": warn_count,
        "active_info": info_count,
        "total_active": len(active_anomalies),
        "pageviews_24h": p24_pageviews,
        "media_plays_24h": p24_plays,
        "media_errors_24h": p24_errors,
        "ai_approvals_24h": p24_approvals,
        "database_size_mb": round(db_size_mb, 1),
        "disk_free_mb": round(disk_free_mb, 1),
    }
