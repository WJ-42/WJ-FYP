"""Exercises run_eval_task() - the piece that actually ties Layers A-D
together into one pipeline - using the same Scripted/Fake stand-ins the
rest of the suite uses, since no real LLM-backed Agent exists yet.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from wjfyp.config import Settings
from wjfyp.eval.baseline import single_agent_pool
from wjfyp.eval.mast import MastFailureMode
from wjfyp.eval.runner import run_eval_task
from wjfyp.eval.task import Checkpoint, EvalTask, GradingContext
from wjfyp.eventlog import EventLog
from wjfyp.models.message import AgentRef, HandoffNote, Message, MessageContent, MessageType
from wjfyp.models.ticket import Ticket
from wjfyp.orchestrator.agent import AgentContext, AgentResponse
from wjfyp.sandbox.controller import TestResult
from wjfyp.sandbox.fake import FakeSandboxController
from wjfyp.sandbox.git_workspace import GitWorkspace


class OneAgentPlaysAllRoles:
    def __init__(self, script: list[str]):
        self.role = "solo"
        self.instance_id = "solo-1"
        self._script = list(script)

    def invoke(self, context: AgentContext) -> AgentResponse:
        trigger = self._script.pop(0)
        message = Message(
            sender=AgentRef(role=context.role, instance_id=self.instance_id),
            channel_id=context.channel_id,
            type=MessageType.STATUS_UPDATE,
            ticket_ref=context.ticket.id,
            content=MessageContent(
                text=f"{context.role} fires {trigger}",
                handoff=HandoffNote(done="done", remaining="n/a", notes_for_next="n/a"),
            ),
        )
        return AgentResponse(message=message, trigger=trigger)


def _sandbox_factory_with_results(results: list[TestResult], workspace: GitWorkspace | None = None):
    """`workspace`, if given, gets ensure_branch() called on it exactly
    like a real DockerAttemptSandbox's constructor does (docker_controller.py),
    plus one real marker commit on that branch - FakeSandboxController
    itself has no git awareness at all (its "files" never touch the
    actual repo), so without at least a real commit, a ticket reaching
    Done would merge a branch identical to base: workspace.merge() (now
    wired through run_eval_task, see its own docstring) would run
    without error either way, but there'd be nothing real to prove it
    actually did something rather than being silently skipped, which is
    exactly the class of bug this fix addresses.
    """
    remaining = list(results)
    attempt = [0]  # mutable counter, closed over below - each retry gets a distinct commit

    def factory(ticket: Ticket) -> FakeSandboxController:
        if workspace is not None:
            branch = workspace.ensure_branch(ticket)
            attempt[0] += 1
            _commit_marker_on_branch(workspace, branch, attempt[0])
        return FakeSandboxController(test_results=[remaining.pop(0)])

    return factory


def _commit_marker_on_branch(workspace: GitWorkspace, branch: str, attempt: int) -> None:
    """Content includes `attempt` since a retried ticket calls the
    sandbox factory (and so this) more than once for the same branch -
    an identical second commit would be a no-op git error ("nothing to
    commit"), so each attempt needs something genuinely new to commit,
    same as a real engineer's retry would.
    """
    repo = workspace.repo_path
    subprocess.run(["git", "-C", str(repo), "checkout", branch], check=True, capture_output=True)
    (repo / f"{branch.replace('/', '_')}.marker").write_text(f"attempt {attempt}\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", f"work on {branch}, attempt {attempt}"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "checkout", workspace.default_base], check=True, capture_output=True
    )


def _passing_test_result() -> TestResult:
    return TestResult(
        passed=True,
        fail_to_pass_total=0,
        fail_to_pass_passed=0,
        pass_to_pass_total=0,
        pass_to_pass_passed=0,
        output="",
    )


class RunEvalTaskTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.repo_path = Path(self._tmpdir.name)
        subprocess.run(["git", "init", "-b", "main", str(self.repo_path)], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(self.repo_path), "config", "user.email", "test@example.com"], check=True
        )
        subprocess.run(["git", "-C", str(self.repo_path), "config", "user.name", "Test"], check=True)
        (self.repo_path / "README.md").write_text("hello\n")
        subprocess.run(["git", "-C", str(self.repo_path), "add", "."], check=True)
        subprocess.run(
            ["git", "-C", str(self.repo_path), "commit", "-m", "initial"], check=True, capture_output=True
        )
        self.workspace = GitWorkspace(self.repo_path, default_base="main")

        db_path = Path(self._tmpdir.name) / "eventlog.db"
        self.event_log = EventLog(db_path)
        self.addCleanup(self.event_log.close)

    def test_a_completed_run_is_scored_and_tagged(self) -> None:
        task = EvalTask(
            task_id="TCK-1",
            title="Demo task",
            prompt="Do the thing.",
            checkpoints=[
                Checkpoint(id="always-true", description="trivially true", points=1, grader=lambda ctx: True),
                Checkpoint(id="always-false", description="trivially false", points=1, grader=lambda ctx: False),
            ],
        )
        solo = OneAgentPlaysAllRoles(
            ["decomposed", "spec_ready", "assigned", "code_submission", "approved"]
        )
        agents = single_agent_pool(solo)
        sandbox_factory = _sandbox_factory_with_results([_passing_test_result()], workspace=self.workspace)

        result = run_eval_task(task, agents, sandbox_factory, self.workspace, self.event_log)

        self.assertEqual(result.task_id, "TCK-1")
        self.assertEqual(result.ticket_id, "TCK-1")
        self.assertEqual(result.ticket_status, "done")
        self.assertEqual(result.score.points_earned, 1)
        self.assertEqual(result.score.points_total, 2)
        self.assertEqual(result.mast_tags, [])  # Done, nothing for the rule-based half to flag
        # workspace= is now threaded through to run() (a real bug this
        # session found) - reaching Done should genuinely have run
        # `git merge --no-ff`, bringing the branch's real marker commit
        # into main's history on top of setUp's single "initial" commit
        # (3 total: initial, the marker commit, the merge commit itself -
        # --no-ff brings the branch's own commits into main's log, not
        # just a single empty merge marker).
        log = subprocess.run(
            ["git", "-C", str(self.repo_path), "log", "--oneline", "main"],
            check=True, capture_output=True, text=True,
        ).stdout.strip().splitlines()
        self.assertEqual(len(log), 3)
        self.assertIn("Merge ticket/TCK-1", log[0])
        self.assertTrue((self.repo_path / "ticket_TCK-1.marker").exists())

        # And diff_against_base should still show the real change even
        # though it's already merged - the exact post-merge case
        # diff_against_base's own docstring is about.
        diff = self.workspace.diff_against_base("ticket/TCK-1")
        self.assertIn("ticket_TCK-1.marker", diff)

    def test_a_halted_run_is_scored_low_and_tagged(self) -> None:
        task = EvalTask(
            task_id="TCK-2",
            title="Demo task",
            prompt="Do the thing.",
            checkpoints=[
                Checkpoint(id="never-gets-there", description="unreachable", points=5, grader=lambda ctx: False)
            ],
        )
        # cto decomposes, then can't resolve the escalation; product/
        # engineering fail their tests 4 times running out the retry cap.
        agents = {
            "cto": [OneAgentPlaysAllRoles(["decomposed", "cto_cannot_resolve"])],
            "product": [OneAgentPlaysAllRoles(["spec_ready"])],
            "engineering": [
                OneAgentPlaysAllRoles(
                    ["assigned", "code_submission", "code_submission", "code_submission", "code_submission"]
                )
            ],
        }
        sandbox_factory = _sandbox_factory_with_results(
            [
                TestResult(
                    passed=False,
                    fail_to_pass_total=0,
                    fail_to_pass_passed=0,
                    pass_to_pass_total=0,
                    pass_to_pass_passed=0,
                    output="",
                )
                for _ in range(4)
            ],
            workspace=self.workspace,
        )

        result = run_eval_task(task, agents, sandbox_factory, self.workspace, self.event_log)

        self.assertEqual(result.ticket_status, "halted")
        self.assertEqual(result.score.points_earned, 0)
        self.assertEqual(
            set(result.mast_tags), {MastFailureMode.FM_1_3, MastFailureMode.FM_3_1}
        )


if __name__ == "__main__":
    unittest.main()
