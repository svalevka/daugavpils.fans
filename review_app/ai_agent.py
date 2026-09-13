"""
AI-driven autonomous approval agent for text and media submissions (GitHub issue #43).
Evaluates incoming proposals using Z-AI (GLM-5.1) or an OpenAI-compatible multimodal endpoint.

Modes:
- 'active': Auto-approves high-confidence proposals and triggers the appropriate
  workflow dispatch (apply-proposal.yml or apply-media-proposal.yml). Escalates
  ambiguous cases to maintainers via email.
- 'shadow': Evaluates proposals, records decisions/confidence/reasoning in SQLite,
  and sends shadow escalation emails, but does NOT trigger automatic git/upload dispatches.
- 'disabled': Does nothing; review_app falls back to standard manual review notifications.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from flask import Flask

import archive_read
import db
import github_dispatch
import mail
import roles
from config import AiConfig

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the automated approval agent for daugavpils.fans, a non-profit digital preservation archive dedicated to the underground and independent rock and metal music scene of Daugavpils, Latvia.
Your mission is to evaluate community submissions on behalf of the archive maintainer.

You must evaluate whether the submission should be approved or escalated to the human maintainer.

Guidelines for TEXT proposals:
- High confidence approval (APPROVE):
  - Correcting typos, spelling mistakes, punctuation, or grammar in artist names, track titles, or descriptions.
  - Adding or updating valid streaming, social, or discography links (e.g. Bandcamp, YouTube, Soundcloud, VK, Discogs).
  - Adding or expanding band history/biography with coherent, plausibly factual, non-vandalous information.
  - Adding valid new band members with plausible names, instruments/roles, and time periods.
- Escalation (ESCALATE):
  - Submissions containing spam, advertising, unrelated commercial links, or SEO keywords.
  - Obscene, offensive, harassing, or defamatory language.
  - Submissions that delete large sections of content, blank out text, or replace genuine information with nonsense.
  - Radical historical claims that contradict known scene history without any source or citation.
  - Ambiguous or uncertain content where you are not confident.

Guidelines for MEDIA proposals (photos/videos):
- High confidence approval (APPROVE):
  - Authentic archival photos of the band, live concerts, rehearsals, musicians, album/cassette artwork, posters, flyers, or ticket stubs matching the band or release context.
  - Reasonable image quality and plausible caption.
- Escalation (ESCALATE):
  - Memes, unrelated graphics, stock photos with watermarks, random selfies unrelated to the band.
  - Explicit, offensive, or inappropriate imagery.
  - Images that are completely unidentifiable, extremely corrupt, or irrelevant to the music scene.
  - Videos with unclear or suspicious metadata.
  - Uncertain relevance to the band or release.

You MUST respond strictly with a valid JSON object with the following schema:
{
  "decision": "approve" | "escalate",
  "confidence": 0.0 to 1.0,
  "reasoning": "A concise explanation (1-2 sentences) of why this decision was reached.",
  "spam_or_vandalism": boolean
}
Do not include any conversational filler outside the JSON object.
"""


@dataclass(frozen=True)
class EvaluationResult:
    decision: str  # "approve" | "escalate"
    confidence: float
    reasoning: str
    spam_or_vandalism: bool


def parse_ai_response(text: str) -> EvaluationResult:
    """Parses and validates the JSON output from the AI model."""
    cleaned = text.strip()
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        cleaned = match.group(0)
    try:
        data = json.loads(cleaned)
    except Exception:
        return EvaluationResult(
            decision="escalate",
            confidence=0.0,
            reasoning="AI output could not be parsed as JSON.",
            spam_or_vandalism=False,
        )

    decision = str(data.get("decision", "escalate")).strip().lower()
    if decision not in ("approve", "escalate"):
        decision = "escalate"

    try:
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    reasoning = str(data.get("reasoning", "No reasoning provided.")).strip()
    spam_or_vandalism = bool(data.get("spam_or_vandalism", False))

    return EvaluationResult(
        decision=decision,
        confidence=confidence,
        reasoning=reasoning,
        spam_or_vandalism=spam_or_vandalism,
    )


def call_ai_api(
    ai_config: AiConfig,
    user_prompt: str,
    image_bytes: bytes | None = None,
    image_mime: str | None = None,
) -> str:
    """Calls the Z-AI / OpenAI-compatible endpoint with optional image attachment."""
    if not ai_config.api_key:
        raise ValueError("AI API key is not configured")

    headers = {
        "Authorization": f"Bearer {ai_config.api_key}",
        "Content-Type": "application/json",
    }

    if image_bytes:
        mime = image_mime or "image/jpeg"
        b64 = base64.b64encode(image_bytes).decode("ascii")
        user_content: Any = [
            {"type": "text", "text": user_prompt},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
        ]
    else:
        user_content = user_prompt

    payload = {
        "model": ai_config.model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.1,
    }

    url = f"{ai_config.base_url.rstrip('/')}/chat/completions"
    response = requests.post(url, headers=headers, json=payload, timeout=ai_config.timeout_seconds)
    response.raise_for_status()
    data = response.json()
    choices = data.get("choices", [])
    if not choices:
        raise ValueError("AI API returned no completion choices")
    return choices[0]["message"]["content"]


def build_text_proposal_prompt(
    proposal: dict[str, Any], band_name: str, release_name: str | None = None
) -> str:
    scope = f"{band_name} ({proposal['band_slug']})"
    if release_name:
        scope += f" / Release: {release_name} ({proposal['release_slug']})"
    target = proposal["target"]
    field = proposal["field"]
    orig = proposal["original_value"]
    prop = proposal["proposed_value"]
    submitter_name = proposal.get("submitter_name") or "(anonymous)"
    submitter_contact = proposal.get("submitter_contact") or "(none)"

    return (
        f"Proposal Scope: {scope}\n"
        f"Target: {target}\n"
        f"Field: {field}\n"
        f"Submitter: {submitter_name} <{submitter_contact}>\n\n"
        f"Original Value:\n{json.dumps(orig, ensure_ascii=False, indent=2)}\n\n"
        f"Proposed Value:\n{json.dumps(prop, ensure_ascii=False, indent=2)}\n\n"
        f"Evaluate this proposed edit according to the archive guidelines. Output JSON only."
    )


def build_media_proposal_prompt(
    proposal: dict[str, Any], band_name: str, release_name: str | None = None
) -> str:
    scope = f"{band_name} ({proposal['band_slug']})"
    if release_name:
        scope += f" / Release: {release_name} ({proposal['release_slug']})"
    media_type = proposal["media_type"]
    original_filename = proposal["original_filename"]
    caption = proposal.get("caption") or "(no caption provided)"
    submitter_name = proposal.get("submitter_name") or "(anonymous)"
    submitter_contact = proposal.get("submitter_contact") or "(none)"

    return (
        f"Media Proposal Scope: {scope}\n"
        f"Media Type: {media_type}\n"
        f"Original Filename: {original_filename}\n"
        f"Submitted Caption: {caption}\n"
        f"Submitter: {submitter_name} <{submitter_contact}>\n\n"
        f"Evaluate this media submission according to the archive guidelines. Output JSON only."
    )


def _get_dashboard_url(app: Flask) -> str:
    base = app.config.get("BASE_URL") or os.environ.get("REVIEW_APP_BASE_URL", "https://review.daugavpils.fans")
    return f"{base.rstrip('/')}/dashboard"


def process_proposal_with_ai(app: Flask, proposal_id: int) -> None:
    """Evaluates a text proposal and performs autonomous approval or escalation."""
    with app.app_context():
        ai_config: AiConfig = app.config.get("AI_CONFIG") or AiConfig()
        if ai_config.mode == "disabled":
            return

        conn = db.get_connection()
        row = conn.execute(
            "SELECT * FROM proposals WHERE id = ? AND status = 'pending'", (proposal_id,)
        ).fetchone()
        if row is None:
            return

        proposal_dict = dict(row)
        try:
            proposal_dict["original_value"] = json.loads(row["original_value"])
        except Exception:
            proposal_dict["original_value"] = row["original_value"]
        try:
            proposal_dict["proposed_value"] = json.loads(row["proposed_value"])
        except Exception:
            proposal_dict["proposed_value"] = row["proposed_value"]

        checkout = app.config["ARCHIVE_CHECKOUT_PATH"]
        band_name = proposal_dict["band_slug"]
        release_name = None
        try:
            band = archive_read.get_band(checkout, proposal_dict["band_slug"])
            band_name = band.name
            if proposal_dict.get("release_slug"):
                release = archive_read.get_release(
                    checkout, proposal_dict["band_slug"], proposal_dict["release_slug"]
                )
                release_name = release.name
        except Exception:
            pass

        user_prompt = build_text_proposal_prompt(proposal_dict, band_name, release_name)

        try:
            raw_response = call_ai_api(ai_config, user_prompt)
            result = parse_ai_response(raw_response)
        except Exception as exc:
            logger.exception("AI evaluation failed for proposal %s: %s", proposal_id, exc)
            result = EvaluationResult(
                decision="escalate",
                confidence=0.0,
                reasoning=f"AI evaluation error: {exc}",
                spam_or_vandalism=False,
            )

        should_auto_approve = (
            ai_config.mode == "active"
            and result.decision == "approve"
            and result.confidence >= ai_config.confidence_threshold
            and not result.spam_or_vandalism
        )

        if should_auto_approve:
            ai_approver_id = roles.ensure_ai_approver(conn)
            cur = conn.execute(
                """
                UPDATE proposals
                SET status = 'approved', decided_by = ?, decided_at = datetime('now'),
                    ai_decision = 'approved', ai_confidence = ?, ai_reasoning = ?,
                    ai_evaluated_at = datetime('now')
                WHERE id = ? AND status = 'pending'
                """,
                (ai_approver_id, result.confidence, result.reasoning, proposal_id),
            )
            conn.commit()
            if cur.rowcount == 1:
                try:
                    github_dispatch.trigger_apply(app.config["GITHUB_CONFIG"], proposal_id)
                except OSError:
                    logger.exception(
                        "failed to dispatch apply-proposal.yml for auto-approved proposal %s",
                        proposal_id,
                    )
                return

        # Record evaluation for escalation or shadow mode
        conn.execute(
            """
            UPDATE proposals
            SET ai_decision = ?, ai_confidence = ?, ai_reasoning = ?,
                ai_evaluated_at = datetime('now')
            WHERE id = ? AND status = 'pending'
            """,
            (result.decision, result.confidence, result.reasoning, proposal_id),
        )
        conn.commit()

        # Send escalation email
        try:
            recipients = roles.get_approver_recipients(conn, app.config.get("MAINTAINER_EMAIL"))
            dashboard_url = _get_dashboard_url(app)
            scope = (
                f"{proposal_dict['band_slug']}/{proposal_dict['release_slug']}"
                if proposal_dict.get("release_slug")
                else proposal_dict["band_slug"]
            )
            target_summary = f"{scope} ({proposal_dict['target']}.{proposal_dict['field']})"
            details = (
                f"- Original: {proposal_dict['original_value']!r}\n"
                f"+ Proposed: {proposal_dict['proposed_value']!r}\n"
                f"Submitter: {proposal_dict.get('submitter_name') or 'anonymous'} "
                f"<{proposal_dict.get('submitter_contact') or 'none'}>"
            )
            mail.send_ai_escalation_notification(
                app.config["SMTP_CONFIG"],
                recipients,
                proposal_id=proposal_id,
                target_summary=target_summary,
                details=details,
                ai_decision=result.decision,
                ai_confidence=result.confidence,
                ai_reasoning=result.reasoning,
                dashboard_url=dashboard_url,
                is_media=False,
                is_shadow=(ai_config.mode == "shadow"),
            )
        except OSError:
            logger.exception(
                "failed to send escalation notification email for proposal %s", proposal_id
            )


def process_media_proposal_with_ai(app: Flask, media_proposal_id: int) -> None:
    """Evaluates a media proposal (photo/video) and performs autonomous approval or escalation."""
    with app.app_context():
        ai_config: AiConfig = app.config.get("AI_CONFIG") or AiConfig()
        if ai_config.mode == "disabled":
            return

        conn = db.get_connection()
        row = conn.execute(
            "SELECT * FROM media_proposals WHERE id = ? AND status = 'pending'",
            (media_proposal_id,),
        ).fetchone()
        if row is None:
            return

        proposal_dict = dict(row)
        checkout = app.config["ARCHIVE_CHECKOUT_PATH"]
        band_name = proposal_dict["band_slug"]
        release_name = None
        try:
            band = archive_read.get_band(checkout, proposal_dict["band_slug"])
            band_name = band.name
            if proposal_dict.get("release_slug"):
                release = archive_read.get_release(
                    checkout, proposal_dict["band_slug"], proposal_dict["release_slug"]
                )
                release_name = release.name
        except Exception:
            pass

        user_prompt = build_media_proposal_prompt(proposal_dict, band_name, release_name)

        image_bytes = None
        image_mime = None
        if proposal_dict["media_type"] == "image":
            file_path = Path(app.config["MEDIA_UPLOADS_PATH"]) / proposal_dict["stored_filename"]
            if file_path.exists():
                try:
                    image_bytes = file_path.read_bytes()
                    image_mime = proposal_dict.get("content_type") or "image/jpeg"
                except Exception as exc:
                    logger.warning("could not read media file for AI evaluation: %s", exc)

        try:
            raw_response = call_ai_api(
                ai_config, user_prompt, image_bytes=image_bytes, image_mime=image_mime
            )
            result = parse_ai_response(raw_response)
        except Exception as exc:
            logger.exception(
                "AI evaluation failed for media proposal %s: %s", media_proposal_id, exc
            )
            result = EvaluationResult(
                decision="escalate",
                confidence=0.0,
                reasoning=f"AI evaluation error: {exc}",
                spam_or_vandalism=False,
            )

        should_auto_approve = (
            ai_config.mode == "active"
            and result.decision == "approve"
            and result.confidence >= ai_config.confidence_threshold
            and not result.spam_or_vandalism
        )

        if should_auto_approve:
            ai_approver_id = roles.ensure_ai_approver(conn)
            cur = conn.execute(
                """
                UPDATE media_proposals
                SET status = 'publishing', decided_by = ?, decided_at = datetime('now'),
                    ai_decision = 'approved', ai_confidence = ?, ai_reasoning = ?,
                    ai_evaluated_at = datetime('now')
                WHERE id = ? AND status = 'pending'
                """,
                (ai_approver_id, result.confidence, result.reasoning, media_proposal_id),
            )
            conn.commit()
            if cur.rowcount == 1:
                # Notify submitter if contact was given
                if proposal_dict.get("submitter_contact"):
                    scope = (
                        f"{proposal_dict['band_slug']}/{proposal_dict['release_slug']}"
                        if proposal_dict.get("release_slug")
                        else proposal_dict["band_slug"]
                    )
                    description = f"your {proposal_dict['media_type']} for {scope}"
                    try:
                        mail.send_media_approved_notification(
                            app.config["SMTP_CONFIG"],
                            proposal_dict["submitter_contact"],
                            description,
                        )
                    except OSError:
                        logger.exception(
                            "failed to send media-approved notification for proposal %s",
                            media_proposal_id,
                        )

                # Dispatch apply-media-proposal.yml
                try:
                    github_dispatch.trigger_media_apply(
                        app.config["GITHUB_CONFIG"], media_proposal_id
                    )
                except OSError:
                    logger.exception(
                        "failed to dispatch apply-media-proposal.yml for proposal %s",
                        media_proposal_id,
                    )
                    conn.execute(
                        "UPDATE media_proposals SET status = 'publish_failed', publish_error = ? WHERE id = ?",
                        (
                            "Failed to dispatch upload workflow to GitHub Actions",
                            media_proposal_id,
                        ),
                    )
                    conn.commit()
                return

        # Record evaluation for escalation or shadow mode
        conn.execute(
            """
            UPDATE media_proposals
            SET ai_decision = ?, ai_confidence = ?, ai_reasoning = ?,
                ai_evaluated_at = datetime('now')
            WHERE id = ? AND status = 'pending'
            """,
            (result.decision, result.confidence, result.reasoning, media_proposal_id),
        )
        conn.commit()

        # Send escalation email
        try:
            recipients = roles.get_approver_recipients(conn, app.config.get("MAINTAINER_EMAIL"))
            dashboard_url = _get_dashboard_url(app)
            scope = (
                f"{proposal_dict['band_slug']}/{proposal_dict['release_slug']}"
                if proposal_dict.get("release_slug")
                else proposal_dict["band_slug"]
            )
            target_summary = (
                f"{scope} ({proposal_dict['media_type']}: {proposal_dict['original_filename']})"
            )
            details = (
                f"File: {proposal_dict['original_filename']} "
                f"({proposal_dict['size_bytes']} bytes, {proposal_dict['content_type']})\n"
                f"Caption: {proposal_dict.get('caption') or '(none)'}\n"
                f"Submitter: {proposal_dict.get('submitter_name') or 'anonymous'} "
                f"<{proposal_dict.get('submitter_contact') or 'none'}>"
            )
            mail.send_ai_escalation_notification(
                app.config["SMTP_CONFIG"],
                recipients,
                proposal_id=media_proposal_id,
                target_summary=target_summary,
                details=details,
                ai_decision=result.decision,
                ai_confidence=result.confidence,
                ai_reasoning=result.reasoning,
                dashboard_url=dashboard_url,
                is_media=True,
                is_shadow=(ai_config.mode == "shadow"),
            )
        except OSError:
            logger.exception(
                "failed to send escalation notification email for media proposal %s",
                media_proposal_id,
            )


def dispatch_evaluation(
    app: Flask, proposal_id: int, is_media: bool = False, sync: bool = False
) -> None:
    """Dispatches the AI evaluation either synchronously or asynchronously in a background thread."""
    ai_config: AiConfig = app.config.get("AI_CONFIG") or AiConfig()
    if ai_config.mode == "disabled":
        return

    is_sync = sync or bool(app.config.get("TESTING") and app.config.get("AI_SYNC_EVALUATION"))
    target = process_media_proposal_with_ai if is_media else process_proposal_with_ai
    if is_sync:
        target(app, proposal_id)
    else:
        thread = threading.Thread(target=target, args=(app, proposal_id), daemon=True)
        thread.start()
