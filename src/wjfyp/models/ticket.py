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

    # Intervention-mode human decision handoff (see cs3ip-dashboard-design
    # memory and orchestrator/loop.py's step()). pending_trigger is the
    # trigger an agent proposed at a requires_approval transition, set
    # only while the ticket is paused waiting on a human; human_decision/
    # decision_notes are what a human (via the dashboard) records in
    # response. The orchestrator and dashboard are meant to run as
    # separate processes sharing only this persisted state (see
    # cs3ip-comm-protocol memory), so these three fields - not an
    # in-memory value - are how a paused ticket and its resolution cross
    # that boundary. All three are cleared by _apply_transition() the
    # moment any transition actually fires, so they never linger past
    # the pause they belong to.
    pending_trigger: str | None = None
    human_decision: str | None = None
    decision_notes: str | None = None

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
