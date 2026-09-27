from __future__ import annotations

from typing import Any

import anthropic

from wjfyp.models.agent import RoleConfig
from wjfyp.models.message import AgentRef, HandoffNote, Message, MessageContent, MessageType, TokenCost
from wjfyp.orchestrator.agent import AgentContext, AgentResponse
from wjfyp.orchestrator.fsm import valid_triggers

_TOOL_NAME = "declare_outcome"

# Cosmetic only - what shows up as a message's `type` in the event log/
# dashboard. The FSM itself only ever looks at `trigger` (returned
# separately in AgentResponse), so a wrong or missing entry here can't
# affect orchestration, only how the message reads in the feed. Triggers
# not listed here (code_submission, tests_passed, tests_failed,
# retry_cap_exceeded) are never declared by this agent - code_submission
# is a later capability (FYP-25 Layer B, sandbox tool access), the other
# three are always decided deterministically by the orchestrator itself,
# never by an agent turn (see fsm.py / loop.py's _step_awaiting_test).
_MESSAGE_TYPE_FOR_TRIGGER: dict[str, MessageType] = {
    "decomposed": MessageType.HANDOFF,
    "spec_ready": MessageType.HANDOFF,
    "approved": MessageType.REVIEW_FEEDBACK,
    "changes_requested": MessageType.REVIEW_FEEDBACK,
    "cto_override": MessageType.REVIEW_FEEDBACK,
    "cto_cannot_resolve": MessageType.REVIEW_FEEDBACK,
}

# What each preset role is responsible for, in terms of this system's
# own FSM stages - not generic job-title flavor text. A custom
# (non-preset) role has no fixed FSM slot of its own yet, so it falls
# back to a generic description built from RoleConfig.name.
_ROLE_RESPONSIBILITIES: dict[str, str] = {
    "cto": (
        "You are the CTO of a small software company. You act at three points in a "
        "ticket's lifecycle: Intake (scope and split a raw requirement by subsystem, "
        "before Product breaks it into tickets), Review (approve a submitted "
        "implementation - your approval IS the merge to main - or send it back with "
        "concrete change requests), and Escalated (an engineer has exhausted their "
        "retry budget on this ticket; either override with new technical direction "
        "and send it back, or judge it unresolvable and halt it)."
    ),
    "product": (
        "You are Product at a small software company. You act at Backlog: take the "
        "CTO's subsystem-level scope and decompose it into one or more concrete "
        "tickets, each with clear acceptance criteria an engineer can implement "
        "against."
    ),
    "engineering": (
        "You are an Engineer at a small software company. Right now you are "
        "accepting a spec'd ticket (Specd -> In Progress) - confirm you're taking it "
        "on. Writing and submitting the actual code happens in a later turn, once "
        "sandbox tools are available to you."
    ),
}


def _base_system_prompt(role_config: RoleConfig, valid: list[str]) -> str:
    responsibilities = _ROLE_RESPONSIBILITIES.get(
        role_config.id,
        f"You are {role_config.name}, one of several agents on a small software team.",
    )
    prompt = (
        f"{responsibilities}\n\n"
        f"Every turn, call the {_TOOL_NAME} tool exactly once. Its `trigger` field is "
        "constrained to the outcomes actually valid from this ticket's current state "
        f"({', '.join(valid)}) - pick the one that reflects what you're deciding. "
        "`narrative` is a short message visible to the rest of the team in the shared "
        "log. `handoff_done` / `handoff_remaining` / `handoff_notes_for_next` are what "
        "the *next* agent to touch this ticket will see instead of the full "
        "conversation history - write them assuming the reader has seen nothing "
        "before this point."
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
                    "description": "Short status update, visible in the team's shared log.",
                },
                "handoff_done": {"type": "string", "description": "What's been completed."},
                "handoff_remaining": {"type": "string", "description": "What's left."},
                "handoff_notes_for_next": {
                    "type": "string",
                    "description": "Anything the next agent needs to know.",
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


class ClaudeAgent:
    """A real Claude-backed Agent (see orchestrator/agent.py's Protocol)
    for every FSM stage that doesn't need sandbox tool access - Intake,
    Backlog, Specd, Review, Escalated. In Progress (an engineer actually
    writing and submitting code via the sandbox) is deliberately out of
    scope here - that needs a real multi-turn tool-use loop against
    AgentContext.sandbox, a separate build (FYP-25 Layer B).

    Trigger mechanism confirmed 2026-09-25 (cs3ip-fyp-overview memory):
    a forced tool call whose schema enumerates exactly the triggers
    valid_triggers() says are legal from the ticket's current status.
    The model cannot free-text a trigger, so there is no
    retry-on-malformed-output path to write - `strict: true` on the
    tool definition guarantees the arguments validate exactly against
    the schema before this code ever sees them.
    """

    def __init__(
        self,
        role_config: RoleConfig,
        instance_id: str,
        client: anthropic.Anthropic | None = None,
    ):
        self.role = role_config.id
        self.instance_id = instance_id
        self._role_config = role_config
        self._client = client or anthropic.Anthropic()

    def invoke(self, context: AgentContext) -> AgentResponse:
        triggers = valid_triggers(context.ticket.status)
        if not triggers:
            raise ValueError(
                f"no valid triggers from status {context.ticket.status!r} - "
                "was this agent invoked at a state with no outgoing transitions?"
            )

        response = self._client.messages.create(
            model=self._role_config.model,
            max_tokens=16000,
            system=_base_system_prompt(self._role_config, triggers),
            tools=[_tool_schema(triggers)],
            tool_choice={"type": "tool", "name": _TOOL_NAME},
            messages=[{"role": "user", "content": _user_turn(context)}],
        )

        tool_use = next(block for block in response.content if block.type == "tool_use")
        args = tool_use.input

        message = Message(
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
            token_cost=TokenCost(
                prompt=response.usage.input_tokens,
                completion=response.usage.output_tokens,
            ),
        )
        return AgentResponse(message=message, trigger=args["trigger"])
