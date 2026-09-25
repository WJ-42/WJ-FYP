from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class TicketStatus(str, Enum):
    INTAKE = "intake"
    BACKLOG = "backlog"
    SPECD = "specd"
    IN_PROGRESS = "in_progress"
    AWAITING_TEST = "awaiting_test"
    REVIEW = "review"
    DONE = "done"
    ESCALATED = "escalated"
    HALTED = "halted"


class Ticket(BaseModel):
    id: str
    title: str
    description: str
    acceptance_criteria: list[str] = Field(default_factory=list)
    status: TicketStatus = TicketStatus.INTAKE
    assignee_role: str | None = None
    assignee_instance_id: str | None = None
    branch_name: str | None = None
    parent_epic: str | None = None

    # Retry tracking for the in_progress <-> awaiting_test loop. Reset when
    # the ticket leaves that loop (either to review or to escalated).
    retry_count: int = 0
    retry_cap: int = 3

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
