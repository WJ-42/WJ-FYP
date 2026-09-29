from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from wjfyp.config import Settings
from wjfyp.eventlog import EventLog
from wjfyp.models.channel import Channel
from wjfyp.models.message import AgentRef, HandoffNote, Message, MessageContent, MessageType
from wjfyp.models.ticket import Ticket, TicketStatus
from wjfyp.orchestrator.agent import Agent, AgentContext
from wjfyp.orchestrator.fsm import (
    EMPTY_DIFF_TRIGGER,
    ESCALATION_COUNTED_TRIGGERS,
    ESCALATION_OVERFLOW_TRIGGER,
    RETRY_COUNTED_TRIGGERS,
    RETRY_OVERFLOW_TRIGGER,
    active_role,
    next_state,
    requires_human_approval,
)
from wjfyp.sandbox.controller import SandboxController
from wjfyp.sandbox.git_workspace import GitWorkspace
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

# Landing here resets the shared retry counter (see Ticket.retry_count's
# docstring and fsm.RETRY_COUNTED_TRIGGERS). Only escalated resets it -
# a human's cto_override decision is what grants a genuinely fresh
# budget. REVIEW used to reset it too, which was the actual bug behind
# FYP-27: a ticket could cycle through review indefinitely, each
# changes_requested rejection getting a full fresh budget just for
# having reached review at all, regardless of how many times that had
# already happened.
_RETRY_RESETTING_STATES = {TicketStatus.ESCALATED}


def _apply_budget(
    ticket: Ticket,
    trigger: str,
    counted_triggers: set[tuple[TicketStatus, str]],
    count_field: str,
    cap_field: str,
    overflow_trigger: str,
) -> tuple[Ticket, str]:
    """Shared shape behind both budgets below: if (ticket.status, trigger)
    is one of `counted_triggers`, increments the named counter field and,
    once it exceeds the named cap field, overrides the trigger to
    `overflow_trigger` instead of letting the original one fire.
    Otherwise returns the ticket and trigger unchanged.

    Call this immediately before whichever _apply_transition() call will
    actually fire the trigger - not any earlier - so a trigger that gets
    paused for human approval (and possibly overridden to something else
    entirely by that human's decision) is never counted against a budget
    before it's known to actually apply. See FYP-27.
    """
    if (ticket.status, trigger) not in counted_triggers:
        return ticket, trigger
    count = getattr(ticket, count_field) + 1
    if count > getattr(ticket, cap_field):
        trigger = overflow_trigger
    return ticket.model_copy(update={count_field: count}), trigger


def _apply_retry_budget(ticket: Ticket, trigger: str) -> tuple[Ticket, str]:
    """The awaiting_test/review loop-back budget (Ticket.retry_count /
    retry_cap, see its own docstring and fsm.RETRY_COUNTED_TRIGGERS) -
    once exceeded, escalates instead of looping back to engineering
    again.
    """
    return _apply_budget(
        ticket, trigger, RETRY_COUNTED_TRIGGERS, "retry_count", "retry_cap", RETRY_OVERFLOW_TRIGGER
    )


def _apply_escalation_budget(ticket: Ticket, trigger: str) -> tuple[Ticket, str]:
    """One level up from _apply_retry_budget: how many times a ticket's
    retry budget can be refreshed via a CTO override at escalated
    (Ticket.escalation_count / escalation_cap, see its own docstring and
    fsm.ESCALATION_COUNTED_TRIGGERS) - once exceeded, halts instead of
    granting yet another fresh retry budget. Found auditing the rest of
    the transition graph for the same "reset with no bound on repeated
    resets" shape after fixing the review-rejection loop in FYP-27.
    """
    return _apply_budget(
        ticket,
        trigger,
        ESCALATION_COUNTED_TRIGGERS,
        "escalation_count",
        "escalation_cap",
        ESCALATION_OVERFLOW_TRIGGER,
    )


def _apply_empty_diff_guard(
    ticket: Ticket, trigger: str, workspace: GitWorkspace | None
) -> tuple[Ticket, str]:
    """Refuses to let an "approved" review transition merge an empty
    diff (FYP-31). Found for real running the first genuinely successful
    Option B comparison: ClaudeAgent._invoke_in_progress's iteration-
    budget fallback can force a code_submission through with no commit
    ever having happened (flagged "[submitted without committing]" in
    its own narrative), but nothing stopped the CTO approving that
    narrative anyway - an empty change got merged and scored as if real
    work had landed. Checked deterministically here instead of trusted
    to the CTO's own judgement, the same "don't let a failure look like
    success" principle as git_commit's _exec_checked and the
    code_submission commit gate itself.

    Only applies to (REVIEW, "approved") with a real workspace and a
    real branch - outside a real run (tests, FakeSandboxController-based
    eval runs with no git state) there's nothing to diff, the same
    precedent as _apply_transition's own merge-skip when workspace is
    None. Call this before _apply_retry_budget, not after: an
    overridden "empty_diff" trigger is itself one of
    fsm.RETRY_COUNTED_TRIGGERS, so a persistently-uncommitted engineer
    still eventually escalates rather than looping this edge forever,
    exactly like the other two retry-counted edges.
    """
    if workspace is None or (ticket.status, trigger) != (TicketStatus.REVIEW, "approved"):
        return ticket, trigger
    if not ticket.branch_name:
        return ticket, trigger
    diff = workspace.diff_against_base(ticket.branch_name)
    if diff.strip():
        return ticket, trigger
    return ticket, EMPTY_DIFF_TRIGGER


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
    ticket: Ticket, trigger: str, event_log: EventLog, workspace: GitWorkspace | None = None
) -> Ticket:
    new_status = next_state(ticket.status, trigger)
    if new_status == TicketStatus.DONE and workspace is not None:
        # The actual merge to the base branch - found missing entirely
        # 2026-09-28 running a real ticket through intervention mode end
        # to end: approving a ticket only ever flipped Ticket.status, no
        # git operation ran at all, despite fsm.py's own TRANSITIONS
        # comment and the CTO's system prompt both stating "approval IS
        # the merge." Deliberately not wrapped in try/except: a ticket
        # must not become Done while its merge silently failed, the same
        # "don't let a failure look like success" principle as
        # DockerAttemptSandbox.git_commit's own _exec_checked. `workspace`
        # is optional so every existing caller that never had a real
        # GitWorkspace (tests, evaluation runs against FakeSandboxController)
        # is unaffected.
        workspace.merge(ticket)
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
    workspace: GitWorkspace | None = None,
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
    ticket, trigger = _apply_retry_budget(ticket, trigger)
    ticket = _apply_transition(ticket, trigger, event_log, workspace)
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
    workspace: GitWorkspace | None = None,
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
    when supplied it's consulted only at awaiting_test. `workspace` is
    the GitWorkspace to merge into on a Review -> Done "approved"
    transition; None outside a real run (tests, evaluation runs against
    FakeSandboxController) where there's no real git state to merge.
    """
    if ticket.status in TERMINAL_STATES:
        return StepResult(ticket=ticket, advanced=False, sandbox=sandbox)

    if ticket.status == TicketStatus.AWAITING_TEST:
        if sandbox is None:
            raise RuntimeError(
                f"ticket {ticket.id} reached awaiting_test with no open attempt sandbox"
            )
        spec = hidden_tests(ticket) if hidden_tests is not None else None
        return _step_awaiting_test(ticket, channel, event_log, sandbox, spec, workspace)

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

    ticket, trigger = _apply_empty_diff_guard(ticket, trigger, workspace)
    ticket, trigger = _apply_retry_budget(ticket, trigger)
    ticket, trigger = _apply_escalation_budget(ticket, trigger)
    ticket = _apply_transition(ticket, trigger, event_log, workspace)
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
    workspace: GitWorkspace | None = None,
) -> StepResult:
    """Drive a ticket through the FSM until it reaches a terminal state
    or pauses for human approval. `max_steps` is a safety valve against
    a runaway loop (e.g. a misbehaving agent that never terminates) -
    not a real limit expected to be hit in practice. `workspace`: see
    step()'s docstring.
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
            workspace,
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
    workspace: GitWorkspace | None = None,
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

    `trigger` still goes through the same shared retry and escalation
    budgets an autonomous-mode trigger would (see _apply_retry_budget and
    _apply_escalation_budget) - a human repeatedly choosing "request
    changes" or "override" in intervention mode can drive either loop
    exactly as unboundedly as an autonomous CTO agent can, so both draw
    from the same caps regardless of who's deciding (FYP-27). A human
    clicking "approve" is equally subject to _apply_empty_diff_guard - an
    empty diff doesn't become mergeable just because a human, rather than
    the CTO, was the one who approved it (FYP-31).
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
    ticket, trigger = _apply_empty_diff_guard(ticket, trigger, workspace)
    ticket, trigger = _apply_retry_budget(ticket, trigger)
    ticket, trigger = _apply_escalation_budget(ticket, trigger)
    ticket = _apply_transition(ticket, trigger, event_log, workspace)
    return run(
        ticket, channel, event_log, agents, sandbox_factory, settings,
        hidden_tests=hidden_tests, workspace=workspace,
    )
