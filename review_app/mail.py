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

    with smtplib.SMTP(smtp_config.host, smtp_config.port) as smtp:
        # Gated on `user` rather than a separate "use TLS" flag: every
        # real relay this project sends through requires authentication,
        # and none of them accept plaintext AUTH, so "configured to
        # authenticate" and "needs STARTTLS first" are the same condition
        # in practice here. Local/dev config simply omits `user`, giving
        # a plain unauthenticated connection (e.g. to a local mail
        # sink) - not a third real-world case this needs to distinguish.
        if smtp_config.user:
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
