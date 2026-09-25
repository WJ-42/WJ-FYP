from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from wjfyp.eventlog import EventLog
from wjfyp.models.message import (
    AgentRef,
    HandoffNote,
    Message,
    MessageContent,
    MessageType,
)
from wjfyp.models.ticket import Ticket, TicketStatus


def _msg(channel_id: str, text: str, handoff: bool = False) -> Message:
    return Message(
        sender=AgentRef(role="engineering", instance_id="engineering-1"),
        channel_id=channel_id,
        type=MessageType.STATUS_UPDATE,
        content=MessageContent(
            text=text,
            handoff=HandoffNote(done=text, remaining="", notes_for_next="")
            if handoff
            else None,
        ),
    )


class EventLogTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.log = EventLog(Path(self._tmpdir.name) / "eventlog.db")
        self.addCleanup(self.log.close)
        self.addCleanup(self._tmpdir.cleanup)

    def test_get_channel_history_returns_last_n_in_chronological_order(self) -> None:
        for i in range(5):
            self.log.append_message(_msg("c1", f"msg-{i}"))

        history = self.log.get_channel_history("c1", 3)

        self.assertEqual([m.content.text for m in history], ["msg-2", "msg-3", "msg-4"])

    def test_last_handoff_returns_none_when_no_message_carries_one(self) -> None:
        self.log.append_message(_msg("c1", "no handoff here"))
        self.assertIsNone(self.log.last_handoff("c1"))

    def test_last_handoff_skips_back_to_most_recent_with_one(self) -> None:
        self.log.append_message(_msg("c1", "first", handoff=True))
        self.log.append_message(_msg("c1", "second", handoff=False))

        handoff = self.log.last_handoff("c1")

        self.assertIsNotNone(handoff)
        self.assertEqual(handoff.done, "first")

    def test_save_ticket_upserts(self) -> None:
        ticket = Ticket(id="TCK-1", title="t", description="d")
        self.log.save_ticket(ticket)
        ticket = ticket.model_copy(update={"status": TicketStatus.BACKLOG})
        self.log.save_ticket(ticket)

        fetched = self.log.get_ticket("TCK-1")

        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.status, TicketStatus.BACKLOG)

    def test_get_ticket_returns_none_when_missing(self) -> None:
        self.assertIsNone(self.log.get_ticket("does-not-exist"))


if __name__ == "__main__":
    unittest.main()
