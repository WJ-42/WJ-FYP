"""Exercises the intervention-decision driver (wjfyp.orchestrator.driver)
against a real EventLog and the same Scripted/Fake stand-ins used
elsewhere - proving a decision recorded via EventLog.record_decision()
(as the dashboard's API would call it) actually gets picked up and
applied, not just that resume() itself works (already covered in
test_orchestrator_loop.py).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from wjfyp.config import Settings
from wjfyp.eventlog import EventLog
from wjfyp.models.channel import Channel
from wjfyp.models.message import AgentRef, HandoffNote, Message, MessageContent, MessageType
from wjfyp.models.ticket import Ticket, TicketStatus
from wjfyp.orchestrator.agent import AgentContext, AgentResponse
from wjfyp.orchestrator.driver import apply_pending_decisions, find_pending_decisions
from wjfyp.orchestrator.loop import run
from wjfyp.sandbox.controller import TestResult
from wjfyp.sandbox.fake import FakeSandboxController


class ScriptedAgent:
    def __init__(self, role: str, instance_id: str, script: list[str]):
        self.role = role
        self.instance_id = instance_id
        self._script = list(script)
        self.received_contexts: list[AgentContext] = []

    def invoke(self, context: AgentContext) -> AgentResponse:
        self.received_contexts.append(context)
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


def _make_ticket() -> Ticket:
    return Ticket(id="TCK-1", title="Test ticket", description="...")


def _make_channel(ticket: Ticket) -> Channel:
    return Channel(id=f"channel-{ticket.id}", key=f"ticket:{ticket.id}", ticket_ref=ticket.id)


def _test_result(passed: bool) -> TestResult:
    return TestResult(
        passed=passed, fail_to_pass_total=0, fail_to_pass_passed=0, pass_to_pass_total=0, pass_to_pass_passed=0, output=""
    )


class DriverTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(self._tmpdir.name) / "eventlog.db"
        self.event_log = EventLog(db_path)
        self.addCleanup(self.event_log.close)
        self.addCleanup(self._tmpdir.cleanup)

    def _agents(self, **scripts: list[str]) -> dict[str, list]:
        return {
            role: [ScriptedAgent(role, f"{role}-1", script)] for role, script in scripts.items()
        }

    def test_find_pending_decisions_excludes_still_waiting_tickets(self) -> None:
        waiting = Ticket(id="TCK-1", title="t", description="d", pending_trigger="approved")
        decided = Ticket(
            id="TCK-2", title="t", description="d", pending_trigger="approved", human_decision="approved"
        )
        done = Ticket(id="TCK-3", title="t", description="d")
        for ticket in (waiting, decided, done):
            self.event_log.save_ticket(ticket)

        pending = find_pending_decisions(self.event_log)

        self.assertEqual([t.id for t in pending], ["TCK-2"])

    def test_apply_pending_decisions_drives_the_ticket_to_completion(self) -> None:
        ticket = _make_ticket()
        channel = _make_channel(ticket)
        settings = Settings(autonomy_mode="intervention")
        agents = self._agents(
            cto=["decomposed", "approved"],
            product=["spec_ready"],
            engineering=["assigned", "code_submission"],
        )
        sandbox_factory = self._sandbox_factory_with_results([_test_result(passed=True)])

        # Pauses at review, awaiting a decision on "approved".
        paused = run(ticket, channel, self.event_log, agents, sandbox_factory, settings)
        self.assertFalse(paused.advanced)
        self.assertEqual(paused.ticket.pending_trigger, "approved")

        # Nothing to apply yet - no decision recorded.
        self.assertEqual(apply_pending_decisions(self.event_log, agents, sandbox_factory, settings), [])

        # The dashboard's decision API records the human's choice...
        self.event_log.record_decision(ticket.id, "approved")

        # ...and the driver picks it up and drives the ticket home.
        results = apply_pending_decisions(self.event_log, agents, sandbox_factory, settings)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].ticket.status, TicketStatus.DONE)
        self.assertEqual(self.event_log.get_ticket(ticket.id).status, TicketStatus.DONE)

    def test_decision_notes_flow_through_to_a_logged_message(self) -> None:
        ticket = _make_ticket()
        channel = _make_channel(ticket)
        settings = Settings(autonomy_mode="intervention")
        agents = self._agents(
            cto=["decomposed", "changes_requested", "approved"],
            product=["spec_ready"],
            engineering=["assigned", "code_submission", "code_submission"],
        )
        sandbox_factory = self._sandbox_factory_with_results(
            [_test_result(passed=True), _test_result(passed=True)]
        )

        run(ticket, channel, self.event_log, agents, sandbox_factory, settings)
        self.event_log.record_decision(ticket.id, "changes_requested", notes="add error handling")

        apply_pending_decisions(self.event_log, agents, sandbox_factory, settings)

        history = self.event_log.get_channel_history(channel.id, 100)
        self.assertTrue(any(m.content.text == "add error handling" for m in history))
        # Not just logged somewhere - actually what the next engineering
        # turn's default context received, checked directly rather than
        # via last_handoff() (which would already be stale here: that
        # same engineering turn logs its own handoff right after,
        # overwriting it) - same guarantee
        # test_orchestrator_loop.py's equivalent test pins against
        # resume() directly; this pins it end to end through the
        # driver's EventLog-mediated path instead.
        engineering_agent = agents["engineering"][0]
        self.assertEqual(
            engineering_agent.received_contexts[-1].handoff.notes_for_next, "add error handling"
        )

    def _sandbox_factory_with_results(self, results: list[TestResult]):
        remaining = list(results)

        def factory(_ticket: Ticket) -> FakeSandboxController:
            return FakeSandboxController(test_results=[remaining.pop(0)])

        return factory


if __name__ == "__main__":
    unittest.main()
