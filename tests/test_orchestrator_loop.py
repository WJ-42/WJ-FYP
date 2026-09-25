"""Exercises the Layer 3 orchestrator loop mechanics end to end, using a
scripted stub agent rather than a real LLM - no model is wired up yet
(config/roles.yaml's `model` fields are still TBD placeholders). This
tests control flow (FSM stepping, retry-cap bookkeeping, intervention
pausing/resume), not agent intelligence.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from wjfyp.config import Settings
from wjfyp.eventlog import EventLog
from wjfyp.models.channel import Channel
from wjfyp.models.message import (
    AgentRef,
    HandoffNote,
    Message,
    MessageContent,
    MessageType,
)
from wjfyp.models.ticket import Ticket, TicketStatus
from wjfyp.orchestrator.agent import AgentContext, AgentResponse
from wjfyp.orchestrator.loop import resume, run, step


class ScriptedAgent:
    """A stub Agent that returns pre-scripted responses in order,
    standing in for a real LLM-backed agent until model selection is
    resolved (see cs3ip-fyp-overview memory).
    """

    def __init__(self, role: str, instance_id: str, script: list[str]):
        self.role = role
        self.instance_id = instance_id
        self._script = list(script)

    def invoke(self, context: AgentContext) -> AgentResponse:
        trigger = self._script.pop(0)
        message = Message(
            sender=AgentRef(role=self.role, instance_id=self.instance_id),
            channel_id=context.channel_id,
            type=MessageType.STATUS_UPDATE,
            ticket_ref=context.ticket.id,
            content=MessageContent(
                text=f"{self.role} fires {trigger}",
                handoff=HandoffNote(
                    done=f"handled by {self.role}",
                    remaining="n/a",
                    notes_for_next="n/a",
                ),
            ),
        )
        return AgentResponse(message=message, trigger=trigger)


def _make_ticket() -> Ticket:
    return Ticket(id="TCK-1", title="Test ticket", description="...")


def _make_channel(ticket: Ticket) -> Channel:
    return Channel(id=f"channel-{ticket.id}", key=f"ticket:{ticket.id}", ticket_ref=ticket.id)


class OrchestratorLoopTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(self._tmpdir.name) / "eventlog.db"
        self.event_log = EventLog(db_path)
        self.addCleanup(self.event_log.close)
        self.addCleanup(self._tmpdir.cleanup)

    def _agents(self, **scripts: list[str]) -> dict[str, list]:
        return {
            role: [ScriptedAgent(role, f"{role}-1", script)]
            for role, script in scripts.items()
        }

    def test_happy_path_reaches_done_in_autonomous_mode(self) -> None:
        ticket = _make_ticket()
        channel = _make_channel(ticket)
        settings = Settings(autonomy_mode="autonomous")
        agents = self._agents(
            cto=["decomposed", "approved"],
            product=["spec_ready"],
            engineering=["assigned", "code_submission", "tests_passed"],
        )

        result = run(ticket, channel, self.event_log, agents, settings)

        self.assertTrue(
            not result.advanced and result.ticket.status == TicketStatus.DONE
        )
        self.assertEqual(self.event_log.get_ticket(ticket.id).status, TicketStatus.DONE)
        # Six agent turns -> six logged messages.
        self.assertEqual(len(self.event_log.get_channel_history(channel.id, 100)), 6)

    def test_retry_cap_exceeded_escalates_instead_of_looping_forever(self) -> None:
        ticket = _make_ticket()
        channel = _make_channel(ticket)
        settings = Settings(autonomy_mode="autonomous")
        # Fails four times in a row: 3 is within cap (loops back to
        # in_progress each time), the 4th must force retry_cap_exceeded.
        # escalated is not terminal - it's a real CTO-actionable state,
        # so the loop keeps going and hands it to the CTO agent, which
        # here reports it can't be resolved either.
        agents = self._agents(
            cto=["decomposed", "cto_cannot_resolve"],
            product=["spec_ready"],
            engineering=[
                "assigned",
                "code_submission", "tests_failed",
                "code_submission", "tests_failed",
                "code_submission", "tests_failed",
                "code_submission", "tests_failed",
            ],
        )

        result = run(ticket, channel, self.event_log, agents, settings)

        self.assertTrue(not result.advanced)
        self.assertEqual(result.ticket.status, TicketStatus.HALTED)
        self.assertEqual(result.ticket.retry_count, 0)  # reset on entering escalated

    def test_intervention_mode_pauses_and_resume_applies_human_decision(self) -> None:
        ticket = _make_ticket()
        channel = _make_channel(ticket)
        settings = Settings(autonomy_mode="intervention")
        agents = self._agents(
            cto=["decomposed", "changes_requested", "approved"],
            product=["spec_ready"],
            engineering=[
                "assigned",
                "code_submission", "tests_passed",
                # After changes_requested sends it back to in_progress,
                # engineering gets invoked again.
                "code_submission", "tests_passed",
            ],
        )

        paused = run(ticket, channel, self.event_log, agents, settings)
        self.assertFalse(paused.advanced)
        self.assertEqual(paused.ticket.status, TicketStatus.REVIEW)
        self.assertEqual(paused.paused_trigger, "changes_requested")

        # A human confirms the proposed trigger; the loop continues,
        # sends the ticket through another in_progress/awaiting_test
        # pass, and pauses again at the next requires_approval gate.
        confirmed = resume(
            paused.ticket, "changes_requested", channel, self.event_log, agents, settings
        )
        self.assertFalse(confirmed.advanced)
        self.assertEqual(confirmed.ticket.status, TicketStatus.REVIEW)
        self.assertEqual(confirmed.paused_trigger, "approved")

        final = resume(
            confirmed.ticket, "approved", channel, self.event_log, agents, settings
        )
        self.assertFalse(final.advanced)
        self.assertEqual(final.ticket.status, TicketStatus.DONE)


if __name__ == "__main__":
    unittest.main()
