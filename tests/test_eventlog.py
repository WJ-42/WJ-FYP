from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from wjfyp.eventlog import EventLog
from wjfyp.models.agent import RoleConfig
from wjfyp.models.message import (
    AgentRef,
    HandoffNote,
    Message,
    MessageContent,
    MessageType,
)
from wjfyp.models.ticket import Ticket, TicketStatus


def _msg(
    channel_id: str, text: str, handoff: bool = False, ticket_ref: str | None = None
) -> Message:
    return Message(
        sender=AgentRef(role="engineering", instance_id="engineering-1"),
        channel_id=channel_id,
        ticket_ref=ticket_ref,
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

    def test_list_tickets_returns_every_saved_ticket(self) -> None:
        self.log.save_ticket(Ticket(id="TCK-1", title="a", description="d"))
        self.log.save_ticket(Ticket(id="TCK-2", title="b", description="d"))

        ids = sorted(t.id for t in self.log.list_tickets())

        self.assertEqual(ids, ["TCK-1", "TCK-2"])

    def test_get_messages_for_ticket_filters_by_ticket_ref_not_channel(self) -> None:
        self.log.append_message(_msg("c1", "for TCK-1", ticket_ref="TCK-1"))
        self.log.append_message(_msg("c1", "for TCK-2", ticket_ref="TCK-2"))

        thread = self.log.get_messages_for_ticket("TCK-1")

        self.assertEqual([m.content.text for m in thread], ["for TCK-1"])

    def test_get_all_messages_spans_every_channel(self) -> None:
        self.log.append_message(_msg("c1", "one"))
        self.log.append_message(_msg("c2", "two"))

        self.assertEqual(
            [m.content.text for m in self.log.get_all_messages()], ["one", "two"]
        )

    def test_get_messages_since_only_returns_newer_rows(self) -> None:
        self.log.append_message(_msg("c1", "old"))
        baseline = self.log.latest_message_rowid()
        self.log.append_message(_msg("c1", "new"))

        newer = self.log.get_messages_since(baseline)

        self.assertEqual([m.content.text for _, m in newer], ["new"])

    def test_latest_message_rowid_is_zero_when_empty(self) -> None:
        self.assertEqual(self.log.latest_message_rowid(), 0)

    def test_record_decision_updates_the_persisted_ticket(self) -> None:
        ticket = Ticket(id="TCK-1", title="t", description="d", pending_trigger="approved")
        self.log.save_ticket(ticket)

        updated = self.log.record_decision("TCK-1", "approved", notes="looks good")

        self.assertEqual(updated.human_decision, "approved")
        self.assertEqual(updated.decision_notes, "looks good")
        self.assertEqual(self.log.get_ticket("TCK-1").human_decision, "approved")

    def test_record_decision_on_unknown_ticket_raises(self) -> None:
        with self.assertRaises(KeyError):
            self.log.record_decision("no-such-ticket", "approved")


class CustomRoleStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.log = EventLog(Path(self._tmpdir.name) / "eventlog.db")
        self.addCleanup(self.log.close)
        self.addCleanup(self._tmpdir.cleanup)

    def _role(self, role_id: str = "reviewer") -> RoleConfig:
        return RoleConfig(id=role_id, name="Reviewer", tier=2, model="TBD", personality="terse", is_preset=False)

    def test_save_and_get_round_trip(self) -> None:
        self.log.save_custom_role(self._role())
        role = self.log.get_custom_role("reviewer")
        self.assertEqual(role.name, "Reviewer")
        self.assertEqual(role.personality, "terse")

    def test_get_missing_role_returns_none(self) -> None:
        self.assertIsNone(self.log.get_custom_role("no-such-role"))

    def test_save_upserts_on_id(self) -> None:
        self.log.save_custom_role(self._role())
        self.log.save_custom_role(self._role().model_copy(update={"name": "Renamed"}))
        roles = self.log.list_custom_roles()
        self.assertEqual(len(roles), 1)
        self.assertEqual(roles[0].name, "Renamed")

    def test_list_returns_every_saved_role(self) -> None:
        self.log.save_custom_role(self._role("reviewer"))
        self.log.save_custom_role(self._role("tester"))
        ids = {r.id for r in self.log.list_custom_roles()}
        self.assertEqual(ids, {"reviewer", "tester"})

    def test_delete_returns_true_when_a_row_was_removed(self) -> None:
        self.log.save_custom_role(self._role())
        self.assertTrue(self.log.delete_custom_role("reviewer"))
        self.assertEqual(self.log.list_custom_roles(), [])

    def test_delete_returns_false_when_nothing_to_remove(self) -> None:
        self.assertFalse(self.log.delete_custom_role("no-such-role"))


if __name__ == "__main__":
    unittest.main()
