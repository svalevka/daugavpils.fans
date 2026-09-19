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

import os
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
        self.tmp_path = tmp_path
        self.origin_dir = tmp_path / "origin"
        self.repo_dir = tmp_path / "repo"  # intentionally not pre-created - the script bootstraps it
        self.site_dir = tmp_path / "site"
        self.checkout_root = self.site_dir / "checkouts"
        self.current_link = self.site_dir / "current"
        self.current_checkout_link = self.site_dir / "current-checkout"
        self.state_file = tmp_path / "last-deployed-sha"
        self.lock_dir = tmp_path / "lock.d"
        self.live_compose_file = tmp_path / "docker-compose.yml"
        self.live_nginx_dir = tmp_path / "nginx"
        self.live_nginx_conf = self.live_nginx_dir / "daugavpils.conf"
        self.bin_dir = tmp_path / "bin"
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        self.docker_log_file = tmp_path / "docker.log"
        self._setup_mock_docker()

    def _setup_mock_docker(self) -> None:
        docker_script = self.bin_dir / "docker"
        docker_script.write_text(f"""#!/bin/sh
echo "$@" >> "{self.docker_log_file}"
case "$*" in
  *"ps --services"*)
    echo "nginx"
    echo "review-app"
    exit 0
    ;;
  *"config"*)
    if [ "${{MOCK_DOCKER_FAIL_CONFIG:-0}}" = "1" ]; then
      echo "compose config error" >&2
      exit 1
    fi
    exit 0
    ;;
  *"nginx -t"*)
    if [ "${{MOCK_DOCKER_FAIL_NGINX_TEST:-0}}" = "1" ]; then
      echo "nginx: syntax error" >&2
      exit 1
    fi
    exit 0
    ;;
  *"nginx -s reload"*)
    exit 0
    ;;
  *"up"*)
    exit 0
    ;;
esac
exit 0
""")
        docker_script.chmod(0o755)

    def current_index_html(self) -> str:
        return (self.current_link / "index.html").read_text()

    def env(self, keep_checkouts: int = 3, extra_env: dict[str, str] | None = None) -> dict[str, str]:
        base = {
            "REPO_URL": str(self.origin_dir),
            "REPO_DIR": str(self.repo_dir),
            "CHECKOUT_ROOT": str(self.checkout_root),
            "CURRENT_LINK": str(self.current_link),
            "CURRENT_CHECKOUT_LINK": str(self.current_checkout_link),
            "STATE_FILE": str(self.state_file),
            "LOCK_DIR": str(self.lock_dir),
            "LIVE_COMPOSE_FILE": str(self.live_compose_file),
            "LIVE_NGINX_DIR": str(self.live_nginx_dir),
            "LIVE_NGINX_CONF": str(self.live_nginx_conf),
            "KEEP_CHECKOUTS": str(keep_checkouts),
            "PYTHON_BIN": sys.executable,
            "PATH": f"{self.bin_dir}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
        }
        if extra_env:
            base.update(extra_env)
        return base

    def run(self, *args: str, keep_checkouts: int = 3, extra_env: dict[str, str] | None = None) -> "subprocess.CompletedProcess[str]":
        return subprocess.run(
            [BASH_BIN, str(SCRIPT), *args], env=self.env(keep_checkouts, extra_env), capture_output=True, text=True
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

            # current-checkout points at the worktree *root* (review_app's
            # ARCHIVE_CHECKOUT_PATH - see GitHub issue #14), not webapp/dist
            # like current does - proven by it containing webapp/build.py
            # as a subpath, which webapp/dist itself never would.
            self.assertTrue(h.current_checkout_link.is_symlink())
            self.assertFalse(h.current_checkout_link.readlink().is_absolute())
            self.assertTrue((h.current_checkout_link / "webapp" / "build.py").exists())

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
            # Both symlinks move together, to the same new commit.
            self.assertEqual(h.current_link.resolve().parent.parent, h.current_checkout_link.resolve())

    def test_detects_review_app_changes_on_new_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = DeployHarness(Path(tmp))
            _init_origin(h.origin_dir, "v1")
            h.run()

            # Add a file in review_app/
            review_app_dir = h.origin_dir / "review_app"
            review_app_dir.mkdir(exist_ok=True)
            (review_app_dir / "new_feature.py").write_text("# new feature\n")
            _git("add", "review_app", cwd=h.origin_dir)
            _git("commit", "-m", "update review_app", cwd=h.origin_dir)

            result = h.run()
            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertIn("review_app code or deployment configuration changed", result.stdout)


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
            (h.lock_dir / "pid").write_text(str(os.getpid()))

            result = h.run()

            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertIn("already in progress", result.stdout)
            self.assertFalse(h.site_dir.exists())
            self.assertFalse(h.repo_dir.exists())

    def test_stale_lock_with_dead_pid_is_reclaimed(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = DeployHarness(Path(tmp))
            _init_origin(h.origin_dir, "v1")
            h.lock_dir.mkdir()
            # Write a dead PID that cannot be running
            (h.lock_dir / "pid").write_text("99999999")

            result = h.run()

            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertIn("stale lock detected", result.stdout)
            self.assertEqual(h.current_index_html(), "v1")
            self.assertTrue(h.state_file.exists())


class DeploymentConfigSyncTest(unittest.TestCase):
    def test_config_changed_is_synced_validated_and_reloaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = DeployHarness(Path(tmp))
            _init_origin(h.origin_dir, "v1")

            # Add deploy config files to origin
            deploy_dir = h.origin_dir / "webapp" / "deploy"
            nginx_dir = deploy_dir / "nginx"
            nginx_dir.mkdir(parents=True, exist_ok=True)
            (deploy_dir / "docker-compose.yml").write_text("services:\n  nginx:\n    image: nginx:alpine\n")
            (nginx_dir / "daugavpils.conf").write_text("# nginx conf v1\n")
            _git("add", "-A", cwd=h.origin_dir)
            _git("commit", "-m", "add deploy configs v1", cwd=h.origin_dir)

            result1 = h.run()
            self.assertEqual(result1.returncode, 0, msg=result1.stdout + result1.stderr)
            self.assertTrue(h.live_compose_file.exists())
            self.assertTrue(h.live_nginx_conf.exists())
            self.assertEqual(h.live_nginx_conf.read_text(), "# nginx conf v1\n")

            # Now advance origin with v2 config
            (nginx_dir / "daugavpils.conf").write_text("# nginx conf v2\n")
            _git("commit", "-am", "update nginx conf to v2", cwd=h.origin_dir)

            h.docker_log_file.write_text("")
            result2 = h.run()
            self.assertEqual(result2.returncode, 0, msg=result2.stdout + result2.stderr)
            self.assertEqual(h.live_nginx_conf.read_text(), "# nginx conf v2\n")
            self.assertIn("deployment configuration changed or drifted", result2.stdout)
            self.assertIn("deployment configuration successfully validated and applied", result2.stdout)
            docker_log = h.docker_log_file.read_text()
            self.assertIn("nginx -t", docker_log)
            self.assertIn("nginx -s reload", docker_log)

    def test_invalid_config_is_rolled_back_and_alert_raised(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = DeployHarness(Path(tmp))
            _init_origin(h.origin_dir, "v1")

            # Initial good config
            deploy_dir = h.origin_dir / "webapp" / "deploy"
            nginx_dir = deploy_dir / "nginx"
            nginx_dir.mkdir(parents=True, exist_ok=True)
            (deploy_dir / "docker-compose.yml").write_text("services:\n  nginx:\n    image: nginx:alpine\n")
            (nginx_dir / "daugavpils.conf").write_text("# good nginx conf\n")
            _git("add", "-A", cwd=h.origin_dir)
            _git("commit", "-m", "add deploy configs v1", cwd=h.origin_dir)
            h.run()

            # Advance with invalid config
            (nginx_dir / "daugavpils.conf").write_text("# broken nginx conf\n")
            _git("commit", "-am", "broken config", cwd=h.origin_dir)

            result = h.run(extra_env={"MOCK_DOCKER_FAIL_NGINX_TEST": "1"})
            # Verification: live nginx conf must be rolled back to good config
            self.assertEqual(h.live_nginx_conf.read_text(), "# good nginx conf\n")
            self.assertIn("ALERT: deployment config validation failed! Rolling back", result.stderr)

    def test_unchanged_config_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = DeployHarness(Path(tmp))
            _init_origin(h.origin_dir, "v1")

            deploy_dir = h.origin_dir / "webapp" / "deploy"
            nginx_dir = deploy_dir / "nginx"
            nginx_dir.mkdir(parents=True, exist_ok=True)
            (deploy_dir / "docker-compose.yml").write_text("services:\n  nginx:\n    image: nginx:alpine\n")
            (nginx_dir / "daugavpils.conf").write_text("# nginx conf v1\n")
            _git("add", "-A", cwd=h.origin_dir)
            _git("commit", "-m", "add deploy configs", cwd=h.origin_dir)
            h.run()

            # Advance with change to build.py only (deploy config unchanged)
            _advance_origin(h.origin_dir, "v2")
            h.docker_log_file.write_text("")
            result = h.run()
            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertIn("deployment configuration unchanged - nothing to sync", result.stdout)
            docker_log = h.docker_log_file.read_text()
            self.assertNotIn("nginx -s reload", docker_log)

    def test_drift_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = DeployHarness(Path(tmp))
            _init_origin(h.origin_dir, "v1")

            deploy_dir = h.origin_dir / "webapp" / "deploy"
            nginx_dir = deploy_dir / "nginx"
            nginx_dir.mkdir(parents=True, exist_ok=True)
            (deploy_dir / "docker-compose.yml").write_text("services:\n  nginx:\n    image: nginx:alpine\n")
            (nginx_dir / "daugavpils.conf").write_text("# nginx conf v1\n")
            _git("add", "-A", cwd=h.origin_dir)
            _git("commit", "-m", "add deploy configs", cwd=h.origin_dir)
            h.run()

            # Check drift when matching
            clean_res = h.run("--check-drift")
            self.assertEqual(clean_res.returncode, 0)
            self.assertIn("OK: live deployment configuration matches repo", clean_res.stdout)

            # Introduce drift manually in live config
            h.live_nginx_conf.write_text("# tampered conf\n")
            drift_res = h.run("--check-drift")
            self.assertEqual(drift_res.returncode, 1)
            self.assertIn("DRIFT:", drift_res.stderr)


if __name__ == "__main__":
    unittest.main()
