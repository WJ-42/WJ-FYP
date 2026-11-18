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
from wjfyp.config import load_roles
from wjfyp.eval.baseline import (
    SOLO_PERSONALITY,
    BaselineComparison,
    SoloAgent,
    build_solo_pool,
    compare_to_baseline,
    single_agent_pool,
)
from wjfyp.models.agent import RoleConfig
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


ROLES_GROQ = Path(__file__).resolve().parents[1] / "config" / "roles_groq.yaml"


class _RecordingInner:
    """Stands in for a real OpenAIAgent/ClaudeAgent: has the two
    attributes SoloAgent swaps (_role_config, role) and records what they
    were at each invoke, which is what decides the prompt a real agent
    sends."""

    def __init__(self, role_config: RoleConfig, instance_id: str, script: list[str]):
        self._role_config = role_config
        self.role = role_config.id
        self.instance_id = instance_id
        self._script = list(script)
        self.seen: list[tuple[str, str, str | None, str]] = []

    def invoke(self, context: AgentContext) -> AgentResponse:
        self.seen.append((context.role, self._role_config.id, self._role_config.personality, self.role))
        trigger = self._script.pop(0)
        message = Message(
            sender=AgentRef(role=self.role, instance_id=self.instance_id),
            channel_id=context.channel_id,
            type=MessageType.STATUS_UPDATE,
            ticket_ref=context.ticket.id,
            content=MessageContent(
                text=f"{self.role} fires {trigger}",
                handoff=HandoffNote(done="done", remaining="n/a", notes_for_next="n/a"),
            ),
        )
        return AgentResponse(message=message, trigger=trigger)


class BuildSoloPoolTest(unittest.TestCase):
    SCRIPT = ["decomposed", "spec_ready", "assigned", "code_submission", "approved"]

    def _team_builder(self, built: list):
        """A team builder shaped like the real ones: one agent per role
        instance, each built from its own RoleConfig, with the model
        overridden on every role as --model does."""

        def build(roles_path: Path, _event_log) -> dict:
            team = {}
            for role in load_roles(roles_path):
                role = role.model_copy(update={"model": "qwen3:8b"})
                team[role.id] = [
                    _RecordingInner(role, f"{role.id}-{i + 1}", self.SCRIPT) for i in range(role.count)
                ]
            built.append(team)
            return team

        return build

    def test_one_instance_under_every_role_with_the_team_model(self) -> None:
        built: list = []
        pool = build_solo_pool(ROLES_GROQ, None, self._team_builder(built))

        self.assertEqual(set(pool), {"cto", "product", "engineering"})
        agents = {id(a) for agents in pool.values() for a in agents}
        self.assertEqual(len(agents), 1)
        solo = pool["cto"][0]
        self.assertIsInstance(solo, SoloAgent)
        self.assertEqual(solo.instance_id, "solo-1")
        for role_config in solo._role_configs.values():
            self.assertEqual(role_config.model, "qwen3:8b")
            self.assertEqual(role_config.personality, SOLO_PERSONALITY)
            self.assertEqual(role_config.count, 1)

    def test_each_stage_is_answered_with_that_roles_instructions_and_no_team_persona(self) -> None:
        """The failure this guards against: a real agent takes its stage
        instructions from the RoleConfig it holds, so without the swap an
        agent built as the CTO would do the Engineer's In Progress turn
        with the CTO's instructions and never be told to commit."""
        built: list = []
        pool = build_solo_pool(ROLES_GROQ, None, self._team_builder(built))
        inner = built[0]["cto"][0]

        with tempfile.TemporaryDirectory() as tmp:
            event_log = EventLog(Path(tmp) / "eventlog.db")
            self.addCleanup(event_log.close)
            ticket = _make_ticket()
            result = run(
                ticket, _make_channel(ticket), event_log, pool,
                _sandbox_factory_with_results([_passing_test_result()]),
                Settings(autonomy_mode="autonomous"),
            )

        self.assertEqual(result.ticket.status, TicketStatus.DONE)
        acting_roles = [seen[0] for seen in inner.seen]
        self.assertEqual(acting_roles, ["cto", "product", "engineering", "engineering", "cto"])
        for context_role, config_role, personality, sender_role in inner.seen:
            self.assertEqual(config_role, context_role)
            self.assertEqual(sender_role, context_role)
            self.assertEqual(personality, SOLO_PERSONALITY)
        # The other team members were built but never invoked.
        for role, agents in built[0].items():
            for agent in agents:
                if agent is not inner:
                    self.assertEqual(agent.seen, [])

    def test_solo_persona_carries_no_hierarchy_or_peer_framing(self) -> None:
        for team_persona in (r.personality for r in load_roles(ROLES_GROQ)):
            self.assertNotEqual(team_persona, SOLO_PERSONALITY)
        for word in ("defer", "authoritative", "peer", "equal"):
            self.assertNotIn(word, SOLO_PERSONALITY.lower())

    def test_works_with_the_real_open_model_agent(self) -> None:
        """Construction makes no request, so the real builder can be used
        here. Checks the attributes SoloAgent relies on exist on the real
        class, and that the prompt for In Progress is the Engineer's."""
        from wjfyp.orchestrator.claude_agent import _base_system_prompt
        from wjfyp.orchestrator.openai_agent import OpenAIAgent, build_agent_pool

        pool = build_solo_pool(
            ROLES_GROQ, None,
            lambda path, log: build_agent_pool(
                path, log, api_key="not-needed", base_url="http://127.0.0.1:9/v1", model="qwen3:8b"
            ),
        )
        solo = pool["engineering"][0]
        self.assertIsInstance(solo._inner, OpenAIAgent)
        solo._inner._role_config = solo._role_configs["engineering"]
        prompt = _base_system_prompt(solo._inner._role_config, TicketStatus.IN_PROGRESS, ["code_submission"])
        self.assertIn("git_commit", prompt)
        self.assertIn(SOLO_PERSONALITY, prompt)
        self.assertEqual(solo._inner._role_config.model, "qwen3:8b")


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
