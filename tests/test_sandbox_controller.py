from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from wjfyp.models.ticket import Ticket
from wjfyp.sandbox.controller import TestResult
from wjfyp.sandbox.fake import FakeSandboxController
from wjfyp.sandbox.git_workspace import GitWorkspace


class FakeSandboxControllerTest(unittest.TestCase):
    def test_read_write_round_trip(self) -> None:
        sandbox = FakeSandboxController()
        sandbox.write_file("a.txt", "hello")
        self.assertEqual(sandbox.read_file("a.txt"), "hello")

    def test_run_tests_returns_scripted_results_in_order(self) -> None:
        results = [
            TestResult(
                passed=False,
                fail_to_pass_total=1,
                fail_to_pass_passed=0,
                pass_to_pass_total=0,
                pass_to_pass_passed=0,
                output="fail",
            ),
            TestResult(
                passed=True,
                fail_to_pass_total=1,
                fail_to_pass_passed=1,
                pass_to_pass_total=0,
                pass_to_pass_passed=0,
                output="pass",
            ),
        ]
        sandbox = FakeSandboxController(test_results=results)
        self.assertFalse(sandbox.run_tests().passed)
        self.assertTrue(sandbox.run_tests().passed)

    def test_git_commit_returns_distinct_shas(self) -> None:
        sandbox = FakeSandboxController()
        first = sandbox.git_commit("first")
        second = sandbox.git_commit("second")
        self.assertNotEqual(first, second)

    def test_close_marks_closed(self) -> None:
        sandbox = FakeSandboxController()
        self.assertFalse(sandbox.closed)
        sandbox.close()
        self.assertTrue(sandbox.closed)


class GitWorkspaceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.repo_path = Path(self._tmpdir.name)
        subprocess.run(["git", "init", "-b", "main", str(self.repo_path)], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(self.repo_path), "config", "user.email", "test@example.com"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.repo_path), "config", "user.name", "Test"], check=True
        )
        (self.repo_path / "README.md").write_text("hello\n")
        subprocess.run(["git", "-C", str(self.repo_path), "add", "."], check=True)
        subprocess.run(
            ["git", "-C", str(self.repo_path), "commit", "-m", "initial"],
            check=True,
            capture_output=True,
        )
        self.workspace = GitWorkspace(self.repo_path, default_base="main")

    def test_ensure_branch_creates_branch_once(self) -> None:
        ticket = Ticket(id="TCK-1", title="t", description="d", branch_name="ticket/TCK-1")

        self.workspace.ensure_branch(ticket)
        branches = subprocess.run(
            ["git", "-C", str(self.repo_path), "branch", "--list", "ticket/TCK-1"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        self.assertIn("ticket/TCK-1", branches)

        # Calling again must not error (branch already exists).
        self.workspace.ensure_branch(ticket)

    def test_ensure_branch_requires_branch_name(self) -> None:
        ticket = Ticket(id="TCK-2", title="t", description="d")
        with self.assertRaises(ValueError):
            self.workspace.ensure_branch(ticket)

    def test_clone_url_is_a_file_url_to_the_repo(self) -> None:
        self.assertEqual(self.workspace.clone_url(), f"file://{self.repo_path}")


if __name__ == "__main__":
    unittest.main()
