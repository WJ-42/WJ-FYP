"""A turn that burns its whole iteration budget on tool calls the schema
rejects must not look, afterwards, like a turn that simply thought a lot.

Those are different findings. "The model emits calls our schema rejects"
is an integration problem with a fix. "The model could not do the task"
is a claim about capability, and it is the kind of claim a dissertation
has to be able to support. The rejected calls are deliberately kept OUT
of the message history, so unless the rejection is written down at the
moment it happens it leaves no trace at all.

Prompted by a real qwen3:4b In Progress turn on 03/11/2026 that made one
successful read_file, spent 8,513 completion tokens, and ended on the
forced declaration having committed nothing - with no way to tell from
the log which of the two explanations applied.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from wjfyp.eventlog import EventLog
from wjfyp.models.agent import RoleConfig
from wjfyp.models.message import MessageType
from wjfyp.models.ticket import Ticket, TicketStatus
from wjfyp.orchestrator.agent import AgentContext
from wjfyp.orchestrator.openai_agent import OpenAIAgent

ROLE = RoleConfig(id="engineering", name="Engineer", tier=3, model="qwen3:4b", count=1)


def context() -> AgentContext:
    return AgentContext(
        ticket=Ticket(
            id="LOC-1",
            title="Validate the expiry header",
            description="It is read but never validated.",
            acceptance_criteria=["Reject malformed values."],
            status=TicketStatus.IN_PROGRESS,
        ),
        channel_id="eng",
        role="engineering",
        instance_id="engineering-1",
    )


def notes(log: EventLog) -> list[str]:
    return [
        m.content.text
        for m in log.get_all_messages()
        if m.type == MessageType.SYSTEM and "rejected tool call" in m.content.text
    ]


class RejectedCallLogTest(unittest.TestCase):
    def test_an_invented_tool_name_is_recorded_with_the_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = EventLog(Path(tmp) / "events.db")
            agent = OpenAIAgent(ROLE, "engineering-1", client=object(), event_log=log)
            agent._log_rejected_calls(context(), ["run_file"], [], uuid4())

            self.assertEqual(len(notes(log)), 1)
            self.assertIn("no such tool: run_file", notes(log)[0])
            self.assertIn("qwen3:4b", notes(log)[0])

    def test_a_schema_mismatch_is_distinguished_from_an_unknown_tool(self):
        """The fixes differ. An invented name means the model is not
        reading the tool list; wrong arguments mean it is, and got the
        shape wrong. Collapsing both into "rejected" would lose that."""
        with tempfile.TemporaryDirectory() as tmp:
            log = EventLog(Path(tmp) / "events.db")
            agent = OpenAIAgent(ROLE, "engineering-1", client=object(), event_log=log)
            agent._log_rejected_calls(context(), [], ["run_command"], uuid4())

            text = notes(log)[0]
            self.assertIn("arguments did not match schema: run_command", text)
            self.assertNotIn("no such tool", text)

    def test_both_kinds_in_one_turn_are_both_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = EventLog(Path(tmp) / "events.db")
            agent = OpenAIAgent(ROLE, "engineering-1", client=object(), event_log=log)
            agent._log_rejected_calls(context(), ["run_file"], ["write_file"], uuid4())

            text = notes(log)[0]
            self.assertIn("no such tool: run_file", text)
            self.assertIn("arguments did not match schema: write_file", text)

    def test_it_is_attached_to_the_turn_so_the_dashboard_can_group_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = EventLog(Path(tmp) / "events.db")
            agent = OpenAIAgent(ROLE, "engineering-1", client=object(), event_log=log)
            turn_id = uuid4()
            agent._log_rejected_calls(context(), ["run_file"], [], turn_id)

            written = [m for m in log.get_all_messages() if m.type == MessageType.SYSTEM]
            self.assertEqual(written[0].parent_id, turn_id)

    def test_an_iteration_with_no_tool_call_is_recorded_with_what_it_said(self):
        """The case that mattered. qwen3:4b produced no call on 11 of 12
        iterations, reasoning at length and never acting, and that left
        no trace at all until this existed - the only way to spot it was
        to subtract executed and rejected calls from the iteration
        count, which is inference from absence."""
        with tempfile.TemporaryDirectory() as tmp:
            log = EventLog(Path(tmp) / "events.db")
            agent = OpenAIAgent(ROLE, "engineering-1", client=object(), event_log=log)
            agent._log_no_tool_call(context(), uuid4(), "I should now write the validation logic.")

            written = [
                m.content.text
                for m in log.get_all_messages()
                if m.type == MessageType.SYSTEM and "no tool call" in m.content.text
            ]
            self.assertEqual(len(written), 1)
            self.assertIn("qwen3:4b", written[0])
            self.assertIn("I should now write the validation logic.", written[0])

    def test_a_silent_iteration_records_the_silence(self):
        """An empty content with no call is still worth a line: it is a
        different signature from a model narrating its intentions."""
        with tempfile.TemporaryDirectory() as tmp:
            log = EventLog(Path(tmp) / "events.db")
            agent = OpenAIAgent(ROLE, "engineering-1", client=object(), event_log=log)
            agent._log_no_tool_call(context(), uuid4(), None)

            written = [
                m.content.text
                for m in log.get_all_messages()
                if m.type == MessageType.SYSTEM and "no tool call" in m.content.text
            ]
            self.assertIn("said: ''", written[0])

    def test_no_event_log_is_not_an_error(self):
        """Unit tests and ad-hoc probes construct agents without one."""
        agent = OpenAIAgent(ROLE, "engineering-1", client=object())
        agent._log_rejected_calls(context(), ["run_file"], [], uuid4())
        agent._log_no_tool_call(context(), uuid4(), "anything")


if __name__ == "__main__":
    unittest.main()
