"""
Outbound email - the three things review_app ever sends: a magic login
link to an approver, a new-submission notification to the maintainer (see
GitHub issue #12), and a your-photo-was-approved notification to a media
submitter who left a contact email (see GitHub issue #21 - text-proposal
submitters are deliberately never notified either way, but a media
submitter waiting on you to physically publish their file benefits from
knowing it was accepted). Deliberately small, separately-mockable
functions rather than one generic "send_mail" - the agreed test seam (see
the parent PRD's Testing Decisions) mocks real SMTP at exactly this
boundary, so tests never touch the network.
"""
from __future__ import annotations

import json
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import SmtpConfig  # noqa: E402


def _send(smtp_config: SmtpConfig, to_addr: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = smtp_config.from_addr
    msg["To"] = to_addr
    msg.set_content(body)

    client_cls = smtplib.SMTP_SSL if smtp_config.port == 465 else smtplib.SMTP
    with client_cls(smtp_config.host, smtp_config.port, timeout=30) as smtp:
        # Gated on `user` rather than a separate "use TLS" flag: every
        # real relay this project sends through requires authentication,
        # and none of them accept plaintext AUTH, so "configured to
        # authenticate" and "needs STARTTLS first" are the same condition
        # in practice here (unless already on port 465 SSL). Local/dev config
        # simply omits `user`, giving a plain unauthenticated connection
        # (e.g. to a local mail sink) - not a third real-world case this
        # needs to distinguish.
        if smtp_config.user:
            if smtp_config.port != 465:
                smtp.starttls()
            smtp.login(smtp_config.user, smtp_config.password)
        smtp.send_message(msg)


def send_magic_link(smtp_config: SmtpConfig, to_addr: str, link_url: str) -> None:
    _send(
        smtp_config,
        to_addr,
        "Your daugavpils.fans review login link",
        f"Click this link to log in (expires in 15 minutes, works once):\n\n{link_url}\n\n"
        "If you didn't request this, you can ignore this email.",
    )


def send_admin_magic_link(smtp_config: SmtpConfig, to_addr: str, link_url: str) -> None:
    _send(
        smtp_config,
        to_addr,
        "Your daugavpils.fans admin statistics login link",
        f"Click this link to log in to the admin statistics dashboard (expires in 15 minutes, works once):\n\n{link_url}\n\n"
        "If you didn't request this, you can ignore this email.",
    )


def send_welcome_invitation(
    smtp_config: SmtpConfig,
    to_addr: str,
    display_name: str,
    roles: list[str],
    login_url: str,
) -> None:
    roles_str = ", ".join(roles) if roles else "contributor"
    name_str = f" {display_name}" if display_name else ""
    _send(
        smtp_config,
        to_addr,
        "Welcome to daugavpils.fans administration",
        f"Hello{name_str},\n\n"
        f"You have been granted access to daugavpils.fans with the following role(s): {roles_str}.\n\n"
        f"You can log in using this secure link (expires in 15 minutes, works once):\n\n{login_url}\n\n"
        "Welcome to the team!",
    )


def send_submission_notification(smtp_config: SmtpConfig, recipients: list[str], summary: str) -> None:
    for to_addr in recipients:
        _send(smtp_config, to_addr, "New proposal to review on daugavpils.fans", summary)


def send_media_approved_notification(smtp_config: SmtpConfig, to_addr: str, description: str) -> None:
    """Sent once, when an approver approves a media proposal - not when
    it's actually published (see GitHub issue #21's design: publishing
    is a manual step that can take a while, so this is deliberately the
    fast "yes, we want this" signal rather than a "it's live" one)."""
    _send(
        smtp_config,
        to_addr,
        "Your daugavpils.fans submission was approved",
        f"Good news - {description} was approved and will be added to the archive soon.\n\n"
        "Thank you for contributing!",
    )


def send_youtube_fetch_failed_notification(
    smtp_config: SmtpConfig, to_addr: str, scope: str, error: str
) -> None:
    """Sent to the submitter when a YouTube-link submission's download
    fails (private/removed/age-restricted/live/too-large video, etc.) -
    see GitHub issue #49. There is no proposal left at that point for
    them to check on later, so this is the only signal they'll get."""
    _send(
        smtp_config,
        to_addr,
        "Your daugavpils.fans video link could not be fetched",
        f"Your YouTube video submission for {scope} could not be fetched:\n\n"
        f"{error}\n\n"
        "If this was a temporary issue (e.g. the video was age-restricted or "
        "region-locked), you're welcome to try a different link or upload the "
        "video file directly instead.",
    )


def send_youtube_fetch_failed_admin_notification(
    smtp_config: SmtpConfig, recipients: list[str], scope: str, source_url: str, error: str
) -> None:
    """Sent to approvers alongside send_youtube_fetch_failed_notification
    (see GitHub issue #49) - the proposal never reaches the approval
    queue, so without this an approver would have no way to know a
    submission was ever attempted."""
    subject = f"YouTube video fetch failed for {scope}"
    body = f"A YouTube video submission for {scope} could not be fetched.\n\nURL: {source_url}\nError: {error}\n"
    for to_addr in recipients:
        _send(smtp_config, to_addr, subject, body)


def send_ai_escalation_notification(
    smtp_config: SmtpConfig,
    recipients: list[str],
    *,
    proposal_id: int,
    target_summary: str,
    details: str,
    ai_decision: str,
    ai_confidence: float | None,
    ai_reasoning: str,
    dashboard_url: str,
    is_media: bool = False,
    is_shadow: bool = False,
) -> None:
    mode_prefix = "[Shadow Mode] " if is_shadow else "[Action Required] "
    media_label = "media proposal" if is_media else "proposal"
    subject = f"{mode_prefix}AI escalated {media_label} #{proposal_id} ({target_summary})"

    conf_str = f"{ai_confidence:.2f}" if ai_confidence is not None else "N/A"
    body = (
        f"AI Approval Agent evaluated {media_label} #{proposal_id} for {target_summary}.\n\n"
        f"AI Recommendation: {ai_decision.upper()} (Confidence: {conf_str})\n"
        f"AI Reasoning: {ai_reasoning}\n\n"
        f"--- Submission Details ---\n"
        f"{details}\n\n"
        f"Review and decide on the dashboard:\n{dashboard_url}\n"
    )
    for to_addr in recipients:
        _send(smtp_config, to_addr, subject, body)


TYPE_LABELS = {
    "edit": {"ru": "текстовые правки", "en": "text edit"},
    "media": {"ru": "фото / видео", "en": "photo / video"},
    "album": {"ru": "новый альбом", "en": "new album"},
    "band": {"ru": "новая группа", "en": "new band"},
}


def send_proposal_decision_notification(
    smtp_config: SmtpConfig,
    to_addr: str,
    *,
    decision: str,
    proposal_type: str,
    target_summary: str,
    review_notes: str | None = None,
    live_url: str | None = None,
    lang: str = "ru",
) -> None:
    """Notify a submitter who provided an email address about the decision
    (approved or rejected) on their proposal.
    """
    if not to_addr or "@" not in to_addr:
        return

    is_en = lang == "en"
    type_info = TYPE_LABELS.get(proposal_type, {})
    type_label = type_info.get("en" if is_en else "ru", proposal_type)

    if decision == "approved":
        if is_en:
            subject = "Your contribution was approved — daugavpils.fans"
            body_lines = [
                "Hello!",
                "",
                f"Your proposal for {target_summary} ({type_label}) has been approved and added to the archive.",
            ]
            if live_url:
                body_lines.extend(["", f"You can view it on the site:\n{live_url}"])
            if review_notes:
                body_lines.extend(["", f"Curator notes:\n{review_notes}"])
            body_lines.extend([
                "",
                "Thank you for contributing to the Daugavpils music archive!",
                "",
                "Best regards,",
                "The daugavpils.fans team",
            ])
        else:
            subject = "Ваше предложение принято — daugavpils.fans"
            body_lines = [
                "Здравствуйте!",
                "",
                f"Ваше предложение для {target_summary} ({type_label}) было принято и добавлено в архив.",
            ]
            if live_url:
                body_lines.extend(["", f"Вы можете увидеть обновления на сайте:\n{live_url}"])
            if review_notes:
                body_lines.extend(["", f"Комментарий куратора:\n{review_notes}"])
            body_lines.extend([
                "",
                "Спасибо за ваш вклад в сохранение музыкального архива Даугавпилса!",
                "",
                "С уважением,",
                "Команда daugavpils.fans",
            ])
    else:  # rejected
        if is_en:
            subject = "Your submission status — daugavpils.fans"
            body_lines = [
                "Hello!",
                "",
                "Thank you for your interest in daugavpils.fans.",
                "",
                f"Unfortunately, your proposal for {target_summary} ({type_label}) could not be accepted.",
            ]
            if review_notes:
                body_lines.extend(["", f"Reason / curator notes:\n{review_notes}"])
            body_lines.extend([
                "",
                "If you have additional materials or questions, feel free to submit an updated proposal on the site.",
                "",
                "Best regards,",
                "The daugavpils.fans team",
            ])
        else:
            subject = "Ваше предложение на daugavpils.fans"
            body_lines = [
                "Здравствуйте!",
                "",
                "Спасибо за интерес к проекту daugavpils.fans.",
                "",
                f"К сожалению, ваше предложение для {target_summary} ({type_label}) не было принято.",
            ]
            if review_notes:
                body_lines.extend(["", f"Причина / комментарий куратора:\n{review_notes}"])
            body_lines.extend([
                "",
                "Если у вас есть дополнительные материалы, уточнения или вопросы, вы можете отправить новую заявку на сайте.",
                "",
                "С уважением,",
                "Команда daugavpils.fans",
            ])

    _send(smtp_config, to_addr, subject, "\n".join(body_lines))


def send_anomaly_alert(
    smtp_config: SmtpConfig,
    to_addr: str,
    anomaly: dict,
    dashboard_url: str | None = None,
) -> None:
    """Send immediate email alert for critical anomalies to the maintainer."""
    severity = anomaly.get("severity", "CRITICAL")
    category = anomaly.get("category", "system")
    anomaly_type = anomaly.get("anomaly_type", "anomaly")
    title = anomaly.get("title", "Anomaly Detected")
    message = anomaly.get("message", "")
    details = anomaly.get("details_json") or anomaly.get("details")

    subject = f"[{severity} ALERT] daugavpils.fans: {title}"
    body_lines = [
        "Automated Anomaly Alert — daugavpils.fans",
        f"Severity: {severity}",
        f"Category: {category}",
        f"Anomaly Type: {anomaly_type}",
        "",
        "Summary:",
        f"{message}",
    ]
    if details:
        if isinstance(details, str):
            try:
                parsed = json.loads(details)
                body_lines.extend(["", "Details:", json.dumps(parsed, indent=2, ensure_ascii=False)])
            except Exception:
                body_lines.extend(["", "Details:", details])
        elif isinstance(details, dict):
            body_lines.extend(["", "Details:", json.dumps(details, indent=2, ensure_ascii=False)])

    if dashboard_url:
        body_lines.extend(["", f"Review in Admin Dashboard:\n{dashboard_url}"])

    body_lines.extend(["", "Best regards,", "daugavpils.fans automated observability"])
    _send(smtp_config, to_addr, subject, "\n".join(body_lines))


def send_anomaly_digest(
    smtp_config: SmtpConfig,
    to_addr: str,
    active_anomalies: list[dict],
    stats_summary: dict | None = None,
    dashboard_url: str | None = None,
) -> None:
    """Send periodic (e.g. daily) health check digest to the maintainer."""
    count = len(active_anomalies)
    crit_count = sum(1 for a in active_anomalies if a.get("severity") == "CRITICAL")
    warn_count = sum(1 for a in active_anomalies if a.get("severity") == "WARNING")

    status_str = "OK" if count == 0 else f"{count} active anomalies ({crit_count} critical, {warn_count} warning)"
    subject = f"[Health Digest] daugavpils.fans system status: {status_str}"

    body_lines = [
        "Daily System Health & Anomaly Digest — daugavpils.fans",
        f"Status: {status_str}",
        "",
    ]
    if stats_summary:
        body_lines.append("24-Hour Metrics Summary:")
        for k, v in stats_summary.items():
            body_lines.append(f"  - {k}: {v}")
        body_lines.append("")

    if active_anomalies:
        body_lines.append("Active Anomalies:")
        for idx, a in enumerate(active_anomalies, 1):
            sev = a.get("severity", "INFO")
            t = a.get("title", a.get("anomaly_type", "Anomaly"))
            msg = a.get("message", "")
            created = a.get("created_at", "")
            body_lines.append(f"{idx}. [{sev}] {t} ({created})")
            body_lines.append(f"   {msg}")
        body_lines.append("")
    else:
        body_lines.append("No active anomalies detected. All systems operating normally.\n")

    if dashboard_url:
        body_lines.append(f"Admin Dashboard:\n{dashboard_url}\n")

    body_lines.extend(["Best regards,", "daugavpils.fans automated observability"])
    _send(smtp_config, to_addr, subject, "\n".join(body_lines))

