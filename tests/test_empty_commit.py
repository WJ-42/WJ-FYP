"""Committing with nothing staged is a distinct, common, recoverable
mistake, and git reports it in prose that reads like success.

On the comparison run of 03/11/2026 an agent opened its turn with
git_commit before doing any work and got back a raw exit-1 dump
beginning "On branch ticket/... Your branch is up to date", which says
nothing about what it should do instead. One wasted iteration per
occurrence, and no cue to go and make an edit.
"""

from __future__ import annotations

import unittest

from wjfyp.orchestrator.claude_agent import _execute_sandbox_tool, _is_nothing_to_commit


class _Sandbox:
    def __init__(self, error: Exception | None = None, sha: str = "abc123"):
        self._error = error
        self._sha = sha

    def git_commit(self, message: str) -> str:
        if self._error is not None:
            raise self._error
        return self._sha


EMPTY_TREE = RuntimeError(
    "failed to commit (exit 1): On branch ticket/x\n"
    "Your branch is up to date with 'origin/x'.\n\nnothing to commit, working tree clean"
)


class EmptyCommitTest(unittest.TestCase):
    def test_it_says_what_to_do_instead(self):
        text, is_error = _execute_sandbox_tool(
            _Sandbox(error=EMPTY_TREE), "git_commit", {"message": "fix"}
        )
        self.assertTrue(is_error)
        self.assertIn("nothing to commit", text)
        self.assertIn("Make your edits first", text)
        self.assertIn("replace_in_file", text)

    def test_the_raw_git_prose_is_not_what_the_agent_sees(self):
        """"Your branch is up to date" reads like success and is the
        reason this needed translating at all."""
        text, _ = _execute_sandbox_tool(
            _Sandbox(error=EMPTY_TREE), "git_commit", {"message": "fix"}
        )
        self.assertNotIn("up to date", text)

    def test_other_commit_failures_keep_their_real_message(self):
        """This narrows one case. A real failure must not be softened
        into an advisory, because git_commit is the orchestrator's
        authoritative 'the code is committed' signal."""
        text, is_error = _execute_sandbox_tool(
            _Sandbox(error=RuntimeError("failed to push to 'ticket/x': permission denied")),
            "git_commit",
            {"message": "fix"},
        )
        self.assertTrue(is_error)
        self.assertIn("permission denied", text)
        self.assertNotIn("Make your edits first", text)

    def test_a_successful_commit_is_untouched(self):
        text, is_error = _execute_sandbox_tool(
            _Sandbox(sha="deadbeef"), "git_commit", {"message": "fix"}
        )
        self.assertFalse(is_error)
        self.assertIn("deadbeef", text)

    def test_the_unstaged_phrasing_is_recognised_too(self):
        """git words it differently when changes exist but are not
        staged, and both mean the same thing to an agent."""
        self.assertTrue(_is_nothing_to_commit("no changes added to commit"))
        self.assertTrue(_is_nothing_to_commit("nothing added to commit but untracked files"))
        self.assertFalse(_is_nothing_to_commit("failed to push: permission denied"))


if __name__ == "__main__":
    unittest.main()
