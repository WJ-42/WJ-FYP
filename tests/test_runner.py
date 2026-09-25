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


def _sandbox_factory_with_results(results: list[TestResult]):
    remaining = list(results)

    def factory(_ticket: Ticket) -> FakeSandboxController:
        return FakeSandboxController(test_results=[remaining.pop(0)])

    return factory


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
        sandbox_factory = _sandbox_factory_with_results([_passing_test_result()])

        result = run_eval_task(task, agents, sandbox_factory, self.workspace, self.event_log)

        self.assertEqual(result.task_id, "TCK-1")
        self.assertEqual(result.ticket_id, "TCK-1")
        self.assertEqual(result.ticket_status, "done")
        self.assertEqual(result.score.points_earned, 1)
        self.assertEqual(result.score.points_total, 2)
        self.assertEqual(result.mast_tags, [])  # Done, nothing for the rule-based half to flag

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
            ]
        )

        result = run_eval_task(task, agents, sandbox_factory, self.workspace, self.event_log)

        self.assertEqual(result.ticket_status, "halted")
        self.assertEqual(result.score.points_earned, 0)
        self.assertEqual(
            set(result.mast_tags), {MastFailureMode.FM_1_3, MastFailureMode.FM_3_1}
        )


if __name__ == "__main__":
    unittest.main()
