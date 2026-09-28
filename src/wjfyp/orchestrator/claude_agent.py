from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import anthropic

from wjfyp.eventlog import EventLog
from wjfyp.models.agent import RoleConfig
from wjfyp.models.message import AgentRef, HandoffNote, Message, MessageContent, MessageType, TokenCost
from wjfyp.models.ticket import TicketStatus
from wjfyp.orchestrator.agent import AgentContext, AgentResponse
from wjfyp.orchestrator.fsm import valid_triggers
from wjfyp.sandbox.controller import SandboxController

_TOOL_NAME = "declare_outcome"

# A turn shouldn't run forever - this bounds real cost/time, and forces
# a decision if the model keeps poking at the sandbox without ever
# committing. See invoke_in_progress()'s final-turn fallback for what
# happens if this is reached.
MAX_SANDBOX_ITERATIONS = 12

# Cap on any single tool result's logged/replayed text, so one huge
# read_file or run_command output can't blow up context or cost. Applied
# to both what's fed back to the model and what's written to the event
# log.
_MAX_TOOL_RESULT_CHARS = 20000

# Every iteration of the In Progress tool-use loop resends the same
# system prompt, the same tool schemas, and a messages list that's only
# grown since the last call - all billed as fresh input tokens with no
# caching, on a loop that can run up to MAX_SANDBOX_ITERATIONS times.
# That's the real driver behind this project's early API spend (see
# cs3ip-budget-crunch memory): marking the stable prefix (system, tools,
# everything before the newest message) as an Anthropic prompt-caching
# breakpoint lets repeated calls bill cached reads at roughly a tenth of
# normal input price instead of full price every turn.
_CACHE_CONTROL: dict[str, str] = {"type": "ephemeral"}

# Cosmetic only - what shows up as a message's `type` in the event log/
# dashboard. The FSM itself only ever looks at `trigger` (returned
# separately in AgentResponse), so a wrong or missing entry here can't
# affect orchestration, only how the message reads in the feed. Triggers
# not listed here (tests_passed, tests_failed, retry_cap_exceeded) are
# always decided deterministically by the orchestrator itself, never by
# an agent turn (see fsm.py / loop.py's _step_awaiting_test).
_MESSAGE_TYPE_FOR_TRIGGER: dict[str, MessageType] = {
    "decomposed": MessageType.HANDOFF,
    "spec_ready": MessageType.HANDOFF,
    "code_submission": MessageType.CODE_SUBMISSION,
    "approved": MessageType.REVIEW_FEEDBACK,
    "changes_requested": MessageType.REVIEW_FEEDBACK,
    "cto_override": MessageType.REVIEW_FEEDBACK,
    "cto_cannot_resolve": MessageType.REVIEW_FEEDBACK,
}

# What each preset role is responsible for, in terms of this system's
# own FSM stages - not generic job-title flavor text. Keyed by
# (role_id, status); a (role_id, None) entry is the fallback used when
# a role's framing doesn't depend on which state it's acting from. A
# custom (non-preset) role, or a state with no entry at all, falls back
# further to a generic description built from RoleConfig.name.
#
# Engineering gets two distinct entries because its two acting states
# genuinely mean different things: Specd is just "accept the ticket",
# In Progress is "now actually do the work" - collapsing them into one
# piece of text (as Layer A did, before sandbox tools existed to give
# In Progress) would leave one of the two states with wrong or
# vacuous instructions.
_ROLE_RESPONSIBILITIES: dict[tuple[str, TicketStatus | None], str] = {
    ("cto", None): (
        "You are the CTO of a small software company. You act at three points in a "
        "ticket's lifecycle: Intake (scope and split a raw requirement by subsystem, "
        "before Product breaks it into tickets), Review (approve a submitted "
        "implementation - your approval IS the merge to main - or send it back with "
        "concrete change requests), and Escalated (an engineer has exhausted their "
        "retry budget on this ticket; either override with new technical direction "
        "and send it back, or judge it unresolvable and halt it)."
    ),
    ("product", None): (
        "You are Product at a small software company. You act at Backlog: take the "
        "CTO's subsystem-level scope and decompose it into one or more concrete "
        "tickets, each with clear acceptance criteria an engineer can implement "
        "against."
    ),
    ("engineering", TicketStatus.SPECD): (
        "You are an Engineer at a small software company. Right now you are "
        "accepting a spec'd ticket - confirm you're taking it on. Writing and "
        "submitting the actual code happens on your next turn, once you have "
        "sandbox tools available."
    ),
    ("engineering", TicketStatus.IN_PROGRESS): (
        "You are an Engineer at a small software company, now actually implementing "
        "this ticket. You have sandbox tools: read_file, write_file, run_command, "
        "run_tests, and git_diff to inspect and iterate, and git_commit to save your "
        "work. run_tests is a self-check only, not your submission - the team's own "
        "authoritative test run happens separately after you submit. You must call "
        "git_commit at least once before you finish. Once your change is committed, "
        f"call {_TOOL_NAME} with trigger code_submission."
    ),
}


def _responsibilities_for(role_config: RoleConfig, status: TicketStatus) -> str:
    return (
        _ROLE_RESPONSIBILITIES.get((role_config.id, status))
        or _ROLE_RESPONSIBILITIES.get((role_config.id, None))
        or f"You are {role_config.name}, one of several agents on a small software team."
    )


def _base_system_prompt(role_config: RoleConfig, status: TicketStatus, valid: list[str]) -> str:
    prompt = (
        f"{_responsibilities_for(role_config, status)}\n\n"
        f"When you are ready to end your turn, call the {_TOOL_NAME} tool exactly "
        "once. Its `trigger` field is constrained to the outcomes actually valid "
        f"from this ticket's current state ({', '.join(valid)}) - pick the one that "
        "reflects what you're deciding. `narrative` is what a teammate reads in a "
        "shared group chat: a sentence or two in plain, everyday language, like "
        "you're telling a coworker what you did and why. Never put code, file "
        "paths, commands, or error output in narrative - anyone who wants that "
        "detail can already see it in your logged tool activity. `handoff_done` / "
        "`handoff_remaining` / `handoff_notes_for_next` are what the *next* agent "
        "to touch this ticket will see instead of the full conversation history - "
        "write them assuming the reader has seen nothing before this point, and in "
        "the same plain style as narrative."
    )
    if role_config.personality:
        prompt += f"\n\nYour working style: {role_config.personality}"
    return prompt


def _tool_schema(triggers: list[str]) -> dict[str, Any]:
    return {
        "name": _TOOL_NAME,
        "description": (
            "Declare this turn's outcome: the FSM trigger you're firing, a short "
            "narrative for the team, and a structured handoff for whoever picks this "
            "ticket up next."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "trigger": {"type": "string", "enum": triggers},
                "narrative": {
                    "type": "string",
                    "description": (
                        "Plain-language status update for a shared team chat - no code, "
                        "paths, commands, or error output."
                    ),
                },
                "handoff_done": {
                    "type": "string",
                    "description": "What's been completed, in plain language.",
                },
                "handoff_remaining": {"type": "string", "description": "What's left, in plain language."},
                "handoff_notes_for_next": {
                    "type": "string",
                    "description": "Anything the next agent needs to know, in plain language.",
                },
            },
            "required": [
                "trigger",
                "narrative",
                "handoff_done",
                "handoff_remaining",
                "handoff_notes_for_next",
            ],
            "additionalProperties": False,
        },
        "strict": True,
    }


# The sandbox tool set an engineering agent gets during In Progress -
# every SandboxController method except run_hidden_tests(), which is
# never exposed to an agent (see SandboxController's docstring: only
# the orchestrator's deterministic awaiting_test step calls it).
_SANDBOX_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "read_file",
        "description": "Read a file's current contents from the sandbox.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "write_file",
        "description": "Create or overwrite a file in the sandbox with the given content.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "run_command",
        "description": "Run a shell command in the sandbox and see its exit code, stdout, and stderr.",
        "input_schema": {
            "type": "object",
            "properties": {"cmd": {"type": "string"}},
            "required": ["cmd"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "run_tests",
        "description": (
            "Run the test suite as a self-check. This does not submit your work - it "
            "just lets you see whether you're on track before committing."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        "strict": True,
    },
    {
        "name": "git_diff",
        "description": "See the current uncommitted diff in the sandbox.",
        "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        "strict": True,
    },
    {
        "name": "git_commit",
        "description": "Commit and push your current changes. Call this once your implementation is ready.",
        "input_schema": {
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]


def _truncate(text: str) -> str:
    if len(text) <= _MAX_TOOL_RESULT_CHARS:
        return text
    return text[:_MAX_TOOL_RESULT_CHARS] + f"\n... [truncated, {len(text)} chars total]"


def _execute_sandbox_tool(sandbox: SandboxController, name: str, args: dict) -> tuple[str, bool]:
    """Runs one sandbox tool call for real. Returns (result_text, is_error).

    is_error reflects the TOOL CALL failing (a bad path, a git failure),
    not the semantic outcome of what it ran - a non-zero exit code from
    run_command/run_tests is a legitimate, expected result an agent
    needs to see and react to, not a harness-level error (same
    distinction DockerAttemptSandbox's own _exec vs _exec_checked
    draws internally).
    """
    try:
        if name == "read_file":
            return _truncate(sandbox.read_file(args["path"])), False
        if name == "write_file":
            sandbox.write_file(args["path"], args["content"])
            return f"wrote {len(args['content'])} bytes to {args['path']}", False
        if name == "run_command":
            result = sandbox.run_command(args["cmd"])
            text = f"exit code {result.exit_code}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            return _truncate(text), False
        if name == "run_tests":
            result = sandbox.run_tests()
            return _truncate(f"{'passed' if result.passed else 'failed'}\n{result.output}"), False
        if name == "git_diff":
            return _truncate(sandbox.git_diff()), False
        if name == "git_commit":
            sha = sandbox.git_commit(args["message"])
            return f"committed {sha}", False
        return f"unknown tool {name!r}", True
    except Exception as exc:  # noqa: BLE001 - deliberately broad, see docstring
        return f"{type(exc).__name__}: {exc}", True


def _user_turn(context: AgentContext) -> str:
    ticket = context.ticket
    lines = [f"Ticket {ticket.id}: {ticket.title}", f"Description: {ticket.description}"]
    if ticket.acceptance_criteria:
        lines.append("Acceptance criteria:")
        lines.extend(f"- {c}" for c in ticket.acceptance_criteria)
    if context.handoff is not None:
        lines += [
            "",
            "Handoff from the previous agent:",
            f"Done: {context.handoff.done}",
            f"Remaining: {context.handoff.remaining}",
            f"Notes: {context.handoff.notes_for_next}",
        ]
    return "\n".join(lines)


def _cached_system(text: str) -> list[dict[str, Any]]:
    """Wraps a system prompt as a single cacheable content block.

    A plain string `system` can't carry a cache breakpoint - the API
    needs the block form for that. The prompt is identical across every
    call for a given role/status (single-turn stages) and across every
    iteration of one In Progress turn, so marking it here is a pure win.
    """
    return [{"type": "text", "text": text, "cache_control": _CACHE_CONTROL}]


def _cached_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Returns tools with a cache breakpoint on the last definition.

    Anthropic caches everything up to and including a marked block, so
    marking only the last tool caches the whole tools array. Returns a
    new list; doesn't mutate the shared schema dicts callers pass in.
    """
    *rest, last = tools
    return [*rest, {**last, "cache_control": _CACHE_CONTROL}]


def _cached_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Returns messages with a cache breakpoint on the last content block.

    Anthropic caches against the longest prefix matching a previous
    call's breakpoints, so moving this marker onto the newest last block
    before every call caches everything the model has already seen in
    this turn's tool-use loop, leaving only the newest content to bill
    at full price - this is what actually caps the loop's otherwise
    roughly quadratic cost. Returns a copy rather than mutating
    `messages` in place: leaving old markers behind would accumulate
    past Anthropic's 4-breakpoint-per-request limit over a long loop.
    """
    if not messages:
        return messages
    *rest, last_message = messages
    content = last_message["content"]
    if isinstance(content, str):
        new_content: Any = [{"type": "text", "text": content, "cache_control": _CACHE_CONTROL}]
    else:
        *content_rest, last_block = content
        block_dict = last_block if isinstance(last_block, dict) else last_block.model_dump()
        new_content = [*content_rest, {**block_dict, "cache_control": _CACHE_CONTROL}]
    return [*rest, {**last_message, "content": new_content}]


class ClaudeAgent:
    """A real Claude-backed Agent (see orchestrator/agent.py's Protocol)
    covering every FSM stage: Intake, Backlog, Specd, Review, Escalated
    with a single forced tool call, and In Progress with a real
    multi-turn tool-use loop against AgentContext.sandbox.

    Trigger mechanism confirmed 2026-09-25 (cs3ip-fyp-overview memory):
    a forced tool call whose schema enumerates exactly the triggers
    valid_triggers() says are legal from the ticket's current status.
    The model cannot free-text a trigger, so there is no
    retry-on-malformed-output path to write - `strict: true` on the
    tool definition guarantees the arguments validate exactly against
    the schema before this code ever sees them. In the In Progress
    loop this is the *last* tool call of the turn rather than the only
    one - sandbox tools are also on offer, and the model chooses freely
    between them (tool_choice "auto") until it's ready to end its turn.

    One instance is invoked at more than one FSM state for the same
    role (engineering acts at both Specd and In Progress - see
    fsm.ACTIVE_ROLE_FOR_STATUS), so branching on
    `context.sandbox is not None` (only set during In Progress, per
    AgentContext's own docstring) is what actually selects which of
    the two turn shapes runs, not anything about which Agent instance
    this is.

    `event_log`, if given, is used only to log the intermediate
    tool_call/tool_result messages a sandbox-using turn produces (see
    cs3ip-comm-protocol memory: "sandbox tool calls are logged as
    tool_call/tool_result messages in the same stream" - without this,
    process metrics like message counts would silently undercount real
    tool activity). The turn's single FSM-transitioning message is
    still only returned via AgentResponse, exactly as for every other
    stage - the caller (orchestrator loop.py) appends that one itself,
    so this never double-logs it.
    """

    def __init__(
        self,
        role_config: RoleConfig,
        instance_id: str,
        client: anthropic.Anthropic | None = None,
        event_log: EventLog | None = None,
    ):
        self.role = role_config.id
        self.instance_id = instance_id
        self._role_config = role_config
        self._client = client or anthropic.Anthropic()
        self._event_log = event_log

    def invoke(self, context: AgentContext) -> AgentResponse:
        triggers = valid_triggers(context.ticket.status)
        if not triggers:
            raise ValueError(
                f"no valid triggers from status {context.ticket.status!r} - "
                "was this agent invoked at a state with no outgoing transitions?"
            )

        if context.sandbox is not None:
            return self._invoke_in_progress(context, triggers)
        return self._invoke_single_turn(context, triggers)

    def _invoke_single_turn(self, context: AgentContext, triggers: list[str]) -> AgentResponse:
        response = self._client.messages.create(
            model=self._role_config.model,
            max_tokens=16000,
            system=_cached_system(_base_system_prompt(self._role_config, context.ticket.status, triggers)),
            tools=_cached_tools([_tool_schema(triggers)]),
            tool_choice={"type": "tool", "name": _TOOL_NAME},
            messages=[{"role": "user", "content": _user_turn(context)}],
        )
        args = next(block for block in response.content if block.type == "tool_use").input
        return AgentResponse(
            message=self._build_message(context, args, response.usage.input_tokens, response.usage.output_tokens),
            trigger=args["trigger"],
        )

    def _invoke_in_progress(self, context: AgentContext, triggers: list[str]) -> AgentResponse:
        sandbox = context.sandbox
        assert sandbox is not None  # narrows the type for mypy; invoke() already checked
        system = _cached_system(_base_system_prompt(self._role_config, context.ticket.status, triggers))
        tools = _cached_tools([*_SANDBOX_TOOL_SCHEMAS, _tool_schema(triggers)])
        messages: list[dict[str, Any]] = [{"role": "user", "content": _user_turn(context)}]
        total_prompt = 0
        total_completion = 0
        final_args: dict[str, Any] | None = None
        # Correlates this turn's logged tool_call/tool_result messages
        # (Message.parent_id) back to the one narrative message this turn
        # produces (whose own id is forced to this value below) - lets the
        # dashboard hide raw tool activity by default and reveal it as a
        # "Logs" toggle on the message it belongs to, instead of dumping
        # every read_file/run_command call into the main chat feed.
        turn_id = uuid4()
        # Whether a git_commit call has actually succeeded yet this turn -
        # checked structurally below rather than trusted from the model's
        # own narrative, for the same reason AWAITING_TEST's pass/fail
        # verdict is computed by the orchestrator rather than self-reported
        # (see cs3ip-comm-protocol memory): a real Haiku run during
        # development called declare_outcome with trigger code_submission
        # without ever calling git_commit, despite the system prompt
        # explicitly requiring it - prompt wording alone isn't enough for
        # a load-bearing invariant like "the work was actually committed".
        has_committed = False

        for _ in range(MAX_SANDBOX_ITERATIONS):
            response = self._client.messages.create(
                model=self._role_config.model,
                max_tokens=16000,
                system=system,
                tools=tools,
                messages=_cached_messages(messages),
            )
            total_prompt += response.usage.input_tokens
            total_completion += response.usage.output_tokens
            messages.append({"role": "assistant", "content": response.content})

            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
            declare_block = next((b for b in tool_use_blocks if b.name == _TOOL_NAME), None)
            other_blocks = [b for b in tool_use_blocks if b is not declare_block]

            tool_results = [self._run_and_log_tool(context, sandbox, block, turn_id) for block in other_blocks]
            has_committed = has_committed or any(
                block.name == "git_commit" and not result["is_error"]
                for block, result in zip(other_blocks, tool_results)
            )

            declaring_uncommitted_submission = (
                declare_block is not None
                and declare_block.input.get("trigger") == "code_submission"
                and not has_committed
            )
            if declaring_uncommitted_submission:
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": declare_block.id,
                        "content": "You haven't committed yet. Call git_commit, then declare_outcome again.",
                        "is_error": True,
                    }
                )
                declare_block = None  # rejected - fall through to the normal continue-the-loop path

            if declare_block is not None:
                final_args = declare_block.input
                break

            if not tool_use_blocks:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Continue working with your sandbox tools, or call "
                            f"{_TOOL_NAME} once your implementation is committed."
                        ),
                    }
                )
                continue

            messages.append({"role": "user", "content": tool_results})
        else:
            # Exhausted the iteration budget without a declare_outcome call -
            # force one final turn so this always terminates with a real
            # FSM-transitioning message rather than raising. Deliberately
            # does NOT re-run the has_committed gate above: that gate could
            # itself loop forever if the model kept mishandling git_commit,
            # and "always terminate" wins over "always enforce" here - the
            # flagging below keeps that tradeoff visible rather than silent.
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "You have used all of your available steps for this turn. "
                        f"Call {_TOOL_NAME} now with your best current outcome."
                    ),
                }
            )
            response = self._client.messages.create(
                model=self._role_config.model,
                max_tokens=16000,
                system=system,
                tools=_cached_tools([_tool_schema(triggers)]),
                tool_choice={"type": "tool", "name": _TOOL_NAME},
                messages=_cached_messages(messages),
            )
            total_prompt += response.usage.input_tokens
            total_completion += response.usage.output_tokens
            final_args = next(block for block in response.content if block.type == "tool_use").input

        if final_args.get("trigger") == "code_submission" and not has_committed:
            # Visible, not silent: a code_submission that never actually
            # committed means the Review -> Done "approval IS the merge"
            # mechanic would merge nothing, since git_commit is the only
            # thing that pushes back to the host branch (see
            # cs3ip-comm-protocol memory's git mechanics). Only reachable
            # via the iteration-budget escape hatch above.
            final_args = dict(final_args)
            final_args["narrative"] = f"[submitted without committing] {final_args['narrative']}"

        return AgentResponse(
            message=self._build_message(context, final_args, total_prompt, total_completion, message_id=turn_id),
            trigger=final_args["trigger"],
        )

    def _run_and_log_tool(
        self, context: AgentContext, sandbox: SandboxController, block: Any, turn_id: UUID
    ) -> dict[str, Any]:
        result_text, is_error = _execute_sandbox_tool(sandbox, block.name, block.input)
        if self._event_log is not None:
            sender = AgentRef(role=self.role, instance_id=self.instance_id)
            self._event_log.append_message(
                Message(
                    sender=sender,
                    channel_id=context.channel_id,
                    ticket_ref=context.ticket.id,
                    type=MessageType.TOOL_CALL,
                    parent_id=turn_id,
                    content=MessageContent(text=_truncate(f"{block.name}({block.input})")),
                )
            )
            self._event_log.append_message(
                Message(
                    sender=sender,
                    channel_id=context.channel_id,
                    ticket_ref=context.ticket.id,
                    type=MessageType.TOOL_RESULT,
                    parent_id=turn_id,
                    content=MessageContent(text=f"{'ERROR: ' if is_error else ''}{result_text}"),
                )
            )
        return {"type": "tool_result", "tool_use_id": block.id, "content": result_text, "is_error": is_error}

    def _build_message(
        self,
        context: AgentContext,
        args: dict[str, Any],
        prompt_tokens: int,
        completion_tokens: int,
        message_id: UUID | None = None,
    ) -> Message:
        fields: dict[str, Any] = {}
        if message_id is not None:
            # Lets logged tool_call/tool_result messages (Message.parent_id)
            # correlate back to this exact message - see _invoke_in_progress's
            # turn_id comment. Only the in-progress path passes this; every
            # other stage has no associated tool activity to correlate, so
            # its message keeps its normal randomly-generated id.
            fields["id"] = message_id
        return Message(
            sender=AgentRef(role=self.role, instance_id=self.instance_id),
            channel_id=context.channel_id,
            type=_MESSAGE_TYPE_FOR_TRIGGER.get(args["trigger"], MessageType.STATUS_UPDATE),
            ticket_ref=context.ticket.id,
            content=MessageContent(
                text=args["narrative"],
                handoff=HandoffNote(
                    done=args["handoff_done"],
                    remaining=args["handoff_remaining"],
                    notes_for_next=args["handoff_notes_for_next"],
                ),
            ),
            token_cost=TokenCost(prompt=prompt_tokens, completion=completion_tokens),
            **fields,
        )
