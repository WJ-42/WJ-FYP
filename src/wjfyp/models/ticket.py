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

    # Shared correction-attempt budget for both loop-back edges: a failed
    # test sending a ticket from awaiting_test back to in_progress, and
    # the CTO requesting changes at review, also back to in_progress.
    # One shared counter, not one per edge (see fsm.RETRY_COUNTED_TRIGGERS
    # and loop.py's _apply_retry_budget) - a CTO that keeps genuinely
    # rejecting resubmitted code that already passed its own tests is
    # just as capable of looping forever as a test that keeps failing
    # (FYP-27: a real run hit exactly this, 34 rejection cycles with no
    # cap, since the counter used to reset every time the ticket reached
    # review at all). Reset only on reaching escalated - a human's
    # cto_override decision is what grants a genuinely fresh budget.
    retry_count: int = 0
    retry_cap: int = 3

    # One level up from retry_count: how many times this ticket has had
    # its retry budget refreshed via a CTO override at escalated. Never
    # reset (unlike retry_count) - it's a lifetime count for this ticket,
    # not something a later state should wipe. Without this, cto_override
    # granting a fresh retry_count every time has no limit on how many
    # times that can happen in total, the same "a reset with no bound on
    # repetitions" shape retry_count itself had before FYP-27, just one
    # level up the escalated <-> in_progress cycle instead of the
    # in_progress <-> awaiting_test/review one. See
    # fsm.ESCALATION_COUNTED_TRIGGERS and loop.py's _apply_escalation_budget.
    escalation_count: int = 0
    escalation_cap: int = 3

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
