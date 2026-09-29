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
    # Added FYP-27: a CTO that keeps genuinely rejecting resubmitted code
    # can loop forever exactly like a test that keeps failing, so this
    # edge needs the same escalation valve as the awaiting_test one. Not
    # gated by requires_approval - like the awaiting_test escalation,
    # this is the orchestrator overriding what the CTO proposed once the
    # shared budget (RETRY_COUNTED_TRIGGERS) runs out, not something a
    # human or the CTO agent decides.
    Transition(TicketStatus.REVIEW, TicketStatus.ESCALATED, "retry_cap_exceeded"),
    Transition(TicketStatus.ESCALATED, TicketStatus.IN_PROGRESS, "cto_override", requires_approval=True),
    Transition(TicketStatus.ESCALATED, TicketStatus.HALTED, "cto_cannot_resolve", requires_approval=True),
]

_TRANSITION_INDEX: dict[tuple[TicketStatus, str], Transition] = {
    (t.from_state, t.trigger): t for t in TRANSITIONS
}

# The orchestrator-only override trigger both retry-counted edges below
# escalate to once Ticket.retry_cap is exceeded - never something an
# agent chooses for itself (see agent_facing_triggers()).
RETRY_OVERFLOW_TRIGGER = "retry_cap_exceeded"

# The retry-counted edges, sharing one budget (Ticket.retry_count /
# Ticket.retry_cap, default 3, see cs3ip-comm-protocol memory and
# Ticket's own docstring). Firing either of these triggers increments
# the shared counter; once it exceeds retry_cap the orchestrator fires
# RETRY_OVERFLOW_TRIGGER instead, from whichever state the count was
# exceeded at. Originally only the test-failure edge was counted here
# (see FYP-19/FYP-25); the review-rejection edge was added in FYP-27
# after a real run showed it could loop just as unboundedly with no cap
# of its own.
RETRY_COUNTED_TRIGGERS: set[tuple[TicketStatus, str]] = {
    (TicketStatus.AWAITING_TEST, "tests_failed"),
    (TicketStatus.REVIEW, "changes_requested"),
}


def next_state(current: TicketStatus, trigger: str) -> TicketStatus:
    """Look up the next status for a (current_status, trigger) pair.

    Raises KeyError if the transition isn't defined - this is deliberate:
    an undefined transition is a bug in the calling agent/orchestrator
    logic, not something to silently ignore.
    """
    return _TRANSITION_INDEX[(current, trigger)].to_state


def active_role(status: TicketStatus) -> str:
    return ACTIVE_ROLE_FOR_STATUS[status]


def valid_triggers(status: TicketStatus) -> list[str]:
    """Every trigger an agent could legally declare while a ticket sits
    in `status`. Used to build the forced-tool-call schema a real
    LLM-backed agent is given for its turn (see orchestrator/agent.py) -
    the model can only pick from this list, never free-text a trigger,
    so there's no retry-on-malformed-output path to write (confirmed
    2026-09-25, cs3ip-fyp-overview memory's FYP-25 entry).
    """
    return [t.trigger for t in TRANSITIONS if t.from_state == status]


def agent_facing_triggers(status: TicketStatus) -> list[str]:
    """Like valid_triggers(), minus RETRY_OVERFLOW_TRIGGER. Use this (not
    valid_triggers directly) when building an agent's forced-tool-call
    schema (see claude_agent.py's ClaudeAgent.invoke) - the overflow
    trigger is always the orchestrator deciding a shared budget ran out,
    never a real choice the CTO or engineering agent should be offered.
    Before FYP-27 this distinction never mattered in practice, since the
    only state with the overflow trigger (awaiting_test) never invokes an
    agent at all; adding it to review as well (a state the CTO genuinely
    is invoked at) made the filter load-bearing.
    """
    return [t for t in valid_triggers(status) if t != RETRY_OVERFLOW_TRIGGER]


def requires_human_approval(current: TicketStatus, trigger: str) -> bool:
    """Whether this transition should pause for a human signal when the
    run is in intervention mode. Callers in autonomous mode should not
    call this at all - autonomous mode always fires transitions.
    """
    return _TRANSITION_INDEX[(current, trigger)].requires_approval
