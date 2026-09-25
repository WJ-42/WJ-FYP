from __future__ import annotations

from wjfyp.config import Settings
from wjfyp.eventlog import EventLog
from wjfyp.models.channel import Channel
from wjfyp.models.ticket import Ticket
from wjfyp.orchestrator.agent import Agent
from wjfyp.orchestrator.loop import HiddenTestLookup, SandboxFactory, StepResult, resume

# Same derivation run_eval_task() and every test helper already use:
# Channel.id is deterministic from ticket.id, so it doesn't need its own
# persistence - reconstructing it here needs no new EventLog lookup.
#
# This assumes ticket-based channel grouping, matching every other call
# site in the codebase today (see Channel's own docstring: "Default
# policy for v1 is one channel per ticket"). cs3ip-comm-protocol memory
# describes role-/feature-based grouping as a future user-selectable
# option, but nothing anywhere yet actually implements picking a
# non-ticket-based key - if that lands later, this derivation (and
# run_eval_task's identical one) would both need a real channel lookup
# instead, since Channel isn't persisted anywhere queryable by ticket id
# currently. Not a gap introduced here, just flagging where it would bite.
def _channel_for(ticket: Ticket) -> Channel:
    return Channel(id=f"channel-{ticket.id}", key=f"ticket:{ticket.id}", ticket_ref=ticket.id)


def find_pending_decisions(event_log: EventLog) -> list[Ticket]:
    """Tickets paused for intervention-mode approval (pending_trigger
    set) that a human has since recorded a decision for (human_decision
    set) - the set this driver still needs to apply. A ticket with
    pending_trigger but no human_decision yet is still waiting; this
    correctly excludes it.
    """
    return [
        ticket
        for ticket in event_log.list_tickets()
        if ticket.pending_trigger is not None and ticket.human_decision is not None
    ]


def apply_pending_decisions(
    event_log: EventLog,
    agents: dict[str, list[Agent]],
    sandbox_factory: SandboxFactory,
    settings: Settings,
    hidden_tests: HiddenTestLookup | None = None,
) -> list[StepResult]:
    """Poll-and-apply pass over every ticket with a recorded human
    decision waiting to be picked up: calls resume() for each, same
    polling pattern the dashboard's own poll_loop() already uses for
    new messages. Meant to be called periodically by whatever process
    is actually driving the orchestrator - no persistent scheduling
    loop of its own here, since there's no real LLM-backed Agent yet
    for a standing process to run against (see cs3ip-fyp-overview
    memory); this is the tested, ready-to-call building block for when
    one exists, not a running service today.
    """
    results = []
    for ticket in find_pending_decisions(event_log):
        channel = _channel_for(ticket)
        result = resume(
            ticket,
            ticket.human_decision,  # type: ignore[arg-type]
            channel,
            event_log,
            agents,
            sandbox_factory,
            settings,
            hidden_tests=hidden_tests,
            notes=ticket.decision_notes,
        )
        results.append(result)
    return results
