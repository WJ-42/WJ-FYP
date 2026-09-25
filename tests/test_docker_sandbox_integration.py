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
