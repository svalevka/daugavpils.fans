"""
Outbound email - the two things review_app ever sends: a magic login link
to an approver, and a new-submission notification to the maintainer (see
GitHub issue #12). Deliberately two small, separately-mockable functions
rather than one generic "send_mail" - the agreed test seam (see the
parent PRD's Testing Decisions) mocks real SMTP at exactly this boundary,
so tests never touch the network.
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


def send_submission_notification(smtp_config: SmtpConfig, recipients: list[str], summary: str) -> None:
    for to_addr in recipients:
        _send(smtp_config, to_addr, "New proposal to review on daugavpils.fans", summary)
