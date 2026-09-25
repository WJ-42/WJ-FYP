from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from wjfyp.api.server import create_app
from wjfyp.eventlog import EventLog
from wjfyp.models.message import AgentRef, Message, MessageContent, MessageType
from wjfyp.models.ticket import Ticket, TicketStatus


class ApiServerTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.event_log = EventLog(Path(self._tmpdir.name) / "eventlog.db")
        self.addCleanup(self.event_log.close)
        self.addCleanup(self._tmpdir.cleanup)
        # Fast polling so the websocket tests don't have to wait ~0.5s
        # (the production default) for a broadcast to arrive. Entered as
        # a context manager (not just constructed) so the app's lifespan
        # actually runs and starts the poll loop - without this, the
        # websocket tests hang forever waiting for a broadcast that
        # never comes.
        self.client = TestClient(create_app(self.event_log, poll_interval=0.05))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def test_list_tickets_reflects_the_event_log(self) -> None:
        self.event_log.save_ticket(Ticket(id="TCK-1", title="t", description="d"))

        response = self.client.get("/api/tickets")

        self.assertEqual(response.status_code, 200)
        self.assertEqual([t["id"] for t in response.json()], ["TCK-1"])

    def test_ticket_messages_filters_to_that_ticket(self) -> None:
        message = Message(
            sender=AgentRef(role="engineering", instance_id="engineering-1"),
            channel_id="c1",
            ticket_ref="TCK-1",
            type=MessageType.STATUS_UPDATE,
            content=MessageContent(text="hello"),
        )
        self.event_log.append_message(message)

        response = self.client.get("/api/tickets/TCK-1/messages")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["content"]["text"], "hello")

    def test_all_messages_spans_channels(self) -> None:
        for channel in ("c1", "c2"):
            self.event_log.append_message(
                Message(
                    sender=AgentRef(role="engineering", instance_id="engineering-1"),
                    channel_id=channel,
                    type=MessageType.STATUS_UPDATE,
                    content=MessageContent(text=f"in {channel}"),
                )
            )

        response = self.client.get("/api/messages")

        self.assertEqual(len(response.json()), 2)

    def test_websocket_broadcasts_a_new_message(self) -> None:
        with self.client.websocket_connect("/ws") as websocket:
            self.event_log.append_message(
                Message(
                    sender=AgentRef(role="cto", instance_id="cto-1"),
                    channel_id="c1",
                    type=MessageType.STATUS_UPDATE,
                    content=MessageContent(text="live update"),
                )
            )
            payload = websocket.receive_json()

        self.assertEqual(payload["type"], "message")
        self.assertEqual(payload["data"]["content"]["text"], "live update")

    def test_websocket_broadcasts_a_ticket_status_change(self) -> None:
        ticket = Ticket(id="TCK-1", title="t", description="d")
        self.event_log.save_ticket(ticket)

        with self.client.websocket_connect("/ws") as websocket:
            self.event_log.save_ticket(
                ticket.model_copy(update={"status": TicketStatus.BACKLOG})
            )
            payload = websocket.receive_json()

        self.assertEqual(payload["type"], "ticket_update")
        self.assertEqual(payload["data"]["status"], "backlog")


if __name__ == "__main__":
    unittest.main()
