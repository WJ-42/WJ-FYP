"""Exercises the single-agent baseline configuration through the real
orchestrator loop (not just the adapter function in isolation) - the
point of the baseline is that it's the identical FSM/sandbox/event-log
machinery, just with one agent answering every role's turn, so proving
that end to end matters more than unit-testing single_agent_pool alone.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from wjfyp.config import Settings
from wjfyp.eval.baseline import BaselineComparison, compare_to_baseline, single_agent_pool
from wjfyp.eval.scoring import TaskScore
from wjfyp.eventlog import EventLog
from wjfyp.models.channel import Channel
from wjfyp.models.message import AgentRef, HandoffNote, Message, MessageContent, MessageType
from wjfyp.models.ticket import Ticket, TicketStatus
from wjfyp.orchestrator.agent import AgentContext, AgentResponse
from wjfyp.orchestrator.loop import run
from wjfyp.sandbox.controller import TestResult
from wjfyp.sandbox.fake import FakeSandboxController


class OneAgentPlaysAllRoles:
    """A single stand-in instance registered under every role, standing
    in for a real LLM-backed baseline agent until model selection is
    resolved - same status as test_orchestrator_loop.py's ScriptedAgent.
    Answers turns strictly in order regardless of which role it's
    currently wearing, since the loop only ever has one active role per
    turn anyway.
    """

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


def _make_ticket() -> Ticket:
    return Ticket(id="TCK-1", title="Test ticket", description="...")


def _make_channel(ticket: Ticket) -> Channel:
    return Channel(id=f"channel-{ticket.id}", key=f"ticket:{ticket.id}", ticket_ref=ticket.id)


def _sandbox_factory_with_results(results: list[TestResult]):
    remaining = list(results)

    def factory(_ticket: Ticket) -> FakeSandboxController:
        return FakeSandboxController(test_results=[remaining.pop(0)])

    return factory


def _passing_test_result() -> TestResult:
    return TestResult(
        passed=True,
        fail_to_pass_total=1,
        fail_to_pass_passed=1,
        pass_to_pass_total=0,
        pass_to_pass_passed=0,
        output="",
    )


class SingleAgentPoolTest(unittest.TestCase):
    def test_maps_every_default_role_to_the_same_instance(self) -> None:
        solo = OneAgentPlaysAllRoles([])
        pool = single_agent_pool(solo)
        self.assertEqual(set(pool.keys()), {"cto", "product", "engineering"})
        for agents in pool.values():
            self.assertEqual(agents, [solo])

    def test_custom_roles_are_honoured(self) -> None:
        solo = OneAgentPlaysAllRoles([])
        pool = single_agent_pool(solo, roles=("cto",))
        self.assertEqual(set(pool.keys()), {"cto"})


class SingleAgentBaselineIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(self._tmpdir.name) / "eventlog.db"
        self.event_log = EventLog(db_path)
        self.addCleanup(self.event_log.close)
        self.addCleanup(self._tmpdir.cleanup)

    def test_one_instance_drives_a_full_ticket_run_wearing_every_hat(self) -> None:
        ticket = _make_ticket()
        channel = _make_channel(ticket)
        settings = Settings(autonomy_mode="autonomous")
        solo = OneAgentPlaysAllRoles(
            ["decomposed", "spec_ready", "assigned", "code_submission", "approved"]
        )
        agents = single_agent_pool(solo)
        sandbox_factory = _sandbox_factory_with_results([_passing_test_result()])

        result = run(ticket, channel, self.event_log, agents, sandbox_factory, settings)

        self.assertEqual(result.ticket.status, TicketStatus.DONE)
        history = self.event_log.get_channel_history(channel.id, 100)
        agent_messages = [m for m in history if m.sender.instance_id == "solo-1"]
        # cto(intake) + product(backlog) + engineering(specd) +
        # engineering(in_progress) + cto(review) = 5 turns, one system
        # tool_result for awaiting_test in between (not counted here).
        self.assertEqual(len(agent_messages), 5)
        self.assertEqual({m.sender.role for m in agent_messages}, {"cto", "product", "engineering"})


class CompareToBaselineTest(unittest.TestCase):
    def _score(self, task_id: str, score: float) -> TaskScore:
        return TaskScore(
            task_id=task_id,
            checkpoint_results=[],
            points_earned=0,
            points_total=0,
            score=score,
            fully_complete=False,
        )

    def test_multi_agent_better_is_true_when_it_scores_higher(self) -> None:
        comparison = compare_to_baseline(self._score("t1", 0.9), self._score("t1", 0.4))
        self.assertIsInstance(comparison, BaselineComparison)
        self.assertAlmostEqual(comparison.score_delta, 0.5)
        self.assertTrue(comparison.multi_agent_better)

    def test_multi_agent_better_is_false_when_baseline_scores_higher(self) -> None:
        comparison = compare_to_baseline(self._score("t1", 0.3), self._score("t1", 0.8))
        self.assertAlmostEqual(comparison.score_delta, -0.5)
        self.assertFalse(comparison.multi_agent_better)

    def test_a_tie_is_not_counted_as_multi_agent_better(self) -> None:
        comparison = compare_to_baseline(self._score("t1", 0.5), self._score("t1", 0.5))
        self.assertEqual(comparison.score_delta, 0.0)
        self.assertFalse(comparison.multi_agent_better)

    def test_mismatched_task_ids_raise(self) -> None:
        with self.assertRaises(ValueError):
            compare_to_baseline(self._score("t1", 0.9), self._score("t2", 0.4))


if __name__ == "__main__":
    unittest.main()
