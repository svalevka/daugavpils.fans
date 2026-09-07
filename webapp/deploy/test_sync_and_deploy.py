#!/usr/bin/env python3
"""
sync-and-deploy.sh is the script a systemd timer on the primary server
(cherry) runs every few minutes to pick up changes to `main` without
GitHub Actions ever needing inbound access to that box (see
webapp/deploy/README.md, ADR-0001, and GitHub issue #10).

This exercises the script's own responsibility - checking out the latest
commit into an isolated git worktree, invoking the Site build, atomically
swapping the `current` symlink to it, pruning old checkouts, and refusing
to run twice at once - as a real subprocess against a throwaway local git
remote and a stub `webapp/build.py` (agreed testing seam: this suite
doesn't depend on network access, archive.org, or the real Site build,
which are already someone else's concern; it depends only on
`python3 webapp/build.py` being invocable and honoring
SITE_SKIP_LOCAL_VALIDATION, same contract the real build.py has).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "sync-and-deploy.sh"
# Resolved once from *this* process's PATH, not the restricted PATH given
# to the subprocess below - macOS ships an ancient bash (3.2, no
# `mapfile`) at /usr/bin/bash that would otherwise shadow a newer one.
BASH_BIN = shutil.which("bash")

STUB_BUILD_PY = """\
import os, pathlib, sys
assert os.environ.get("SITE_SKIP_LOCAL_VALIDATION") == "1"
dist = pathlib.Path(__file__).resolve().parent / "dist"
dist.mkdir(exist_ok=True)
(dist / "index.html").write_text({marker!r})
sys.exit({exit_code})
"""


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _head(repo_dir: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_dir, check=True, capture_output=True, text=True
    ).stdout.strip()


def _write_stub_build_py(origin_dir: Path, marker: str, *, fail_build: bool) -> None:
    """Overwrite origin_dir/webapp/build.py with a stub that mirrors the
    real build.py's SITE_SKIP_LOCAL_VALIDATION contract - without
    touching the network or the real Site templates - and writes `marker`
    into webapp/dist/index.html so tests can tell which commit actually
    got built."""
    webapp_dir = origin_dir / "webapp"
    webapp_dir.mkdir(exist_ok=True)
    (webapp_dir / "build.py").write_text(
        STUB_BUILD_PY.format(marker=marker, exit_code=1 if fail_build else 0)
    )


def _init_origin(origin_dir: Path, marker: str, *, fail_build: bool = False) -> str:
    """Create a fresh git repo at origin_dir with a stub webapp/build.py.
    Returns the new commit's SHA."""
    origin_dir.mkdir(parents=True)
    _git("init", "--initial-branch=main", cwd=origin_dir)
    _git("config", "user.email", "test@example.com", cwd=origin_dir)
    _git("config", "user.name", "Test", cwd=origin_dir)
    _write_stub_build_py(origin_dir, marker, fail_build=fail_build)
    _git("add", "-A", cwd=origin_dir)
    _git("commit", "-m", f"marker={marker}", cwd=origin_dir)
    return _head(origin_dir)


def _advance_origin(origin_dir: Path, marker: str, *, fail_build: bool = False) -> str:
    """Commit a new build.py revision directly into origin_dir - no push
    needed, since sync-and-deploy.sh's `git fetch` reads refs straight
    from this directory regardless of any checked-out branch there."""
    _write_stub_build_py(origin_dir, marker, fail_build=fail_build)
    _git("commit", "-am", f"marker={marker}", cwd=origin_dir)
    return _head(origin_dir)


class DeployHarness:
    """Bundles the scratch directories one test run needs and knows how
    to invoke sync-and-deploy.sh against them.

    CHECKOUT_ROOT and CURRENT_LINK are direct siblings under `site_dir`,
    matching the production contract (see sync-and-deploy.sh's header
    comment): CURRENT_LINK is a *relative* symlink, so it only resolves
    correctly when both live under the same parent directory - the thing
    a real deploy bind-mounts as a whole into the nginx container.
    """

    def __init__(self, tmp_path: Path):
        self.origin_dir = tmp_path / "origin"
        self.repo_dir = tmp_path / "repo"  # intentionally not pre-created - the script bootstraps it
        self.site_dir = tmp_path / "site"
        self.checkout_root = self.site_dir / "checkouts"
        self.current_link = self.site_dir / "current"
        self.state_file = tmp_path / "last-deployed-sha"
        self.lock_dir = tmp_path / "lock.d"

    def current_index_html(self) -> str:
        return (self.current_link / "index.html").read_text()

    def env(self, keep_checkouts: int = 3) -> dict[str, str]:
        return {
            "REPO_URL": str(self.origin_dir),
            "REPO_DIR": str(self.repo_dir),
            "CHECKOUT_ROOT": str(self.checkout_root),
            "CURRENT_LINK": str(self.current_link),
            "STATE_FILE": str(self.state_file),
            "LOCK_DIR": str(self.lock_dir),
            "KEEP_CHECKOUTS": str(keep_checkouts),
            "PYTHON_BIN": sys.executable,
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
        }

    def run(self, *, keep_checkouts: int = 3) -> "subprocess.CompletedProcess[str]":
        return subprocess.run(
            [BASH_BIN, str(SCRIPT)], env=self.env(keep_checkouts), capture_output=True, text=True
        )


class FirstAndRepeatDeployTest(unittest.TestCase):
    def test_first_run_deploys_the_latest_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = DeployHarness(Path(tmp))
            _init_origin(h.origin_dir, "v1")

            result = h.run()
            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertEqual(h.current_index_html(), "v1")
            self.assertTrue(h.current_link.is_symlink())
            self.assertFalse(h.current_link.readlink().is_absolute())
            self.assertTrue(h.state_file.exists())

    def test_second_run_with_no_new_commits_is_a_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = DeployHarness(Path(tmp))
            _init_origin(h.origin_dir, "v1")
            first = h.run()
            self.assertEqual(first.returncode, 0, msg=first.stdout + first.stderr)

            link_target_before = h.current_link.readlink()
            checkouts_before = sorted(p.name for p in h.checkout_root.iterdir())

            second = h.run()
            self.assertEqual(second.returncode, 0, msg=second.stdout + second.stderr)
            self.assertIn("nothing to do", second.stdout)
            self.assertEqual(h.current_link.readlink(), link_target_before)
            self.assertEqual(sorted(p.name for p in h.checkout_root.iterdir()), checkouts_before)

    def test_new_commit_gets_deployed(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = DeployHarness(Path(tmp))
            _init_origin(h.origin_dir, "v1")
            h.run()

            _advance_origin(h.origin_dir, "v2")
            result = h.run()

            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertEqual(h.current_index_html(), "v2")
            checkouts = sorted(p.name for p in h.checkout_root.iterdir())
            self.assertEqual(len(checkouts), 2)


class FailedBuildTest(unittest.TestCase):
    def test_a_failing_build_never_moves_current(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = DeployHarness(Path(tmp))
            good_sha = _init_origin(h.origin_dir, "v1")
            first = h.run()
            self.assertEqual(first.returncode, 0, msg=first.stdout + first.stderr)

            _advance_origin(h.origin_dir, "v2", fail_build=True)
            result = h.run()

            self.assertNotEqual(result.returncode, 0)
            # The last good deploy is still what's live.
            self.assertEqual(h.current_index_html(), "v1")
            self.assertEqual(h.state_file.read_text().strip(), good_sha)


class PruningTest(unittest.TestCase):
    def test_prunes_old_checkouts_beyond_the_configured_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = DeployHarness(Path(tmp))
            _init_origin(h.origin_dir, "v1")
            h.run(keep_checkouts=2)
            for marker in ("v2", "v3", "v4"):
                _advance_origin(h.origin_dir, marker)
                result = h.run(keep_checkouts=2)
                self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)

            checkouts = sorted(h.checkout_root.iterdir())
            self.assertEqual(len(checkouts), 2)
            self.assertEqual(h.current_index_html(), "v4")
            # current_link must resolve into one of the surviving
            # checkouts, never a pruned one.
            resolved = h.current_link.resolve()
            self.assertTrue(any(resolved.is_relative_to(p.resolve()) for p in checkouts))


class OverlapTest(unittest.TestCase):
    def test_a_run_already_in_progress_causes_the_second_to_exit_early(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = DeployHarness(Path(tmp))
            _init_origin(h.origin_dir, "v1")
            h.lock_dir.mkdir()  # simulate an in-progress run holding the lock

            result = h.run()

            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertIn("already in progress", result.stdout)
            self.assertFalse(h.site_dir.exists())
            self.assertFalse(h.repo_dir.exists())


if __name__ == "__main__":
    unittest.main()
