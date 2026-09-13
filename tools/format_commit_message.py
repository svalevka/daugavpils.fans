#!/usr/bin/env python3
"""
Formats a Git commit message and optional GitHub Step Summary for an approved
text or media proposal.

Extracts audit trail details (target, approver, AI confidence, AI reasoning)
from proposal.json and generates a structured commit message for git history.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def format_commit_message(
    proposal: dict[str, Any],
    is_media: bool = False,
    is_album: bool = False,
    is_band: bool = False,
) -> str:
    pid = proposal.get("id") or "unknown"
    band_slug = proposal.get("band_slug", "")
    release_slug = proposal.get("release_slug")
    scope = f"{band_slug}/{release_slug}" if release_slug else band_slug

    if is_band:
        title = f"Apply approved band proposal #{pid}"
        band_name = proposal.get("name", band_slug)
        has_rel = proposal.get("has_release")
        rel_suffix = f" + release {proposal.get('release_name')}" if has_rel else ""
        target_line = f"Target: bands/{band_slug} ({band_name}{rel_suffix})"
        caption = None
    elif is_album:
        title = f"Apply approved album proposal #{pid}"
        album_name = proposal.get("name", "")
        tracks_count = len(proposal.get("tracks", []))
        target_line = f"Target: {scope} ({album_name} - {tracks_count} tracks)"
        caption = None
    elif is_media:
        title = f"Apply approved media proposal #{pid}"
        media_type = proposal.get("media_type", "media")
        filename = proposal.get("original_filename", "")
        target_line = (
            f"Target: {scope} ({media_type}: {filename})"
            if filename
            else f"Target: {scope} ({media_type})"
        )
        caption = proposal.get("caption")
    else:
        title = f"Apply approved proposal #{pid}"
        target = proposal.get("target", "")
        field = proposal.get("field", "")
        if field:
            target_line = f"Target: {scope} ({target}.{field})"
        elif target:
            target_line = f"Target: {scope} ({target})"
        else:
            target_line = f"Target: {scope}"
        caption = None

    lines = [title, "", target_line]

    if caption:
        lines.append(f"Caption: {caption}")

    decided_by = proposal.get("decided_by_name")
    if decided_by:
        lines.append(f"Decided by: {decided_by}")

    ai_decision = proposal.get("ai_decision")
    if ai_decision:
        confidence = proposal.get("ai_confidence")
        conf_str = f" ({round(confidence * 100)}% confidence)" if confidence is not None else ""
        lines.append(f"AI Decision: {ai_decision}{conf_str}")

    ai_reasoning = proposal.get("ai_reasoning")
    if ai_reasoning:
        lines.append(f"AI Reasoning: {ai_reasoning}")

    if is_band:
        tag_name = "Band-Proposal-ID"
    elif is_album:
        tag_name = "Album-Proposal-ID"
    elif is_media:
        tag_name = "Media-Proposal-ID"
    else:
        tag_name = "Proposal-ID"
    lines.extend(["", f"{tag_name}: {pid}"])

    return "\n".join(lines) + "\n"


def format_step_summary(
    proposal: dict[str, Any],
    is_media: bool = False,
    is_album: bool = False,
    is_band: bool = False,
) -> str:
    pid = proposal.get("id") or "unknown"
    band_slug = proposal.get("band_slug", "")
    release_slug = proposal.get("release_slug")
    scope = f"{band_slug}/{release_slug}" if release_slug else band_slug

    if is_band:
        title = f"Applied Band Proposal #{pid}"
        band_name = proposal.get("name", band_slug)
        has_rel = proposal.get("has_release")
        rel_suffix = f" + release {proposal.get('release_name')}" if has_rel else ""
        target_str = f"bands/{band_slug} ({band_name}{rel_suffix})"
    elif is_album:
        title = f"Applied Album Proposal #{pid}"
        album_name = proposal.get("name", "")
        tracks_count = len(proposal.get("tracks", []))
        target_str = f"{scope} ({album_name} - {tracks_count} tracks)"
    elif is_media:
        title = f"Applied Media Proposal #{pid}"
        media_type = proposal.get("media_type", "media")
        filename = proposal.get("original_filename", "")
        target_str = f"{scope} ({media_type}: {filename})"
    else:
        title = f"Applied Proposal #{pid}"
        target = proposal.get("target", "")
        field = proposal.get("field", "")
        target_str = f"{scope} ({target}.{field})" if field else f"{scope} ({target})"

    decided_by = proposal.get("decided_by_name") or "Maintainer"
    ai_decision = proposal.get("ai_decision")
    confidence = proposal.get("ai_confidence")
    conf_str = f" ({round(confidence * 100)}% confidence)" if confidence is not None else ""
    ai_reasoning = proposal.get("ai_reasoning")

    md = [
        f"### {title}",
        f"* **Target**: `{target_str}`",
        f"* **Decided by**: **{decided_by}**",
    ]

    if ai_decision:
        md.append(f"* **AI Evaluation**: `{ai_decision}`{conf_str}")
    if ai_reasoning:
        md.append(f"> **AI Reasoning**: {ai_reasoning}")

    md.append("")
    return "\n".join(md) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Format git commit message from proposal.json")
    parser.add_argument("--proposal-file", type=Path, required=True, help="Path to proposal JSON")
    parser.add_argument("--output", type=Path, help="Path to write commit message")
    parser.add_argument("--summary-file", type=str, help="Path to append step summary markdown")
    parser.add_argument("--is-media", action="store_true", help="Whether this is a media proposal")
    parser.add_argument("--is-album", action="store_true", help="Whether this is an album proposal")
    parser.add_argument("--is-band", action="store_true", help="Whether this is a band proposal")

    args = parser.parse_args()

    data = json.loads(args.proposal_file.read_text(encoding="utf-8"))

    commit_msg = format_commit_message(
        data, is_media=args.is_media, is_album=args.is_album, is_band=args.is_band
    )

    if args.output:
        args.output.write_text(commit_msg, encoding="utf-8")
    else:
        print(commit_msg, end="")

    if args.summary_file and args.summary_file.strip():
        summary_path = Path(args.summary_file.strip())
        summary_md = format_step_summary(
            data, is_media=args.is_media, is_album=args.is_album, is_band=args.is_band
        )
        with summary_path.open("a", encoding="utf-8") as f:
            f.write(summary_md)

    return 0


if __name__ == "__main__":
    sys.exit(main())
