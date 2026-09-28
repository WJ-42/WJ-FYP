from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from wjfyp.models.ticket import Ticket
from wjfyp.sandbox.controller import TestResult
from wjfyp.sandbox.fake import FakeSandboxController
from wjfyp.sandbox.git_workspace import GitWorkspace
from wjfyp.sandbox.hidden_tests import HiddenTestSpec


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

    def test_run_hidden_tests_writes_spec_files_and_returns_scripted_result(self) -> None:
        result = TestResult(
            passed=True,
            fail_to_pass_total=1,
            fail_to_pass_passed=1,
            pass_to_pass_total=1,
            pass_to_pass_passed=1,
            output="pass",
        )
        sandbox = FakeSandboxController(hidden_test_results=[result])
        spec = HiddenTestSpec(
            test_files={"tests/test_hidden.py": "def test_hidden():\n    assert True\n"},
            fail_to_pass=["tests/test_hidden.py::test_hidden"],
            pass_to_pass=[],
        )

        actual = sandbox.run_hidden_tests(spec)

        self.assertIs(actual, result)
        self.assertEqual(sandbox.files["tests/test_hidden.py"], spec.test_files["tests/test_hidden.py"])
        self.assertEqual(sandbox.hidden_test_specs_seen, [spec])

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

    def _commit_on_branch(self, branch: str, filename: str, content: str) -> None:
        """Simulates what git_commit() pushing back from a container
        actually does to this host workspace: a real commit lands on the
        ticket branch while the workspace itself ends up back on main -
        the invariant merge() depends on (see its own docstring)."""
        subprocess.run(["git", "-C", str(self.repo_path), "checkout", branch], check=True, capture_output=True)
        (self.repo_path / filename).write_text(content)
        subprocess.run(["git", "-C", str(self.repo_path), "add", "."], check=True)
        subprocess.run(
            ["git", "-C", str(self.repo_path), "commit", "-m", f"add {filename}"],
            check=True, capture_output=True,
        )
        subprocess.run(["git", "-C", str(self.repo_path), "checkout", "main"], check=True, capture_output=True)

    def test_merge_requires_branch_name(self) -> None:
        ticket = Ticket(id="TCK-3", title="t", description="d")
        with self.assertRaises(ValueError):
            self.workspace.merge(ticket)

    def test_merge_brings_the_branch_commit_into_base(self) -> None:
        ticket = Ticket(id="TCK-4", title="Add a feature", description="d", branch_name="ticket/TCK-4")
        self.workspace.ensure_branch(ticket)
        self._commit_on_branch("ticket/TCK-4", "feature.txt", "real work\n")

        self.workspace.merge(ticket)

        self.assertTrue((self.repo_path / "feature.txt").exists())
        current_branch = subprocess.run(
            ["git", "-C", str(self.repo_path), "branch", "--show-current"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        self.assertEqual(current_branch, "main")

    def test_merge_returns_the_resulting_commit_sha(self) -> None:
        ticket = Ticket(id="TCK-5", title="t", description="d", branch_name="ticket/TCK-5")
        self.workspace.ensure_branch(ticket)
        self._commit_on_branch("ticket/TCK-5", "other.txt", "content\n")

        sha = self.workspace.merge(ticket)

        head = subprocess.run(
            ["git", "-C", str(self.repo_path), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        self.assertEqual(sha, head)

    def test_merge_raises_on_a_real_conflict(self) -> None:
        ticket = Ticket(id="TCK-6", title="t", description="d", branch_name="ticket/TCK-6")
        self.workspace.ensure_branch(ticket)
        # Conflicting changes to the same line on both sides.
        (self.repo_path / "README.md").write_text("changed on main\n")
        subprocess.run(["git", "-C", str(self.repo_path), "add", "."], check=True)
        subprocess.run(
            ["git", "-C", str(self.repo_path), "commit", "-m", "change on main"],
            check=True, capture_output=True,
        )
        self._commit_on_branch("ticket/TCK-6", "README.md", "changed on the ticket branch\n")

        with self.assertRaises(subprocess.CalledProcessError):
            self.workspace.merge(ticket)

    def test_diff_against_base_returns_empty_for_a_never_created_branch(self) -> None:
        self.assertEqual(self.workspace.diff_against_base("ticket/no-such-branch"), "")

    def test_diff_against_base_shows_the_branch_diff_before_merge(self) -> None:
        ticket = Ticket(id="TCK-7", title="t", description="d", branch_name="ticket/TCK-7")
        self.workspace.ensure_branch(ticket)
        self._commit_on_branch("ticket/TCK-7", "unmerged.txt", "not merged yet\n")

        diff = self.workspace.diff_against_base("ticket/TCK-7")

        self.assertIn("unmerged.txt", diff)
        self.assertIn("not merged yet", diff)

    def test_diff_against_base_still_shows_the_diff_after_merge(self) -> None:
        # A direct branch-vs-base diff would read empty here, since the
        # branch is now an ancestor of base - this is exactly why
        # diff_against_base uses merge-base instead (see its docstring).
        ticket = Ticket(id="TCK-8", title="t", description="d", branch_name="ticket/TCK-8")
        self.workspace.ensure_branch(ticket)
        self._commit_on_branch("ticket/TCK-8", "merged.txt", "already merged\n")
        self.workspace.merge(ticket)

        diff = self.workspace.diff_against_base("ticket/TCK-8")

        self.assertIn("merged.txt", diff)
        self.assertIn("already merged", diff)


if __name__ == "__main__":
    unittest.main()
