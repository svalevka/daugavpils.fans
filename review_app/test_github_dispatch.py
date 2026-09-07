#!/usr/bin/env python3
"""
github_dispatch.trigger_apply() (GitHub issue #13). The real GitHub API
is never touched - requests.post is mocked, matching the agreed seam
(see the parent PRD's Testing Decisions: "the GitHub workflow_dispatch
call mocked at its narrowest boundary").
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import github_dispatch  # noqa: E402
from config import GithubConfig  # noqa: E402


class TriggerApplyTest(unittest.TestCase):
    def setUp(self):
        self.github_config = GithubConfig(token="test-token", repo="svalevka/daugavpils.fans")

    @mock.patch("github_dispatch.requests.post")
    def test_posts_to_the_correct_workflow_dispatch_url(self, mock_post):
        mock_post.return_value.raise_for_status.return_value = None

        github_dispatch.trigger_apply(self.github_config, 42)

        url = mock_post.call_args.args[0]
        self.assertEqual(
            url,
            "https://api.github.com/repos/svalevka/daugavpils.fans/actions/"
            "workflows/apply-proposal.yml/dispatches",
        )

    @mock.patch("github_dispatch.requests.post")
    def test_sends_only_the_proposal_id_never_any_proposed_text(self, mock_post):
        mock_post.return_value.raise_for_status.return_value = None

        github_dispatch.trigger_apply(self.github_config, 42)

        payload = mock_post.call_args.kwargs["json"]
        self.assertEqual(payload, {"ref": "main", "inputs": {"proposal_id": "42"}})

    @mock.patch("github_dispatch.requests.post")
    def test_authenticates_with_the_configured_token(self, mock_post):
        mock_post.return_value.raise_for_status.return_value = None

        github_dispatch.trigger_apply(self.github_config, 42)

        headers = mock_post.call_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer test-token")

    @mock.patch("github_dispatch.requests.post")
    def test_a_non_2xx_response_raises(self, mock_post):
        mock_post.return_value.raise_for_status.side_effect = Exception("boom")

        with self.assertRaises(Exception):
            github_dispatch.trigger_apply(self.github_config, 42)


if __name__ == "__main__":
    unittest.main()
