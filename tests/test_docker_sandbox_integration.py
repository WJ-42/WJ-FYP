"""Real end-to-end test of DockerAttemptSandbox against an actual Docker
daemon. Self-skips when the daemon isn't reachable (permission denied on
the socket, daemon not running, etc.) rather than failing the whole
suite - see cs3ip-fyp-overview memory for the current docker-group-
permission status. Requires the sandbox image to be built first:

    docker build -t wjfyp-sandbox:latest -f docker/sandbox.Dockerfile .
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from wjfyp.models.ticket import Ticket
from wjfyp.sandbox.docker_controller import DEFAULT_IMAGE, DockerAttemptSandbox
from wjfyp.sandbox.git_workspace import GitWorkspace
from wjfyp.sandbox.hidden_tests import HiddenTestSpec


def _docker_available() -> bool:
    try:
        import docker

        docker.from_env().ping()
        return True
    except Exception:
        return False


def _image_available(image: str) -> bool:
    try:
        import docker

        docker.from_env().images.get(image)
        return True
    except Exception:
        return False


@unittest.skipUnless(_docker_available(), "docker daemon not reachable")
@unittest.skipUnless(
    _image_available(DEFAULT_IMAGE),
    f"{DEFAULT_IMAGE} not built - run docker build -t {DEFAULT_IMAGE} -f docker/sandbox.Dockerfile .",
)
class DockerAttemptSandboxIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.repo_path = Path(self._tmpdir.name)
        subprocess.run(["git", "init", "-b", "main", str(self.repo_path)], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(self.repo_path), "config", "user.email", "test@example.com"],
            check=True,
        )
        subprocess.run(["git", "-C", str(self.repo_path), "config", "user.name", "Test"], check=True)
        (self.repo_path / "test_sample.py").write_text("def test_ok():\n    assert True\n")
        subprocess.run(["git", "-C", str(self.repo_path), "add", "."], check=True)
        subprocess.run(
            ["git", "-C", str(self.repo_path), "commit", "-m", "initial"],
            check=True,
            capture_output=True,
        )
        self.workspace = GitWorkspace(self.repo_path, default_base="main")

    def test_run_command_read_write_and_tests(self) -> None:
        ticket = Ticket(id="TCK-1", title="t", description="d", branch_name="ticket/TCK-1")
        sandbox = DockerAttemptSandbox(ticket, self.workspace)
        try:
            sandbox.write_file("greeting.txt", "hi")
            self.assertEqual(sandbox.read_file("greeting.txt"), "hi")

            result = sandbox.run_tests()
            self.assertTrue(result.passed)
        finally:
            sandbox.close()

    def test_run_tests_with_no_tests_in_the_repo_counts_as_passed(self) -> None:
        """Found for real on WJ-FYP's first live Option B run, 2026-09-29:
        a repo/task with no pytest-expressible tests at all (this
        fixture has none - setUp's own test_sample.py isn't checked out
        on this ticket's branch, only main) legitimately has nothing to
        fail here, and pytest's own exit code 5 says exactly that -
        distinct from exit code 1, a real test failure.
        """
        ticket = Ticket(id="TCK-2", title="t", description="d", branch_name="ticket/TCK-2")
        sandbox = DockerAttemptSandbox(ticket, self.workspace)
        try:
            sandbox.run_command("rm test_sample.py")
            result = sandbox.run_tests()
            self.assertTrue(result.passed)
            self.assertIn("no tests ran", result.output)
        finally:
            sandbox.close()

    def test_git_commit_pushes_back_to_the_host_workspace(self) -> None:
        # The core sandbox-design mechanic that had never actually run
        # against a real daemon before Docker access was fixed (FYP-22):
        # a container-side commit is supposed to reach the host's
        # GitWorkspace via `git push origin HEAD:<branch>` against the
        # bind-mounted /host-repo, exercising exactly the UID-mismatch
        # question DockerAttemptSandbox's own docstring flags as an
        # unresolved known limitation - this doesn't resolve that
        # concern, but it's the first real evidence either way.
        ticket = Ticket(id="TCK-3", title="t", description="d", branch_name="ticket/TCK-3")
        sandbox = DockerAttemptSandbox(ticket, self.workspace)
        try:
            sandbox.write_file("new_file.txt", "written from inside the container")
            sha = sandbox.git_commit("add new_file.txt")
            self.assertTrue(sha)
        finally:
            sandbox.close()

        # Verified on the HOST side, independent of the sandbox/container
        # entirely - a real subprocess git call against self.repo_path.
        log = subprocess.run(
            ["git", "-C", str(self.repo_path), "log", "ticket/TCK-3", "-1", "--format=%H %s"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        self.assertTrue(log.startswith(sha), f"host branch HEAD {log!r} doesn't match pushed sha {sha!r}")
        self.assertIn("add new_file.txt", log)

        content = subprocess.run(
            ["git", "-C", str(self.repo_path), "show", "ticket/TCK-3:new_file.txt"],
            check=True, capture_output=True, text=True,
        ).stdout
        self.assertEqual(content, "written from inside the container")

    def test_run_hidden_tests_flips_fail_to_pass_and_keeps_pass_to_pass(self) -> None:
        ticket = Ticket(id="TCK-2", title="t", description="d", branch_name="ticket/TCK-2")
        sandbox = DockerAttemptSandbox(ticket, self.workspace)
        try:
            spec = HiddenTestSpec(
                test_files={
                    "test_hidden.py": (
                        "def test_new_behaviour():\n"
                        "    assert True\n"
                    )
                },
                fail_to_pass=["test_hidden.py::test_new_behaviour"],
                pass_to_pass=["test_sample.py::test_ok"],
            )

            result = sandbox.run_hidden_tests(spec)

            self.assertTrue(result.passed)
            self.assertEqual(result.fail_to_pass_passed, 1)
            self.assertEqual(result.pass_to_pass_passed, 1)
        finally:
            sandbox.close()


if __name__ == "__main__":
    unittest.main()
