from __future__ import annotations

from dataclasses import dataclass

from wjfyp.models.ticket import TicketStatus

# Which role tier is "active" (expected to produce the next message) while
# a ticket sits in a given status. INTAKE and ESCALATED are handled by the
# CTO agent; other states map to a single acting role by design (see
# cs3ip-comm-protocol memory - one ticket in flight at a time, so there is
# never ambiguity about who acts next).
#
# AWAITING_TEST (confirmed 2026-09-25, see cs3ip-comm-protocol memory):
# the entry below is informational/attribution only (whose submission is
# being verified, e.g. for dashboard display) - the orchestrator loop
# does NOT invoke an engineering agent for this state. It's a
# deterministic system step (orchestrator calls the sandbox's
# run_tests() directly); see src/wjfyp/orchestrator/loop.py's
# _step_awaiting_test.
ACTIVE_ROLE_FOR_STATUS: dict[TicketStatus, str] = {
    TicketStatus.INTAKE: "cto",
    TicketStatus.BACKLOG: "product",
    TicketStatus.SPECD: "engineering",
    TicketStatus.IN_PROGRESS: "engineering",
    TicketStatus.AWAITING_TEST: "engineering",
    TicketStatus.REVIEW: "cto",
    TicketStatus.ESCALATED: "cto",
}


@dataclass(frozen=True)
class Transition:
    from_state: TicketStatus
    to_state: TicketStatus
    trigger: str
    # When True and the run is in intervention mode, the orchestrator must
    # pause on this transition and wait for a human approve/reject/redirect
    # signal instead of firing automatically. Autonomous mode (needed for
    # unattended batch evaluation) ignores this and always fires. Gating
    # points match where intervention controls live on the kanban board:
    # the Review and Escalated states (see cs3ip-dashboard-design memory).
    requires_approval: bool = False


# The full transition table. `trigger` is a symbolic event name the
# orchestrator loop maps from an agent's output (usually derived from
# MessageType plus a semantic outcome, e.g. tests_passed/tests_failed).
TRANSITIONS: list[Transition] = [
    Transition(TicketStatus.INTAKE, TicketStatus.BACKLOG, "decomposed"),
    Transition(TicketStatus.BACKLOG, TicketStatus.SPECD, "spec_ready"),
    Transition(TicketStatus.SPECD, TicketStatus.IN_PROGRESS, "assigned"),
    Transition(TicketStatus.IN_PROGRESS, TicketStatus.AWAITING_TEST, "code_submission"),
    Transition(TicketStatus.AWAITING_TEST, TicketStatus.REVIEW, "tests_passed"),
    Transition(TicketStatus.AWAITING_TEST, TicketStatus.IN_PROGRESS, "tests_failed"),
    Transition(TicketStatus.REVIEW, TicketStatus.DONE, "approved", requires_approval=True),  # this IS the merge
    Transition(TicketStatus.REVIEW, TicketStatus.IN_PROGRESS, "changes_requested", requires_approval=True),
    Transition(TicketStatus.AWAITING_TEST, TicketStatus.ESCALATED, "retry_cap_exceeded"),
    Transition(TicketStatus.ESCALATED, TicketStatus.IN_PROGRESS, "cto_override", requires_approval=True),
    Transition(TicketStatus.ESCALATED, TicketStatus.HALTED, "cto_cannot_resolve", requires_approval=True),
]

_TRANSITION_INDEX: dict[tuple[TicketStatus, str], Transition] = {
    (t.from_state, t.trigger): t for t in TRANSITIONS
}

# The retry-counted loop. A `tests_failed` transition increments
# Ticket.retry_count; once it exceeds Ticket.retry_cap (default 3, see
# cs3ip-comm-protocol memory) the orchestrator loop fires
# `retry_cap_exceeded` instead of `tests_failed`.
RETRY_LOOP_EDGE = (TicketStatus.AWAITING_TEST, TicketStatus.IN_PROGRESS)


def next_state(current: TicketStatus, trigger: str) -> TicketStatus:
    """Look up the next status for a (current_status, trigger) pair.

    Raises KeyError if the transition isn't defined - this is deliberate:
    an undefined transition is a bug in the calling agent/orchestrator
    logic, not something to silently ignore.
    """
    return _TRANSITION_INDEX[(current, trigger)].to_state


def active_role(status: TicketStatus) -> str:
    return ACTIVE_ROLE_FOR_STATUS[status]


def requires_human_approval(current: TicketStatus, trigger: str) -> bool:
    """Whether this transition should pause for a human signal when the
    run is in intervention mode. Callers in autonomous mode should not
    call this at all - autonomous mode always fires transitions.
    """
    return _TRANSITION_INDEX[(current, trigger)].requires_approval
