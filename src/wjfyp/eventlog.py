from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from wjfyp.models.message import HandoffNote, Message
from wjfyp.models.ticket import Ticket

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    ticket_ref TEXT,
    timestamp TEXT NOT NULL,
    type TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_channel ON messages(channel_id, timestamp);

CREATE TABLE IF NOT EXISTS tickets (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    data TEXT NOT NULL
);
"""


class EventLog:
    """Append-only message store plus ticket-state snapshots, backed by
    SQLite (stdlib sqlite3 - no new dependency, matching the "Datastore"
    decision in cs3ip-comm-protocol memory). The messages table IS the
    event log the comm protocol describes: chat feed / kanban / terminal
    log are all just different renderers over this one stream. The
    tickets table is a queryable projection for convenience, not a
    second source of truth - it could be rebuilt from messages alone.

    One instance is shared across threads (the orchestrator loop and,
    when embedded in the same process as in tests, the dashboard's
    async routes and poll loop): `check_same_thread=False` plus `_lock`
    around every connection access makes that safe, since a bare
    sqlite3.Connection isn't safe for concurrent cross-thread use.
    """

    def __init__(self, path: str | Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        # WAL mode so a second process (the dashboard server) can read
        # while the orchestrator loop is writing, without lock contention.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def append_message(self, message: Message) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO messages (id, channel_id, ticket_ref, timestamp, type, data) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(message.id),
                    message.channel_id,
                    message.ticket_ref,
                    message.timestamp.isoformat(),
                    message.type.value,
                    message.model_dump_json(),
                ),
            )
            self._conn.commit()

    def get_channel_history(self, channel_id: str, n: int) -> list[Message]:
        """Naive last-N retrieval - confirmed 2026-09-25 (see
        cs3ip-comm-protocol memory) over Generative Agents' recency/
        importance/relevance scoring, since this is explicitly a
        fallback path: agents get a handoff note by default and only
        call this when they judge that insufficient.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT data FROM messages WHERE channel_id = ? ORDER BY timestamp DESC LIMIT ?",
                (channel_id, n),
            ).fetchall()
        return [Message.model_validate_json(row[0]) for row in reversed(rows)]

    def last_handoff(self, channel_id: str) -> HandoffNote | None:
        """The most recent handoff note in a channel - what an agent
        actually receives on invocation by default, per
        cs3ip-comm-protocol memory. Not every message carries one, so
        this walks backward from the most recent message.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT data FROM messages WHERE channel_id = ? ORDER BY timestamp DESC",
                (channel_id,),
            ).fetchall()
        for (data,) in rows:
            handoff = Message.model_validate_json(data).content.handoff
            if handoff is not None:
                return handoff
        return None

    def save_ticket(self, ticket: Ticket) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO tickets (id, status, data) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET status = excluded.status, data = excluded.data",
                (ticket.id, ticket.status.value, ticket.model_dump_json()),
            )
            self._conn.commit()

    def get_ticket(self, ticket_id: str) -> Ticket | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT data FROM tickets WHERE id = ?", (ticket_id,)
            ).fetchone()
        return Ticket.model_validate_json(row[0]) if row else None

    def list_tickets(self) -> list[Ticket]:
        with self._lock:
            rows = self._conn.execute("SELECT data FROM tickets").fetchall()
        return [Ticket.model_validate_json(row[0]) for row in rows]

    def get_messages_for_ticket(self, ticket_id: str) -> list[Message]:
        """A ticket's full thread, for the kanban card-to-thread drill-down
        the dashboard design calls for, keyed by ticket_ref rather than
        channel_id so the dashboard doesn't need to know the channel-
        grouping policy in effect.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT data FROM messages WHERE ticket_ref = ? ORDER BY timestamp ASC",
                (ticket_id,),
            ).fetchall()
        return [Message.model_validate_json(row[0]) for row in rows]

    def get_all_messages(self, limit: int = 200) -> list[Message]:
        """The unfiltered feed across every channel, oldest first - the
        default chat feed view."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT data FROM messages ORDER BY rowid DESC LIMIT ?", (limit,)
            ).fetchall()
        return [Message.model_validate_json(row[0]) for row in reversed(rows)]

    def get_messages_since(self, after_rowid: int, limit: int = 200) -> list[tuple[int, Message]]:
        """For the dashboard's polling loop: every message inserted after
        `after_rowid`, oldest first. Uses SQLite's implicit rowid (insert
        order) rather than timestamp, since rowid is guaranteed
        monotonic and collision-free, timestamps aren't.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT rowid, data FROM messages WHERE rowid > ? ORDER BY rowid ASC LIMIT ?",
                (after_rowid, limit),
            ).fetchall()
        return [(rowid, Message.model_validate_json(data)) for rowid, data in rows]

    def latest_message_rowid(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM messages").fetchone()
        return row[0]
