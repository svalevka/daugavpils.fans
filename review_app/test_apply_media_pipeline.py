#!/usr/bin/env python3
"""
Full approve -> upload -> apply -> push pipeline for media proposals,
exercised end to end with a real git remote (mirroring test_apply_pipeline.py).
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from test_support import JPEG_BYTES, ReviewAppTestCase  # noqa: E402

from archive_fixture import build_archive_with_nested_fields  # noqa: E402

TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
APPLY_MEDIA_PY = TOOLS_DIR / "apply_media_proposal.py"


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def _run_ok(result: subprocess.CompletedProcess, msg: str) -> None:
    assert result.returncode == 0, f"{msg}: {result.stdout}\n{result.stderr}"


class FullMediaPipelineTest(ReviewAppTestCase):
    def _build_checkout(self, tmp_path: Path) -> Path:
        self.origin_dir = tmp_path / "origin.git"
        _run_ok(
            _git("init", "--bare", "--initial-branch=main", str(self.origin_dir), cwd=tmp_path),
            "git init --bare",
        )

        checkout_path = tmp_path / "checkout"
        _run_ok(_git("clone", str(self.origin_dir), str(checkout_path), cwd=tmp_path), "git clone")
        _run_ok(_git("config", "user.email", "test@example.com", cwd=checkout_path), "git config email")
        _run_ok(_git("config", "user.name", "Test", cwd=checkout_path), "git config name")

        self.fx = build_archive_with_nested_fields(checkout_path / "bands")
        _run_ok(_git("add", "-A", cwd=checkout_path), "git add")
        _run_ok(_git("commit", "-m", "initial archive", cwd=checkout_path), "git commit")
        _run_ok(_git("push", "origin", "main", cwd=checkout_path), "git push")

        self.checkout_path = checkout_path
        return checkout_path

    def test_full_media_upload_pipeline_lands_commit(self):
        approver_id = self.seed_approver("approver@example.com")
        self.submit_media(
            file_tuples=[("stage-photo.jpg", JPEG_BYTES)],
            caption_0="Live at festival 1996",
        )
        proposal_id = self.fetch_media_proposals()[-1]["id"]
        self.login_as(approver_id)

        # 1. Approver approves the media proposal
        approve_resp = self.client.post(f"/media-proposals/{proposal_id}/approve")
        self.assertEqual(approve_resp.status_code, 302)
        self.assertEqual(self.fetch_media_proposals()[0]["status"], "approved")

        # 2. Maintainer clicks "Upload"
        upload_resp = self.client.post(f"/media-proposals/{proposal_id}/upload")
        self.assertEqual(upload_resp.status_code, 302)
        self.assertEqual(self.fetch_media_proposals()[0]["status"], "publishing")
        self.mock_trigger_media_apply.assert_called_once()

        # 3. Simulate the GitHub Action workflow:
        with tempfile.TemporaryDirectory() as action_tmp:
            action_path = Path(action_tmp)

            fetch_meta = self.client.get(
                f"/api/media-proposals/{proposal_id}", headers=self.callback_headers()
            )
            self.assertEqual(fetch_meta.status_code, 200)
            proposal_file = action_path / "proposal.json"
            proposal_file.write_text(json.dumps(fetch_meta.get_json()))

            fetch_file = self.client.get(
                f"/api/media-proposals/{proposal_id}/file", headers=self.callback_headers()
            )
            self.assertEqual(fetch_file.status_code, 200)
            media_file = action_path / "media.bin"
            media_file.write_bytes(fetch_file.data)

            # Run apply_media_proposal.py with --skip-upload
            apply_res = subprocess.run(
                [
                    sys.executable,
                    str(APPLY_MEDIA_PY),
                    "--proposal-file",
                    str(proposal_file),
                    "--media-file",
                    str(media_file),
                    "--bands-dir",
                    str(self.checkout_path / "bands"),
                    "--skip-upload",
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(apply_res.returncode, 0, apply_res.stderr)

            # Git commit and push (only YAML is tracked in git)
            _git("add", "-A", "--", "bands", cwd=self.checkout_path)
            commit = _git(
                "commit", "-m", f"Apply approved media proposal #{proposal_id}", cwd=self.checkout_path
            )
            self.assertEqual(commit.returncode, 0, commit.stderr)
            push = _git("push", "origin", "main", cwd=self.checkout_path)
            self.assertEqual(push.returncode, 0, push.stderr)

            # Callback report
            result_resp = self.client.post(
                f"/api/media-proposals/{proposal_id}/publish-result",
                json={"success": True, "run_id": "gh-run-123"},
                headers=self.callback_headers(),
            )
            self.assertEqual(result_resp.status_code, 200)

        # 4. Verify end-state:
        # Check database: status is published, published_at is set, github_run_id recorded
        row = self.fetch_media_proposals()[0]
        self.assertEqual(row["status"], "published")
        self.assertEqual(row["github_run_id"], "gh-run-123")
        self.assertIsNotNone(row["published_at"])

        # Check staged upload file was deleted
        stored_path = (
            self.config.resolved_media_uploads_path() / row["stored_filename"]
        )
        self.assertFalse(stored_path.exists())

        # Check origin main git commit:
        with tempfile.TemporaryDirectory() as verify_tmp:
            verify_dir = Path(verify_tmp) / "verify"
            _run_ok(
                _git("clone", str(self.origin_dir), str(verify_dir), cwd=Path(verify_tmp)),
                "verify clone",
            )
            band_yaml = (verify_dir / "bands" / self.fx.band_slug / "band.yaml").read_text()
            self.assertIn("Live at festival 1996", band_yaml)
            self.assertIn(f"{self.fx.band_slug}-stage-photo.jpg", band_yaml)


if __name__ == "__main__":
    unittest.main()
