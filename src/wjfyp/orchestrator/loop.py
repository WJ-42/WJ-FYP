from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from wjfyp.config import Settings
from wjfyp.eventlog import EventLog
from wjfyp.models.channel import Channel
from wjfyp.models.message import AgentRef, HandoffNote, Message, MessageContent, MessageType
from wjfyp.models.ticket import Ticket, TicketStatus
from wjfyp.orchestrator.agent import Agent, AgentContext
from wjfyp.orchestrator.fsm import active_role, next_state, requires_human_approval
from wjfyp.sandbox.controller import SandboxController
from wjfyp.sandbox.hidden_tests import HiddenTestSpec

TERMINAL_STATES = {TicketStatus.DONE, TicketStatus.HALTED}

SandboxFactory = Callable[[Ticket], SandboxController]

# Resolves a ticket to its SWE-bench-style hidden test definition, or
# None outside an evaluation run - see _step_awaiting_test. A callable
# lookup rather than a Ticket field, matching how sandbox_factory is
# already threaded through rather than baked into Ticket: hidden tests
# are an evaluation-harness concern layered on top of the core ticket
# model, not part of it (see cs3ip-evaluation-detail memory).
HiddenTestLookup = Callable[[Ticket], "HiddenTestSpec | None"]

# The one trigger the orchestrator itself overrides rather than passing
# through verbatim: run_tests() always reports a plain pass/fail, but
# whether a failure stays within budget or forces an escalation depends
# on Ticket.retry_count, which is orchestrator-owned state.
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
    # The current ticket-attempt's open sandbox, if any - only non-None
    # while a ticket is between entering in_progress and its
    # awaiting_test check resolving. Callers driving the loop step by
    # step (rather than via run()) must thread this back into the next
    # step() call.
    sandbox: SandboxController | None = None


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


def _apply_transition(
    ticket: Ticket, trigger: str, event_log: EventLog
) -> Ticket:
    new_status = next_state(ticket.status, trigger)
    # Any transition actually firing means whatever pause/decision state
    # was on this ticket has been resolved - clear it unconditionally
    # rather than only in the resume() path, so it can never linger past
    # the pause it belonged to (see Ticket.pending_trigger's docstring).
    updates: dict[str, object] = {
        "status": new_status,
        "pending_trigger": None,
        "human_decision": None,
        "decision_notes": None,
    }
    if new_status in _RETRY_RESETTING_STATES:
        updates["retry_count"] = 0
    ticket = ticket.model_copy(update=updates)
    event_log.save_ticket(ticket)
    return ticket


def _step_awaiting_test(
    ticket: Ticket,
    channel: Channel,
    event_log: EventLog,
    sandbox: SandboxController,
    hidden_test_spec: HiddenTestSpec | None,
) -> StepResult:
    """AWAITING_TEST is a deterministic, system-verified step - not an
    agent turn (confirmed 2026-09-25: letting the engineering agent
    self-report its own test result would make it both author and judge
    of its work, a real task-verification failure mode; see
    cs3ip-comm-protocol memory). Outside an evaluation run
    (hidden_test_spec is None) the orchestrator calls the sandbox's
    plain run_tests(); inside one it calls run_hidden_tests() instead,
    which is the only place a ticket's FAIL_TO_PASS/PASS_TO_PASS tests
    ever get written into the sandbox - never during in_progress, so the
    engineering agent's own run_tests() tool calls never see them (see
    HiddenTestSpec's docstring).
    """
    if hidden_test_spec is not None:
        result = sandbox.run_hidden_tests(hidden_test_spec)
    else:
        result = sandbox.run_tests()

    summary = f"run_tests: {'passed' if result.passed else 'failed'}"
    if result.fail_to_pass_total or result.pass_to_pass_total:
        summary += (
            f" (FAIL_TO_PASS {result.fail_to_pass_passed}/{result.fail_to_pass_total}, "
            f"PASS_TO_PASS {result.pass_to_pass_passed}/{result.pass_to_pass_total})"
        )
    message = Message(
        sender=AgentRef(role="system", instance_id="sandbox"),
        channel_id=channel.id,
        type=MessageType.TOOL_RESULT,
        ticket_ref=ticket.id,
        content=MessageContent(text=f"{summary}\n{result.output}"),
    )
    event_log.append_message(message)

    trigger = "tests_passed" if result.passed else "tests_failed"
    if trigger == "tests_failed":
        retry_count = ticket.retry_count + 1
        if retry_count > ticket.retry_cap:
            trigger = _RETRY_OVERFLOW_TRIGGER
        ticket = ticket.model_copy(update={"retry_count": retry_count})

    ticket = _apply_transition(ticket, trigger, event_log)
    sandbox.close()  # the attempt is over either way
    return StepResult(ticket=ticket, advanced=True, sandbox=None)


def step(
    ticket: Ticket,
    channel: Channel,
    event_log: EventLog,
    agents: dict[str, list[Agent]],
    sandbox_factory: SandboxFactory,
    settings: Settings,
    sandbox: SandboxController | None = None,
    hidden_tests: HiddenTestLookup | None = None,
) -> StepResult:
    """Run exactly one FSM step.

    At awaiting_test this deterministically runs the current attempt's
    sandbox tests (see _step_awaiting_test). At every other state it
    invokes the active role's agent, logs its message, and resolves the
    trigger it declares into a transition - unless that transition
    requires human approval and the run is in intervention mode, in
    which case this pauses and returns the proposed trigger for a
    caller (eventually the dashboard) to confirm, reject, or redirect
    via resume().

    `sandbox` is the currently open attempt sandbox, if any (None
    between attempts) - callers driving step-by-step must pass back
    whatever the previous StepResult.sandbox was; run() does this
    automatically. `hidden_tests` is None outside an evaluation run;
    when supplied it's consulted only at awaiting_test.
    """
    if ticket.status in TERMINAL_STATES:
        return StepResult(ticket=ticket, advanced=False, sandbox=sandbox)

    if ticket.status == TicketStatus.AWAITING_TEST:
        if sandbox is None:
            raise RuntimeError(
                f"ticket {ticket.id} reached awaiting_test with no open attempt sandbox"
            )
        spec = hidden_tests(ticket) if hidden_tests is not None else None
        return _step_awaiting_test(ticket, channel, event_log, sandbox, spec)

    role = active_role(ticket.status)
    agent = _pick_instance(role, agents, ticket)
    if ticket.assignee_role != role or ticket.assignee_instance_id != agent.instance_id:
        ticket = ticket.model_copy(
            update={"assignee_role": role, "assignee_instance_id": agent.instance_id}
        )

    # A ticket entering in_progress starts a new attempt - fresh
    # sandbox, per cs3ip-sandbox-design memory's "one container per
    # ticket-attempt, not per run" granularity.
    if ticket.status == TicketStatus.IN_PROGRESS and sandbox is None:
        sandbox = sandbox_factory(ticket)

    context = AgentContext(
        ticket=ticket,
        channel_id=channel.id,
        role=role,
        instance_id=agent.instance_id,
        handoff=event_log.last_handoff(channel.id),
        sandbox=sandbox,
    )
    response = agent.invoke(context)
    event_log.append_message(response.message)
    trigger = response.trigger

    if settings.autonomy_mode == "intervention" and requires_human_approval(
        ticket.status, trigger
    ):
        # Persisted, not just returned in StepResult: the dashboard (a
        # separate process, per cs3ip-comm-protocol memory) has no other
        # way to know this ticket is waiting on a human or what was
        # proposed - see Ticket.pending_trigger's docstring.
        ticket = ticket.model_copy(update={"pending_trigger": trigger})
        event_log.save_ticket(ticket)
        return StepResult(ticket=ticket, advanced=False, paused_trigger=trigger, sandbox=sandbox)

    ticket = _apply_transition(ticket, trigger, event_log)
    return StepResult(ticket=ticket, advanced=True, sandbox=sandbox)


def run(
    ticket: Ticket,
    channel: Channel,
    event_log: EventLog,
    agents: dict[str, list[Agent]],
    sandbox_factory: SandboxFactory,
    settings: Settings,
    max_steps: int = 1000,
    hidden_tests: HiddenTestLookup | None = None,
) -> StepResult:
    """Drive a ticket through the FSM until it reaches a terminal state
    or pauses for human approval. `max_steps` is a safety valve against
    a runaway loop (e.g. a misbehaving agent that never terminates) -
    not a real limit expected to be hit in practice.
    """
    event_log.save_ticket(ticket)
    result = StepResult(ticket=ticket, advanced=True, sandbox=None)
    for _ in range(max_steps):
        if result.ticket.status in TERMINAL_STATES:
            return StepResult(ticket=result.ticket, advanced=False)
        result = step(
            result.ticket,
            channel,
            event_log,
            agents,
            sandbox_factory,
            settings,
            result.sandbox,
            hidden_tests,
        )
        if not result.advanced:
            return result
    return result


def resume(
    ticket: Ticket,
    trigger: str,
    channel: Channel,
    event_log: EventLog,
    agents: dict[str, list[Agent]],
    sandbox_factory: SandboxFactory,
    settings: Settings,
    hidden_tests: HiddenTestLookup | None = None,
    notes: str | None = None,
) -> StepResult:
    """Apply a human's confirm/reject/redirect decision for a ticket
    paused at a requires_approval transition, then continue the loop.
    The decision surface itself (kanban approve/reject/"repeat with
    notes" controls) is a dashboard-layer concern; this just applies
    whichever trigger the caller supplies. Pauses only ever happen at
    review/escalated, both reached only after awaiting_test has already
    closed that attempt's sandbox, so there's never one to thread
    through here.

    `notes` is the free-text half of the "repeat with notes" option
    (see cs3ip-project-diary memory) - logged as a message on the
    channel before the transition applies, carrying its own HandoffNote
    so it's what last_handoff() returns for the very next agent turn -
    not just appended to the channel history (an agent's default
    AgentContext.handoff is the ONLY thing guaranteed seen; text-only
    messages with no handoff attached are invisible there and only
    reachable via get_channel_history(), an explicit fallback agents
    aren't guaranteed to call). A first attempt at this logged a
    handoff-less message and would have silently dropped the human's
    guidance for any agent that didn't happen to fall back to channel
    history - caught reviewing this layer, fixed before it shipped
    without a test that would have caught it. Only logged when notes
    are actually given - a plain approve/reject doesn't need a message
    of its own beyond the agent turns already logged.
    """
    if notes:
        event_log.append_message(
            Message(
                sender=AgentRef(role="human", instance_id="dashboard"),
                channel_id=channel.id,
                type=MessageType.REVIEW_FEEDBACK,
                ticket_ref=ticket.id,
                content=MessageContent(
                    text=notes,
                    handoff=HandoffNote(
                        done="Human reviewed and requested changes.",
                        remaining="Address the feedback below and resubmit.",
                        notes_for_next=notes,
                    ),
                ),
            )
        )
    ticket = _apply_transition(ticket, trigger, event_log)
    return run(ticket, channel, event_log, agents, sandbox_factory, settings, hidden_tests=hidden_tests)
