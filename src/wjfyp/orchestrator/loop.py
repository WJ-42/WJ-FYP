from __future__ import annotations

from dataclasses import dataclass

from wjfyp.config import Settings
from wjfyp.eventlog import EventLog
from wjfyp.models.channel import Channel
from wjfyp.models.ticket import Ticket, TicketStatus
from wjfyp.orchestrator.agent import Agent
from wjfyp.orchestrator.agent import AgentContext
from wjfyp.orchestrator.fsm import (
    RETRY_LOOP_EDGE,
    active_role,
    next_state,
    requires_human_approval,
)

TERMINAL_STATES = {TicketStatus.DONE, TicketStatus.HALTED}

# The one trigger the orchestrator itself overrides rather than passing
# through verbatim: an agent always reports "tests_failed" on a failed
# run, but whether that stays within budget or forces an escalation
# depends on Ticket.retry_count, which is orchestrator-owned state the
# agent has no business deciding. See RETRY_LOOP_EDGE in fsm.py.
_RETRY_TRIGGER = "tests_failed"
_RETRY_OVERFLOW_TRIGGER = "retry_cap_exceeded"

# Landing in either of these resets the retry counter - it only tracks
# the awaiting_test <-> in_progress loop, per Ticket.retry_count's
# docstring.
_RETRY_RESETTING_STATES = {TicketStatus.REVIEW, TicketStatus.ESCALATED}


@dataclass
class StepResult:
    ticket: Ticket
    advanced: bool
    paused_trigger: str | None = None


def _pick_instance(role: str, agents: dict[str, list[Agent]], ticket: Ticket) -> Agent:
    """Instance selection within a role's pool. Sticks to whichever
    instance is already assigned to the ticket (continuity across
    retries, matching the handoff-note design's assumption of "the same
    agent picking back up"); otherwise defaults to the pool's first
    instance. A fixed, simple policy - like get_channel_history's naive
    last-N, this is an implementation-level scheduling choice, not a
    Brief-level one, and is revisitable once evaluation shows whether it
    matters.
    """
    pool = agents[role]
    if ticket.assignee_role == role and ticket.assignee_instance_id is not None:
        for candidate in pool:
            if candidate.instance_id == ticket.assignee_instance_id:
                return candidate
    return pool[0]


def step(
    ticket: Ticket,
    channel: Channel,
    event_log: EventLog,
    agents: dict[str, list[Agent]],
    settings: Settings,
) -> StepResult:
    """Run exactly one FSM step: invoke the active role's agent, log its
    message, resolve the trigger it declares into a transition, and
    apply it - unless the transition requires human approval and the
    run is in intervention mode, in which case this pauses and returns
    the proposed trigger for a caller (eventually the dashboard) to
    confirm, reject, or redirect via resume().
    """
    if ticket.status in TERMINAL_STATES:
        return StepResult(ticket=ticket, advanced=False)

    role = active_role(ticket.status)
    agent = _pick_instance(role, agents, ticket)
    if ticket.assignee_role != role or ticket.assignee_instance_id != agent.instance_id:
        ticket = ticket.model_copy(
            update={"assignee_role": role, "assignee_instance_id": agent.instance_id}
        )

    context = AgentContext(
        ticket=ticket,
        channel_id=channel.id,
        role=role,
        instance_id=agent.instance_id,
        handoff=event_log.last_handoff(channel.id),
    )
    response = agent.invoke(context)
    event_log.append_message(response.message)
    trigger = response.trigger

    if (ticket.status, trigger) == (RETRY_LOOP_EDGE[0], _RETRY_TRIGGER):
        retry_count = ticket.retry_count + 1
        if retry_count > ticket.retry_cap:
            trigger = _RETRY_OVERFLOW_TRIGGER
        ticket = ticket.model_copy(update={"retry_count": retry_count})

    if settings.autonomy_mode == "intervention" and requires_human_approval(
        ticket.status, trigger
    ):
        event_log.save_ticket(ticket)
        return StepResult(ticket=ticket, advanced=False, paused_trigger=trigger)

    new_status = next_state(ticket.status, trigger)
    updates: dict[str, object] = {"status": new_status}
    if new_status in _RETRY_RESETTING_STATES:
        updates["retry_count"] = 0
    ticket = ticket.model_copy(update=updates)
    event_log.save_ticket(ticket)
    return StepResult(ticket=ticket, advanced=True)


def run(
    ticket: Ticket,
    channel: Channel,
    event_log: EventLog,
    agents: dict[str, list[Agent]],
    settings: Settings,
    max_steps: int = 1000,
) -> StepResult:
    """Drive a ticket through the FSM until it reaches a terminal state
    or pauses for human approval. `max_steps` is a safety valve against
    a runaway loop (e.g. a misbehaving agent that never terminates) -
    not a real limit expected to be hit in practice.
    """
    event_log.save_ticket(ticket)
    result = StepResult(ticket=ticket, advanced=True)
    for _ in range(max_steps):
        if result.ticket.status in TERMINAL_STATES:
            return StepResult(ticket=result.ticket, advanced=False)
        result = step(result.ticket, channel, event_log, agents, settings)
        if not result.advanced:
            return result
    return result


def resume(
    ticket: Ticket,
    trigger: str,
    channel: Channel,
    event_log: EventLog,
    agents: dict[str, list[Agent]],
    settings: Settings,
) -> StepResult:
    """Apply a human's confirm/reject/redirect decision for a ticket
    paused at a requires_approval transition, then continue the loop.
    The decision surface itself (kanban approve/reject/"repeat with
    notes" controls) is a dashboard-layer concern; this just applies
    whichever trigger the caller supplies.
    """
    new_status = next_state(ticket.status, trigger)
    updates: dict[str, object] = {"status": new_status}
    if new_status in _RETRY_RESETTING_STATES:
        updates["retry_count"] = 0
    ticket = ticket.model_copy(update=updates)
    event_log.save_ticket(ticket)
    return run(ticket, channel, event_log, agents, settings)
