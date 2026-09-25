from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


class Channel(BaseModel):
    """A channel is just an id plus an opaque grouping key. The grouping
    semantics (by ticket, by role, by feature) live entirely in how `key`
    gets populated - that's a policy decision, not a schema one. Default
    policy for v1 is one channel per ticket: key = f"ticket:{ticket_id}".
    """

    id: str
    key: str
    ticket_ref: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    closed_at: datetime | None = None
