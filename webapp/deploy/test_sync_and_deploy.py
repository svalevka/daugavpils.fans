#!/usr/bin/env python3
"""
sync-and-deploy.sh is the script a systemd timer on the primary server
(cherry) runs every few minutes to pick up changes to `main` without
GitHub Actions ever needing inbound access to that box (see
webapp/deploy/README.md, ADR-0001, and GitHub issues #10, #79, #83).

This exercises the script's own responsibility - checking out the latest
commit into an isolated git worktree, invoking the Site build, atomically
swapping the `current` symlink to it, pruning old checkouts, and refusing
to run twice at once - as a real subprocess against a throwaway local git
remote and a stub `webapp/build.py` (agreed testing seam: this suite
doesn't depend on network access, archive.org, or the real Site build,
which are already someone else's concern; it depends only on
`python3 webapp/build.py` being invocable and honoring
SITE_SKIP_LOCAL_VALIDATION, same contract the real build.py has).

Also tests least-privilege container rebuild separation (GitHub issue #83):
sync-and-deploy.sh never calls docker directly, only writes a rebuild marker file;
rebuild-containers.sh runs as root to validate configs, reload nginx, and
rebuild/restart review-app.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

DEPLOY_DIR = Path(__file__).resolve().parent
SCRIPT = DEPLOY_DIR / "sync-and-deploy.sh"
REBUILD_SCRIPT = DEPLOY_DIR / "rebuild-containers.sh"
SYNC_SERVICE_FILE = DEPLOY_DIR / "daugavpils-fans-sync.service"
REBUILD_SERVICE_FILE = DEPLOY_DIR / "daugavpils-fans-rebuild.service"
REBUILD_PATH_FILE = DEPLOY_DIR / "daugavpils-fans-rebuild.path"
BASH_BIN = shutil.which("bash") or "bash"

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
        self.rebuild_marker_file = tmp_path / ".rebuild-needed"
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
            "REBUILD_MARKER_FILE": str(self.rebuild_marker_file),
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

    def test_detects_review_app_changes_and_writes_marker_without_calling_docker(self):
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
            latest_sha = _head(h.origin_dir)

            result = h.run()
            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertIn("review_app code or deployment configuration changed", result.stdout)
            self.assertIn("rebuild marker written", result.stdout)

            # Rebuild marker written with latest SHA
            self.assertTrue(h.rebuild_marker_file.exists())
            self.assertEqual(h.rebuild_marker_file.read_text().strip(), latest_sha)

            # Least privilege: sync script must NEVER invoke docker directly
            self.assertFalse(h.docker_log_file.exists(), "sync-and-deploy.sh must not call docker directly")


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
    def test_config_changed_is_synced_and_marker_written_without_calling_docker(self):
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

            # Reset marker
            h.rebuild_marker_file.unlink(missing_ok=True)

            # Now advance origin with v2 config
            (nginx_dir / "daugavpils.conf").write_text("# nginx conf v2\n")
            _git("commit", "-am", "update nginx conf to v2", cwd=h.origin_dir)
            latest_sha = _head(h.origin_dir)

            result2 = h.run()
            self.assertEqual(result2.returncode, 0, msg=result2.stdout + result2.stderr)
            self.assertEqual(h.live_nginx_conf.read_text(), "# nginx conf v2\n")
            self.assertIn("deployment configuration changed or drifted", result2.stdout)
            self.assertIn("deployment configuration successfully synced", result2.stdout)

            # Rebuild marker written
            self.assertTrue(h.rebuild_marker_file.exists())
            self.assertEqual(h.rebuild_marker_file.read_text().strip(), latest_sha)

            # Sync script must never call docker directly
            self.assertFalse(h.docker_log_file.exists(), "sync script must never invoke docker directly")

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

            # Advance with change to build.py only (deploy config and review_app unchanged)
            h.rebuild_marker_file.unlink(missing_ok=True)
            _advance_origin(h.origin_dir, "v2")
            result = h.run()
            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertIn("deployment configuration unchanged - nothing to sync", result.stdout)
            self.assertFalse(h.rebuild_marker_file.exists())
            self.assertFalse(h.docker_log_file.exists())

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


class RebuildContainersScriptTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)
        self.base_dir = self.tmp_path / "base"
        self.base_dir.mkdir()

        self.compose_file = self.base_dir / "docker-compose.yml"
        self.compose_file.write_text("services:\n  nginx:\n    image: nginx:alpine\n  review-app:\n    build: .\n")

        self.nginx_dir = self.base_dir / "nginx"
        self.nginx_dir.mkdir()
        self.nginx_conf = self.nginx_dir / "daugavpils.conf"
        self.nginx_conf.write_text("# nginx conf\n")

        self.marker_file = self.base_dir / ".rebuild-needed"
        self.docker_log = self.tmp_path / "mock_docker.log"
        self.bin_dir = self.tmp_path / "bin"
        self.bin_dir.mkdir()
        self._setup_mock_docker()

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def _setup_mock_docker(self) -> None:
        mock_docker = self.bin_dir / "docker"
        mock_docker.write_text(f"""#!/usr/bin/env bash
echo "docker $@" >> "{self.docker_log}"
case "$*" in
  *"ps --services"*)
    echo "nginx"
    echo "review-app"
    exit 0
    ;;
  *"config --services"*)
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
    exit 0
    ;;
  *"nginx -s reload"*)
    exit 0
    ;;
  *"build review-app"*)
    exit 0
    ;;
  *"up -d"*)
    exit 0
    ;;
esac
exit 0
""")
        mock_docker.chmod(0o755)

    def test_script_syntax(self) -> None:
        """Bash syntax check: bash -n rebuild-containers.sh."""
        res = subprocess.run([BASH_BIN, "-n", str(REBUILD_SCRIPT)], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Syntax error in {REBUILD_SCRIPT}: {res.stderr}")

    def test_noop_without_marker_file(self) -> None:
        """When no marker file exists, rebuild script exits cleanly without running docker commands."""
        env = os.environ.copy()
        env.update(
            {
                "BASE_DIR": str(self.base_dir),
                "DOCKER_COMPOSE_FILE": str(self.compose_file),
                "REBUILD_MARKER_FILE": str(self.marker_file),
                "DOCKER_BIN": str(self.bin_dir / "docker"),
            }
        )

        res = subprocess.run([str(REBUILD_SCRIPT)], env=env, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        self.assertFalse(self.docker_log.exists())

    def test_rebuild_executed_and_marker_removed(self) -> None:
        """When marker file exists, docker validation, reload, rebuild, and marker cleanup take place."""
        self.marker_file.write_text("commit-sha-12345\n")

        env = os.environ.copy()
        env.update(
            {
                "BASE_DIR": str(self.base_dir),
                "DOCKER_COMPOSE_FILE": str(self.compose_file),
                "REBUILD_MARKER_FILE": str(self.marker_file),
                "LIVE_NGINX_CONF": str(self.nginx_conf),
                "DOCKER_BIN": str(self.bin_dir / "docker"),
            }
        )

        res = subprocess.run([str(REBUILD_SCRIPT)], env=env, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Script failed: {res.stderr}\n{res.stdout}")
        self.assertIn("Container rebuild/reload complete for commit commit-sha-12345", res.stdout)

        # Marker file was cleaned up
        self.assertFalse(self.marker_file.exists())

        # Verify docker calls
        docker_calls = self.docker_log.read_text()
        self.assertIn("compose -f", docker_calls)
        self.assertIn("nginx -t", docker_calls)
        self.assertIn("nginx -s reload", docker_calls)
        self.assertIn("build review-app", docker_calls)
        self.assertIn("up -d review-app", docker_calls)


class SystemdUnitsLeastPrivilegeTest(unittest.TestCase):
    def test_sync_service_runs_as_unprivileged_deploy_user(self) -> None:
        content = SYNC_SERVICE_FILE.read_text()
        self.assertIn("User=daugavpils-deploy", content)
        self.assertNotIn("User=root", content)
        self.assertNotIn("User=sergei", content)
        self.assertIn("REBUILD_MARKER_FILE=", content)

    def test_rebuild_service_and_path_units(self) -> None:
        service_content = REBUILD_SERVICE_FILE.read_text()
        self.assertIn("User=root", service_content)
        self.assertIn("ExecStart=/opt/daugavpils-fans/rebuild-containers.sh", service_content)

        path_content = REBUILD_PATH_FILE.read_text()
        self.assertIn("PathModified=/opt/daugavpils-fans/.rebuild-needed", path_content)
        self.assertIn("Unit=daugavpils-fans-rebuild.service", path_content)


if __name__ == "__main__":
    unittest.main()
