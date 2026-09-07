#!/usr/bin/env python3
"""
The full approve -> apply -> push pipeline (GitHub issue #13), end to
end. Doesn't run .github/workflows/apply-proposal.yml itself (no local
GH Actions runner) - instead drives the exact same sequence of
operations its steps perform: fetch the approved proposal from a real
running review_app instance via the callback API, apply it with
tools/apply_proposal.py (a real subprocess), validate with
tools/validate.py (a real subprocess), then commit and push against a
real local git remote standing in for GitHub - proving the pipeline's
logic end to end without needing a real Action runner. Agreed testing
seam for this ticket, confirmed with the user (mirrors ticket #10's
sync-and-deploy.sh precedent).

api.py/github_dispatch.py's own request/response contracts are tested
directly in test_api.py/test_github_dispatch.py; this file is about
whether the pieces actually cohere into one working pipeline.
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

from test_support import ReviewAppTestCase  # noqa: E402

from archive_fixture import build_archive_with_nested_fields  # noqa: E402

TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
APPLY_PROPOSAL_PY = TOOLS_DIR / "apply_proposal.py"
VALIDATE_PY = TOOLS_DIR / "validate.py"


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def _run_ok(result: subprocess.CompletedProcess, msg: str) -> None:
    assert result.returncode == 0, f"{msg}: {result.stdout}\n{result.stderr}"


class FullApprovalPipelineTest(ReviewAppTestCase):
    """Overrides the base class's checkout - instead of a plain
    directory, it's a git clone of a bare "origin" repo (standing in for
    GitHub), so this suite can actually exercise the commit-and-push half
    of the pipeline that #9's tools/test_apply_proposal.py never needed
    to (it only ever tested the mutation, in a plain directory)."""

    def _build_checkout(self, tmp_path: Path) -> Path:
        # Overrides only the checkout shape (the base class's hook for
        # exactly this) - _build_app() (Config/create_app/mocks) is
        # inherited and reused as-is, not duplicated.
        self.origin_dir = tmp_path / "origin.git"
        _run_ok(_git("init", "--bare", "--initial-branch=main", str(self.origin_dir), cwd=tmp_path), "git init --bare")

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

    def _simulate_the_action(self, proposal_id: int, tmp_path: Path) -> subprocess.CompletedProcess:
        """Everything apply-proposal.yml's steps do, after workflow_dispatch
        has fired - minus needing a real Action runner (see this file's
        docstring)."""
        fetch = self.client.get(f"/api/proposals/{proposal_id}", headers=self.callback_headers())
        assert fetch.status_code == 200, fetch.data

        proposal_file = tmp_path / "proposal.json"
        proposal_file.write_text(json.dumps(fetch.get_json()))

        apply_result = subprocess.run(
            [sys.executable, str(APPLY_PROPOSAL_PY), "--proposal-file", str(proposal_file),
             "--bands-dir", str(self.checkout_path / "bands")],
            capture_output=True, text=True,
        )
        if apply_result.returncode != 0:
            return apply_result

        validate_result = subprocess.run(
            [sys.executable, str(VALIDATE_PY), "--bands-dir", str(self.checkout_path / "bands")],
            capture_output=True, text=True,
        )
        return validate_result

    def _commit_and_push(self, proposal_id: int) -> subprocess.CompletedProcess:
        _git("add", "-A", "--", "bands", cwd=self.checkout_path)
        commit = _git("commit", "-m", f"Apply approved proposal #{proposal_id}", cwd=self.checkout_path)
        if commit.returncode != 0:
            return commit
        return _git("push", "origin", "main", cwd=self.checkout_path)

    def test_a_full_approve_to_push_run_lands_a_correct_commit(self):
        approver_id = self.seed_approver("approver@example.com")
        self.submit(target="band", field="description", proposed_value="Corrected biography text.")
        proposal_id = self.fetch_proposals()[0]["id"]
        self.login_as(approver_id)
        self.client.post(f"/proposals/{proposal_id}/approve")

        with tempfile.TemporaryDirectory() as action_tmp:
            pipeline_result = self._simulate_the_action(proposal_id, Path(action_tmp))
        self.assertEqual(pipeline_result.returncode, 0, msg=pipeline_result.stdout + pipeline_result.stderr)
        self.assertIn("OK", pipeline_result.stdout)  # validate.py's own success message

        push_result = self._commit_and_push(proposal_id)
        self.assertEqual(push_result.returncode, 0, msg=push_result.stdout + push_result.stderr)

        self.client.post(
            f"/api/proposals/{proposal_id}/apply-result",
            json={"success": True, "run_id": "1"},
            headers=self.callback_headers(),
        )

        # The commit really landed on origin's main, not just the local
        # checkout - clone it fresh and read the file back.
        with tempfile.TemporaryDirectory() as verify_tmp:
            verify_dir = Path(verify_tmp) / "verify"
            _run_ok(_git("clone", str(self.origin_dir), str(verify_dir), cwd=Path(verify_tmp)), "verify clone")
            band_yaml = (verify_dir / "bands" / self.fx.band_slug / "band.yaml").read_text()
            self.assertIn("Corrected biography text.", band_yaml)

            log = _git("log", "--oneline", "-1", cwd=verify_dir)
            self.assertIn(f"#{proposal_id}", log.stdout)

        self.assertEqual(self.fetch_proposals()[0]["status"], "applied")

    def test_a_failed_validation_never_reaches_origin(self):
        # Sabotage the checkout so validate.py fails after apply_proposal.py
        # succeeds: delete the audio file a track references.
        approver_id = self.seed_approver("approver@example.com")
        self.submit(target="band", field="description", proposed_value="Corrected biography text.")
        proposal_id = self.fetch_proposals()[0]["id"]
        self.login_as(approver_id)
        self.client.post(f"/proposals/{proposal_id}/approve")

        audio_files = list((self.checkout_path / "bands" / self.fx.band_slug).glob("**/*.mp3"))
        self.assertTrue(audio_files, "fixture should have at least one audio file")
        audio_files[0].unlink()

        with tempfile.TemporaryDirectory() as action_tmp:
            pipeline_result = self._simulate_the_action(proposal_id, Path(action_tmp))
        self.assertNotEqual(pipeline_result.returncode, 0)

        before_head = _git("rev-parse", "main", cwd=self.origin_dir).stdout.strip()

        self.client.post(
            f"/api/proposals/{proposal_id}/apply-result",
            json={"success": False, "run_id": "2", "error": "validate.py failed"},
            headers=self.callback_headers(),
        )

        after_head = _git("rev-parse", "main", cwd=self.origin_dir).stdout.strip()
        self.assertEqual(before_head, after_head)  # nothing was ever pushed
        row = self.fetch_proposals()[0]
        self.assertEqual(row["status"], "apply_failed")
        self.assertEqual(row["apply_error"], "validate.py failed")


if __name__ == "__main__":
    unittest.main()
