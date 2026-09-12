"""
Triggers .github/workflows/apply-proposal.yml via GitHub's workflow_dispatch
API on approval (see GitHub issue #13). Only the proposal's id crosses
this boundary - never the proposed text, which the Action fetches for
itself via the callback API (api.py) once it's running - using a token
scoped to Actions: write only, so a compromised review_app process could
trigger extra Action runs but could never itself push to git.
"""
from __future__ import annotations

import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import GithubConfig  # noqa: E402


def trigger_apply(github_config: GithubConfig, proposal_id: int) -> None:
    url = (
        f"https://api.github.com/repos/{github_config.repo}/actions/"
        f"workflows/{github_config.workflow_file}/dispatches"
    )
    response = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {github_config.token}",
            "Accept": "application/vnd.github+json",
        },
        json={"ref": github_config.ref, "inputs": {"proposal_id": str(proposal_id)}},
        timeout=10,
    )
    response.raise_for_status()


def trigger_media_apply(
    github_config: GithubConfig,
    proposal_id: int,
    workflow_file: str = "apply-media-proposal.yml",
) -> None:
    url = (
        f"https://api.github.com/repos/{github_config.repo}/actions/"
        f"workflows/{workflow_file}/dispatches"
    )
    response = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {github_config.token}",
            "Accept": "application/vnd.github+json",
        },
        json={"ref": github_config.ref, "inputs": {"proposal_id": str(proposal_id)}},
        timeout=10,
    )
    response.raise_for_status()

