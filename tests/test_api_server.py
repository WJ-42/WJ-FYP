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


class RolesApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.event_log = EventLog(Path(self._tmpdir.name) / "eventlog.db")
        self.addCleanup(self.event_log.close)
        self.addCleanup(self._tmpdir.cleanup)
        self.client = TestClient(create_app(self.event_log, poll_interval=0.05))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def _role_body(self, role_id: str = "reviewer") -> dict:
        return {"id": role_id, "name": "Reviewer", "tier": 2, "model": "TBD", "count": 1, "personality": "terse"}

    def test_list_includes_presets_and_is_empty_of_custom_roles_initially(self) -> None:
        response = self.client.get("/api/roles")
        ids = {r["id"] for r in response.json()}
        self.assertEqual(ids, {"cto", "product", "engineering"})

    def test_create_then_list_includes_the_new_role(self) -> None:
        created = self.client.post("/api/roles", json=self._role_body())
        self.assertEqual(created.status_code, 201)
        self.assertFalse(created.json()["is_preset"])

        ids = {r["id"] for r in self.client.get("/api/roles").json()}
        self.assertIn("reviewer", ids)

    def test_create_with_a_preset_id_is_rejected(self) -> None:
        response = self.client.post("/api/roles", json=self._role_body("cto"))
        self.assertEqual(response.status_code, 409)

    def test_create_with_a_duplicate_custom_id_is_rejected(self) -> None:
        self.client.post("/api/roles", json=self._role_body())
        response = self.client.post("/api/roles", json=self._role_body())
        self.assertEqual(response.status_code, 409)

    def test_update_an_existing_custom_role(self) -> None:
        self.client.post("/api/roles", json=self._role_body())
        body = self._role_body()
        body["name"] = "Senior Reviewer"

        response = self.client.put("/api/roles/reviewer", json=body)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["name"], "Senior Reviewer")

    def test_update_a_preset_is_rejected(self) -> None:
        response = self.client.put("/api/roles/cto", json=self._role_body("cto"))
        self.assertEqual(response.status_code, 400)

    def test_update_a_missing_role_is_a_404(self) -> None:
        response = self.client.put("/api/roles/no-such-role", json=self._role_body("no-such-role"))
        self.assertEqual(response.status_code, 404)

    def test_delete_a_custom_role(self) -> None:
        self.client.post("/api/roles", json=self._role_body())
        response = self.client.delete("/api/roles/reviewer")
        self.assertEqual(response.status_code, 204)
        self.assertNotIn("reviewer", {r["id"] for r in self.client.get("/api/roles").json()})

    def test_delete_a_preset_is_rejected(self) -> None:
        response = self.client.delete("/api/roles/cto")
        self.assertEqual(response.status_code, 400)
        self.assertIn("cto", {r["id"] for r in self.client.get("/api/roles").json()})

    def test_delete_a_missing_role_is_a_404(self) -> None:
        response = self.client.delete("/api/roles/no-such-role")
        self.assertEqual(response.status_code, 404)


class DecisionApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.event_log = EventLog(Path(self._tmpdir.name) / "eventlog.db")
        self.addCleanup(self.event_log.close)
        self.addCleanup(self._tmpdir.cleanup)
        self.client = TestClient(create_app(self.event_log, poll_interval=0.05))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def test_records_a_valid_decision(self) -> None:
        ticket = Ticket(
            id="TCK-1", title="t", description="d", status=TicketStatus.REVIEW, pending_trigger="approved"
        )
        self.event_log.save_ticket(ticket)

        response = self.client.post("/api/tickets/TCK-1/decision", json={"trigger": "approved"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["human_decision"], "approved")

    def test_records_notes_alongside_the_decision(self) -> None:
        ticket = Ticket(
            id="TCK-1",
            title="t",
            description="d",
            status=TicketStatus.REVIEW,
            pending_trigger="changes_requested",
        )
        self.event_log.save_ticket(ticket)

        response = self.client.post(
            "/api/tickets/TCK-1/decision",
            json={"trigger": "changes_requested", "notes": "please fix the tests"},
        )

        self.assertEqual(response.json()["decision_notes"], "please fix the tests")

    def test_unknown_ticket_is_a_404(self) -> None:
        response = self.client.post("/api/tickets/no-such-ticket/decision", json={"trigger": "approved"})
        self.assertEqual(response.status_code, 404)

    def test_ticket_not_awaiting_a_decision_is_rejected(self) -> None:
        ticket = Ticket(id="TCK-1", title="t", description="d", status=TicketStatus.DONE)
        self.event_log.save_ticket(ticket)

        response = self.client.post("/api/tickets/TCK-1/decision", json={"trigger": "approved"})

        self.assertEqual(response.status_code, 400)

    def test_invalid_trigger_for_the_ticket_status_is_rejected(self) -> None:
        ticket = Ticket(
            id="TCK-1", title="t", description="d", status=TicketStatus.REVIEW, pending_trigger="approved"
        )
        self.event_log.save_ticket(ticket)

        response = self.client.post("/api/tickets/TCK-1/decision", json={"trigger": "not_a_real_trigger"})

        self.assertEqual(response.status_code, 400)


class ReadOnlyModeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.event_log = EventLog(Path(self.tmp.name) / "events.db")
        self.addCleanup(self.event_log.close)
        self.event_log.save_ticket(
            Ticket(id="TCK-1", title="t", description="d", status=TicketStatus.REVIEW, pending_trigger="approved")
        )
        self.client = TestClient(create_app(self.event_log, poll_interval=0.05, read_only=True))

    def test_reads_still_work(self) -> None:
        self.assertEqual(self.client.get("/api/tickets").status_code, 200)
        self.assertEqual(self.client.get("/api/roles").status_code, 200)

    def test_decision_is_refused_and_not_recorded(self) -> None:
        response = self.client.post("/api/tickets/TCK-1/decision", json={"trigger": "approved"})

        self.assertEqual(response.status_code, 403)
        self.assertIn("read-only", response.json()["detail"])
        self.assertIsNone(self.event_log.get_ticket("TCK-1").human_decision)

    def test_role_writes_are_refused(self) -> None:
        body = {"id": "qa", "name": "QA", "tier": 3, "model": "m"}

        self.assertEqual(self.client.post("/api/roles", json=body).status_code, 403)
        self.assertEqual(self.client.put("/api/roles/qa", json=body).status_code, 403)
        self.assertEqual(self.client.delete("/api/roles/qa").status_code, 403)
        self.assertIsNone(self.event_log.get_custom_role("qa"))


if __name__ == "__main__":
    unittest.main()
