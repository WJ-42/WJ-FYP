"""An Agent backed by any OpenAI-compatible chat-completions endpoint.

Sits alongside ClaudeAgent rather than replacing it: `Agent` in
orchestrator/agent.py is a Protocol, so the orchestrator loop and the
FSM need no changes to run a team on open-weight models instead of
Claude. Which backend a run uses is a config choice (see
config/roles_groq.yaml), not a code change.

Added for FYP-60, after Ziyang's 22/10 feedback that agent runs should
be on open models rather than paid out of pocket. Groq is the first
provider wired up, but nothing here is Groq-specific - `base_url` and
`model` both come from config, so Cerebras, OpenRouter, or a local
Ollama/vLLM server on another machine work through the same code path.

**Prompt text is deliberately imported from claude_agent rather than
copied.** The hierarchical-vs-flat comparison is only valid if both
backends send byte-identical system prompts, user turns and tool
schemas; a copy would drift the moment either side was edited, and the
drift would silently confound the results. The private imports below
are that single-source-of-truth guarantee, not an oversight. The only
things this module defines for itself are the ones the two wire formats
genuinely disagree about.

Where the two APIs actually differ, and what this does about it:

* Tool schemas. Anthropic takes {name, description, input_schema};
  OpenAI takes {type: function, function: {name, description,
  parameters}}. `_to_openai_tool` converts, so the schemas stay defined
  once in claude_agent.
* The system prompt is a first message here, not a separate argument.
* Tool arguments arrive as a JSON *string* to parse, not a dict.
* Tool results are one `{"role": "tool"}` message per call, not a single
  user turn carrying a list of result blocks. Every tool call in an
  assistant message must get a reply or the next request is rejected,
  which is why the uncommitted-submission rejection below answers the
  declare call too rather than just dropping it.
* There is no `is_error` flag on a tool result, so an error is marked in
  the result text instead.
* Usage fields are prompt_tokens/completion_tokens, not
  input_tokens/output_tokens. `_UsageShim` adapts them so SpendGuard
  keeps its single Anthropic-shaped contract.
* No prompt caching. Anthropic's cache_control breakpoints are what keep
  the In Progress loop's cost near-linear; without them the loop is
  quadratic in tokens again. That is a real cost regression against
  Claude and is the main reason the rate limits below bite.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
from openai import BadRequestError, OpenAI

from wjfyp.config import DEFAULT_ROLES_PATH, load_roles
from wjfyp.eventlog import EventLog
from wjfyp.models.agent import RoleConfig
from wjfyp.models.message import AgentRef, HandoffNote, Message, MessageContent, MessageType, TokenCost
from wjfyp.orchestrator.agent import Agent, AgentContext, AgentResponse
from wjfyp.orchestrator.budget import SpendGuard
from wjfyp.orchestrator.claude_agent import (
    _MESSAGE_TYPE_FOR_TRIGGER,
    _SANDBOX_TOOL_SCHEMAS,
    _TOOL_NAME,
    MAX_SANDBOX_ITERATIONS,
    _base_system_prompt,
    _execute_sandbox_tool,
    _tool_schema,
    _truncate,
    _user_turn,
)
from wjfyp.orchestrator.fsm import agent_facing_triggers
from wjfyp.orchestrator.verification import accept_under_protest, verify_declaration
from wjfyp.sandbox.controller import SandboxController

DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"

# ClaudeAgent asks for 16000 output tokens, which is fine against
# Anthropic's limits. It is not fine here: Groq's free tier allows 8,000
# tokens per MINUTE on every chat model (measured from the console on
# 03/11/2026, not from the docs - the docs' figures were wrong about
# which models exist at all). Providers that count max_tokens toward
# that budget would reject a 16000-token request outright. declare_outcome
# only ever emits a few hundred tokens, so this is generous already.
MAX_OUTPUT_TOKENS = 4096

# 8,000 tokens a minute means a real In Progress loop WILL hit 429s
# rather than might. The SDK retries 429s with backoff on its own, so
# raising this past the default of 2 is what turns "the run died" into
# "the run was slow", which is the right trade for an unattended
# evaluation batch.
MAX_RETRIES = 6

# A forced tool choice is a GUARANTEE on some endpoints and a HINT on
# others, and the difference only shows up under repetition.
#
# Anthropic and Groq both honour tool_choice absolutely: name a tool and
# the response contains a call to it. Ollama passes the choice to the
# chat template as a suggestion, so the model can finish its reasoning
# and then simply decline. Measured 03/11/2026 against a self-hosted
# server on fifty IDENTICAL forced calls: qwen3:4b refused 3 of 50
# (6.0%, 95% Wilson CI 2.1-16.2%), and qwen3:8b 1 of 10, so this is the
# endpoint and not the model size.
#
# What it is NOT, in both cases:
#   - truncation. finish_reason was "stop", never "length".
#   - a reasoning-length effect. The 4B refusals had 721-848 completion
#     tokens against a 247-1649 range overall, and the one 8B refusal
#     had 325, its SHORTEST response of the ten.
# Fifty identical prompts with three refusals is also the experiment for
# whether a refusal is a property of the prompt, and it is not: the same
# input succeeded 47 times. So resampling genuinely resamples, rather
# than retrying something that was always going to fail.
#
# A 6% refusal rate matters because an arm makes hundreds of forced
# calls, so the question is not whether this happens during a run but
# how early. A non-response is a MISSING MEASUREMENT, not a decision to
# record: there is no trigger to transition on and no narrative to
# score, so the only two options are resample or lose the run.
#
# 3 resamples (4 attempts) is where the pessimistic end of that CI stops
# mattering. Expected hard failures across a 500-forced-call run, at the
# point estimate and at the CI upper bound:
#   2 attempts   1.80   13.15
#   3 attempts   0.11    2.13
#   4 attempts   0.006   0.35   <- first budget under 1 even pessimistically
#   5 attempts   0.000   0.06
# A fifth attempt buys little and each one costs a full generation.
#
# Every discarded attempt is logged (see _log_resample), because an
# unlogged resample would hide a real difference between arms in how
# reliably they follow a forced schema under a given prompt, and that
# rate is a result in its own right.
MAX_EMPTY_RESAMPLES = 3


class ContextWindowExceeded(RuntimeError):
    """The server silently dropped the front of the prompt.

    Measured against a self-hosted Ollama server on 03/11/2026. /api/ps
    reported a 4096-token window for qwen3:8b (the model itself
    supports 40,960; 4096 is just Ollama's default). Sending more than
    that does NOT fail:

        sent ~6,000 tokens  -> 200 OK, usage.prompt_tokens = 2,050
        sent ~12,000 tokens -> 200 OK, usage.prompt_tokens = 2,050

    With a marker planted at each end of the prompt, the model could
    quote the one at the BACK and not the one at the FRONT. The window
    slides and the oldest tokens go.

    This is worse than the 413 Request too large that Groq returned for
    the same overflow. A 413 is a refusal: loud, survivable, and
    impossible to mistake for a result. This is a 200 with a plausible
    answer computed from a prompt that no longer contains the system
    prompt or the role persona - which, for a project whose independent
    variable IS the persona, means silently measuring nothing while
    producing data that looks fine.

    So it is raised rather than worked around. A run that loses its
    persona should stop, not continue and be written up.
    """

# Raising max_retries without also lowering the timeout was a mistake
# that cost a real run. The SDK's default read timeout is 600 seconds,
# so a request that hangs rather than failing blocks for ten minutes
# before the first retry, and six retries makes that an hour of silence
# on a single call. A pilot run on 03/11/2026 made three sandbox tool
# calls in two seconds and then sat completely still for ten minutes,
# which looked exactly like rate limiting and was not.
#
# 90 seconds is well past anything this workload legitimately needs: the
# slowest real call measured was a 13,500-token In Progress turn at
# around 70 seconds, and a single forced tool call returns in about one.
# Past that the request is hung rather than slow, and failing fast to
# retry beats waiting. Connect stays short because a connection that
# cannot be established is never worth waiting on.
REQUEST_TIMEOUT = httpx.Timeout(90.0, connect=10.0)


class _UsageShim:
    """Presents OpenAI-shaped usage under the Anthropic field names
    SpendGuard.record() expects.

    SpendGuard is a money failsafe shared with ClaudeAgent, and teaching
    it two usage dialects would mean two code paths in the one place
    that must not get this wrong. Adapting at the edge keeps it with a
    single contract. The cache fields it also reads are absent here,
    which is correct rather than a gap: there is no prompt caching on
    this path, so there are no cached tokens to price.
    """

    __slots__ = ("input_tokens", "output_tokens")

    def __init__(self, usage: Any):
        self.input_tokens = getattr(usage, "prompt_tokens", 0) or 0
        self.output_tokens = getattr(usage, "completion_tokens", 0) or 0


def _to_openai_tool(schema: dict[str, Any]) -> dict[str, Any]:
    """Converts one claude_agent tool schema into OpenAI's wire format.

    Keeps the descriptions and JSON schemas defined exactly once, in
    claude_agent, so the two backends can never drift apart on what a
    tool is or what it accepts.
    """
    parameters = dict(schema["input_schema"])
    # run_tests and git_diff take no arguments, so their schemas carry an
    # empty `properties` next to an empty `required`. Anthropic accepts
    # that; Groq rejects the whole request with "'required' present but
    # 'properties' is missing", reading an empty properties object as
    # absent. Dropping the now-meaningless `required` is the portable
    # spelling and changes nothing about what the tool accepts.
    if not parameters.get("properties"):
        parameters.pop("required", None)
    return {
        "type": "function",
        "function": {
            "name": schema["name"],
            "description": schema["description"],
            "parameters": parameters,
            "strict": schema.get("strict", False),
        },
    }


def _assistant_message(message: Any, keep_call_ids: set[str] | None = None) -> dict[str, Any]:
    """Rebuilds an assistant turn for replay, field by field.

    Deliberately not model_dump(): gpt-oss models return a `reasoning`
    field alongside content, and replaying provider-specific extras is
    the kind of thing that works on one endpoint and 400s on the next.
    Only the parts the protocol actually defines go back.

    `keep_call_ids`, when given, filters which tool calls are replayed.
    See _invoke_in_progress's handling of hallucinated tool names for
    why a call sometimes has to be left out of history entirely.
    """
    out: dict[str, Any] = {"role": "assistant"}
    if message.content:
        out["content"] = message.content
    calls = [
        call
        for call in (message.tool_calls or [])
        if keep_call_ids is None or call.id in keep_call_ids
    ]
    if calls:
        out["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.function.name, "arguments": call.function.arguments},
            }
            for call in calls
        ]
    return out


def _parse_arguments(call: Any) -> dict[str, Any] | None:
    """Parses a tool call's arguments, or returns None if unparsable.

    `strict: true` is supposed to guarantee these validate before we see
    them, and on the three models probed on 03/11/2026 it did. Open
    models vary more than Claude does here, so a malformed payload is
    treated as a turn that simply did not declare an outcome - the loop
    nudges and carries on - rather than as a crash that loses the run.
    """
    try:
        parsed = json.loads(call.function.arguments)
    except (json.JSONDecodeError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


_JSON_TYPES: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _conforms(parameters: dict[str, Any], args: dict[str, Any]) -> bool:
    """Checks a tool call's arguments against its declared schema.

    Needed because this endpoint validates the arguments of tool calls
    replayed in history, not just newly generated ones, and rejects the
    whole request when they do not match ("parameters for tool
    run_command did not match schema: `/cmd`: expected string, but got
    array" - a real gpt-oss-20b call). A call that fails this is handled
    exactly like an invented tool name: kept out of history, corrected
    in plain language.

    Deliberately shallow rather than a real JSON Schema validator. Every
    schema in _SANDBOX_TOOL_SCHEMAS and _tool_schema is a flat object of
    primitives, so presence of the required keys plus a type check on
    each declared property is the whole of what those schemas express.
    A dependency on jsonschema to re-derive that would be a worse trade.
    """
    properties = parameters.get("properties") or {}
    for name in parameters.get("required") or []:
        if name not in args:
            return False
    for name, value in args.items():
        declared = properties.get(name, {}).get("type")
        expected = _JSON_TYPES.get(declared) if declared else None
        if expected is not None and not isinstance(value, expected):
            return False
    return True


def build_agent_pool(
    roles_path: Path = DEFAULT_ROLES_PATH,
    event_log: EventLog | None = None,
    spend_guard: SpendGuard | None = None,
    api_key: str | None = None,
    base_url: str = DEFAULT_BASE_URL,
    model: str | None = None,
) -> dict[str, list[Agent]]:
    """The open-model counterpart to claude_agent.build_agent_pool, with
    the same signature plus the two provider arguments, so a caller can
    swap one for the other and change nothing else.

    One client is shared by every agent in the pool. The SDK's client is
    thread-safe and pools connections, and sharing it means the retry
    budget and connection reuse are shared too.

    `model`, if given, replaces the model on EVERY role, leaving tiers,
    counts and personas exactly as the file wrote them. That is what
    keeps one set of arm configs usable on any backend instead of a
    duplicate set per provider: six arms times two backends would be
    twelve near-identical files whose persona text has to stay
    byte-identical in pairs, and the only reliable way to keep two
    copies identical is to have one copy.

    Overriding every role with the same model deliberately COLLAPSES the
    capability ladder. For the persona arms that is correct and in fact
    desirable, since they hold models constant by design and vary only
    the prompt. For an arm whose whole point is differing capability per
    tier, leave this unset and let the file say what it means.
    """
    client = OpenAI(
        api_key=api_key, base_url=base_url, max_retries=MAX_RETRIES, timeout=REQUEST_TIMEOUT
    )
    roles = load_roles(roles_path)
    if model is not None:
        roles = [r.model_copy(update={"model": model}) for r in roles]
    return {
        role.id: [
            OpenAIAgent(
                role,
                f"{role.id}-{i + 1}",
                client=client,
                event_log=event_log,
                spend_guard=spend_guard,
            )
            for i in range(role.count)
        ]
        for role in roles
    }


class OpenAIAgent:
    """An Agent (see orchestrator/agent.py's Protocol) backed by an
    OpenAI-compatible endpoint, covering the same FSM stages ClaudeAgent
    does and with the same two turn shapes: a single forced tool call
    for Intake/Backlog/Specd/Review/Escalated, and a multi-turn tool-use
    loop against AgentContext.sandbox for In Progress.

    The structural guarantees ClaudeAgent makes are all preserved here,
    because they are what make the comparison between backends mean
    anything:

    * The trigger is a forced tool call whose schema enumerates exactly
      the triggers legal from this ticket's current status, so a model
      cannot free-text an invalid transition.
    * A code_submission declared without a successful git_commit is
      rejected and sent back, checked structurally rather than trusted
      from the model's narrative (FYP-31, and see claude_agent's own
      note on the real Haiku run that motivated it).
    * The loop always terminates with a real FSM-transitioning message,
      via the same forced final turn when the iteration budget runs out.
    """

    def __init__(
        self,
        role_config: RoleConfig,
        instance_id: str,
        client: OpenAI,
        event_log: EventLog | None = None,
        spend_guard: SpendGuard | None = None,
    ):
        self.role = role_config.id
        self.instance_id = instance_id
        self._role_config = role_config
        self._client = client
        self._event_log = event_log
        self._spend_guard = spend_guard

    def _create(self, **kwargs: Any) -> Any:
        """Every real API call goes through here, never
        self._client.chat.completions.create directly - same contract as
        ClaudeAgent._create: check() before, so a call already known to
        exceed the cap never starts, and record() after with the real
        usage. On a free tier the recorded cost is near zero, but the
        guard still has to be wired or moving to a paid plan would
        silently remove the failsafe.
        """
        if self._spend_guard is not None:
            self._spend_guard.check()
        response = self._client.chat.completions.create(**kwargs)
        if self._spend_guard is not None and response.usage is not None:
            self._spend_guard.record(self._role_config.model, _UsageShim(response.usage))
        return response

    def invoke(self, context: AgentContext) -> AgentResponse:
        triggers = agent_facing_triggers(context.ticket.status)
        if not triggers:
            raise ValueError(
                f"no valid triggers from status {context.ticket.status!r} - "
                "was this agent invoked at a state with no outgoing transitions?"
            )

        if context.sandbox is not None:
            return self._invoke_in_progress(context, triggers)
        return self._invoke_single_turn(context, triggers)

    def _messages(self, context: AgentContext, triggers: list[str]) -> list[dict[str, Any]]:
        return [
            {
                "role": "system",
                "content": _base_system_prompt(self._role_config, context.ticket.status, triggers),
            },
            {"role": "user", "content": _user_turn(context)},
        ]

    def _invoke_single_turn(self, context: AgentContext, triggers: list[str]) -> AgentResponse:
        """One forced declare_outcome call, resampled if the endpoint
        returns nothing (see MAX_EMPTY_RESAMPLES on why that happens).

        Tokens from the empty attempts are added to the turn's recorded
        cost. They were really spent, and a resample that cost nothing
        on paper would understate the price of running an arm on a
        backend that needs resampling more often.
        """
        prompt_tokens = 0
        completion_tokens = 0
        reason = "no attempts were made"
        schema = _tool_schema(triggers)

        for attempt in range(1 + MAX_EMPTY_RESAMPLES):
            response = self._create(
                model=self._role_config.model,
                max_tokens=MAX_OUTPUT_TOKENS,
                messages=self._messages(context, triggers),
                tools=[_to_openai_tool(schema)],
                tool_choice={"type": "function", "function": {"name": _TOOL_NAME}},
            )
            usage = response.usage
            prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
            completion_tokens += getattr(usage, "completion_tokens", 0) or 0

            calls = [
                c
                for c in (response.choices[0].message.tool_calls or [])
                if c.function.name == _TOOL_NAME
            ]
            if not calls:
                reason = (
                    f"returned no {_TOOL_NAME!r} call despite tool_choice forcing it "
                    f"(finish_reason={response.choices[0].finish_reason!r})"
                )
            else:
                args = _parse_arguments(calls[0])
                if args is None:
                    reason = (
                        f"returned unparsable arguments: "
                        f"{calls[0].function.arguments[:200]!r}"
                    )
                elif not _conforms(schema["input_schema"], args):
                    # Five fields are required, and a hint-only endpoint
                    # constrains generation to none of them. Without
                    # this check a missing handoff field surfaces as a
                    # KeyError from _build_message, several frames from
                    # the cause, instead of as one more resample.
                    reason = (
                        f"returned a call missing required fields or with wrong types: "
                        f"{sorted(args)}"
                    )
                elif args.get("trigger") not in triggers:
                    # The schema enumerates the legal triggers, but a
                    # hint-only endpoint does not constrain generation
                    # to it either, so this has to be checked rather
                    # than assumed. An out-of-enum trigger would be an
                    # illegal FSM transition.
                    reason = (
                        f"declared trigger {args.get('trigger')!r}, which is not one of "
                        f"{triggers} for status {context.ticket.status.value!r}"
                    )
                else:
                    return AgentResponse(
                        message=self._build_message(
                            context, args, prompt_tokens, completion_tokens
                        ),
                        trigger=args["trigger"],
                    )

            self._log_resample(context, attempt, reason)

        raise ValueError(
            f"{self._role_config.model} failed to produce a usable {_TOOL_NAME!r} call in "
            f"{1 + MAX_EMPTY_RESAMPLES} attempts at status "
            f"{context.ticket.status.value!r}; last attempt {reason}"
        )

    def _log_resample(self, context: AgentContext, attempt: int, reason: str) -> None:
        """Records a discarded attempt in the event log.

        Without this the resample is invisible and the run looks clean,
        which would be a quiet lie about how the data was obtained. A
        later analysis can count these per arm straight out of the log,
        and the rate is itself a measure of how reliably a model follows
        a forced schema under a given prompt.

        Every discard is logged, including the last one before this
        gives up, which is why the text says "discarded attempt N/M"
        rather than "resampled": attempt M is not followed by another
        sample, it is followed by the raise.
        """
        if self._event_log is None:
            return
        self._event_log.append_message(
            Message(
                sender=AgentRef(role=self.role, instance_id=self.instance_id),
                channel_id=context.channel_id,
                ticket_ref=context.ticket.id,
                type=MessageType.SYSTEM,
                content=MessageContent(
                    text=_truncate(
                        f"discarded attempt {attempt + 1}/{1 + MAX_EMPTY_RESAMPLES} of forced "
                        f"{_TOOL_NAME}: {self._role_config.model} {reason}"
                    )
                ),
            )
        )

    def _check_context_held(
        self, prompt_tokens: int, last_prompt_tokens: int, appended_chars: int
    ) -> None:
        """Detects a slid context window from the usage numbers alone.

        Checking the reported prompt size against a ceiling would mean
        knowing the ceiling, and nothing in the OpenAI-compatible API
        reports it - /api/ps is Ollama-specific and vLLM has its own.
        So this uses the one invariant that holds on every backend: this
        loop only ever APPENDS to `messages`, so the prompt can only
        grow. If a few thousand characters go in and the reported prompt
        barely moves, the front went out as the back went in.

        The threshold is deliberately slack. English runs about 4 to 5
        characters per token, so dividing by 8 is roughly half the
        growth the smallest plausible tokenizer would produce - far
        below any honest tokenizer difference, and far above the ~0
        growth seen at a real ceiling. `appended_chars` has a floor too,
        so a one-word append cannot trip it.
        """
        if last_prompt_tokens <= 0 or appended_chars < 400:
            return
        growth = prompt_tokens - last_prompt_tokens
        if growth >= appended_chars / 8:
            return
        raise ContextWindowExceeded(
            f"{self._role_config.model} appears to have dropped the front of the prompt: "
            f"{appended_chars:,} characters were appended to the transcript but the reported "
            f"prompt only moved from {last_prompt_tokens:,} to {prompt_tokens:,} tokens. "
            "The server's context window is too small for this turn and it is truncating "
            "silently rather than refusing. Raise it (for Ollama, set OLLAMA_CONTEXT_LENGTH "
            "on the server and restart it) or run a model whose window fits the transcript."
        )

    def _invoke_in_progress(self, context: AgentContext, triggers: list[str]) -> AgentResponse:
        sandbox = context.sandbox
        assert sandbox is not None  # narrows the type; invoke() already checked
        tools = [_to_openai_tool(s) for s in [*_SANDBOX_TOOL_SCHEMAS, _tool_schema(triggers)]]
        parameters_by_tool = {t["function"]["name"]: t["function"]["parameters"] for t in tools}
        valid_tool_names = set(parameters_by_tool)
        messages = self._messages(context, triggers)
        total_prompt = 0
        total_completion = 0
        final_args: dict[str, Any] | None = None
        # Correlates this turn's logged tool_call/tool_result messages
        # back to the one narrative message the turn produces - same
        # mechanism as ClaudeAgent, so the dashboard's "Logs" toggle
        # works identically whichever backend produced the run.
        turn_id = uuid4()
        has_committed = False
        # For _check_context_held. The transcript only ever grows here,
        # so a prompt that stops growing with it has been truncated.
        last_prompt_tokens = 0
        last_sent_chars = 0
        # What the turn actually did, for the forced final turn below,
        # which cannot be shown the real history. One line per tool call.
        tool_log: list[str] = []

        for _ in range(MAX_SANDBOX_ITERATIONS):
            sent_chars = len(json.dumps(messages, default=str))
            try:
                response = self._create(
                    model=self._role_config.model,
                    max_tokens=MAX_OUTPUT_TOKENS,
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",
                )
            except BadRequestError as exc:
                # Groq validates the tool call the model GENERATES, not
                # just the ones replayed from history, and fails the
                # whole request rather than returning the bad call. So an
                # open model inventing `run_file`, or passing an array
                # where `cmd` wants a string, surfaces here as a 400 and
                # never reaches the filtering below - all three were
                # observed from gpt-oss-20b on one ticket.
                #
                # Anthropic has no equivalent: a malformed call comes
                # back as content and ClaudeAgent just carries on, which
                # is why none of this exists there. Treated as a
                # recoverable turn rather than a crashed run: say what
                # was wrong, re-state the real tools, and spend an
                # iteration retrying. A model that never recovers ends
                # the turn through the normal iteration budget below.
                reason = str(exc).split(" - ", 1)[-1][:300]
                tool_log.append("rejected tool call")
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"Your last tool call was rejected: {reason}\n"
                            f"The tools you have are: {', '.join(sorted(valid_tool_names))}. "
                            "Check the argument names and types, then try again."
                        ),
                    }
                )
                continue
            usage = response.usage
            prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
            total_prompt += prompt_tokens
            total_completion += getattr(usage, "completion_tokens", 0) or 0
            self._check_context_held(
                prompt_tokens, last_prompt_tokens, sent_chars - last_sent_chars
            )
            last_prompt_tokens = prompt_tokens
            last_sent_chars = sent_chars

            message = response.choices[0].message
            all_calls = list(message.tool_calls or [])

            # A second line of defence, for providers that do NOT
            # validate what the model generates. Groq rejects an invented
            # tool name or a mistyped argument at the API boundary, so
            # those arrive as the BadRequestError handled above and never
            # get this far. A local Ollama or vLLM server has no such
            # check and will hand back whatever the model produced, so
            # this module cannot assume the endpoint is policing it.
            #
            # Calls that are unusable are kept out of history rather than
            # answered as errors, because replaying a call naming a tool
            # that is not in request.tools would then make every
            # subsequent request in the turn fail on a stricter endpoint.
            # They get a plain-language correction instead.
            parsed: dict[str, dict[str, Any] | None] = {c.id: _parse_arguments(c) for c in all_calls}
            calls = [
                c
                for c in all_calls
                if c.function.name in valid_tool_names
                and parsed[c.id] is not None
                and _conforms(parameters_by_tool[c.function.name], parsed[c.id] or {})
            ]
            rejected = [c for c in all_calls if c not in calls]
            messages.append(_assistant_message(message, keep_call_ids={c.id for c in calls}))

            declare_call = next((c for c in calls if c.function.name == _TOOL_NAME), None)
            other_calls = [c for c in calls if c is not declare_call]

            for call in other_calls:
                args = parsed[call.id] or {}
                result_text, is_error = _execute_sandbox_tool(sandbox, call.function.name, args)
                self._log_tool(context, call.function.name, args, result_text, is_error, turn_id)
                tool_log.append(f"{call.function.name}: {'failed' if is_error else 'ok'}")
                if call.function.name == "git_commit" and not is_error:
                    has_committed = True
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": f"ERROR: {result_text}" if is_error else result_text,
                    }
                )

            if declare_call is not None:
                declared = parsed[declare_call.id] or {}
                # Same shared invariant ClaudeAgent applies - see
                # orchestrator/verification.py. One definition, so the
                # two backends cannot drift on what counts as a valid
                # submission.
                verdict = verify_declaration(
                    declared.get("trigger", ""), has_committed=has_committed
                )
                rejection: str | None = None if verdict else verdict.rejection
                # Every tool call in an assistant message needs a reply
                # before the next request, so a rejected declare is
                # answered rather than dropped - otherwise the follow-up
                # call is rejected by the endpoint for an unanswered id.
                if rejection is not None:
                    messages.append(
                        {"role": "tool", "tool_call_id": declare_call.id, "content": f"ERROR: {rejection}"}
                    )
                else:
                    final_args = declared
                    break

            if rejected:
                unknown = sorted({c.function.name for c in rejected if c.function.name not in valid_tool_names})
                malformed = sorted({c.function.name for c in rejected if c.function.name in valid_tool_names})
                notes = []
                if unknown:
                    notes.append(f"There is no tool called {', '.join(unknown)}.")
                if malformed:
                    notes.append(
                        f"Your arguments to {', '.join(malformed)} did not match that tool's "
                        "schema. Check the required fields and their types."
                    )
                notes.append(f"The tools you have are: {', '.join(sorted(valid_tool_names))}.")
                messages.append({"role": "user", "content": " ".join(notes)})
            elif not calls:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Continue working with your sandbox tools, or call "
                            f"{_TOOL_NAME} once your implementation is committed."
                        ),
                    }
                )
        else:
            # Iteration budget exhausted without a usable declare_outcome.
            # Force one final turn so this always terminates with a real
            # FSM-transitioning message. Same deliberate trade as
            # ClaudeAgent: the has_committed gate is NOT re-run here,
            # because a gate that can reject forever would never
            # terminate. The flagging below keeps that visible.
            #
            # This turn is built on a FRESH context rather than the real
            # history, which is where it diverges from ClaudeAgent and
            # why. Forcing tool_choice to declare_outcome against a
            # history that contains sandbox calls is rejected outright
            # ("attempted to call tool 'run_command' which does not
            # match request.tool_choice"), and offering the full tool
            # list instead would stop the choice being forced at all.
            # Anthropic validates neither, so ClaudeAgent can simply
            # append and narrow. A summary of what the turn did stands
            # in for the transcript. That is a real behavioural
            # difference between the two backends, but only on this
            # exception path, and it also keeps the final call small,
            # which matters on an 8,000 token-per-minute budget.
            did = "; ".join(tool_log) if tool_log else "no tools were called"
            # The system prompt for In Progress advertises the sandbox
            # tools, so a model handed only declare_outcome still reaches
            # for run_command, and Groq then fails the request rather
            # than ignoring the stray call. Saying plainly that the
            # sandbox is closed is what actually stops it.
            closing = (
                "You have used all of your available steps for this turn.\n"
                f"What you did this turn: {did}.\n"
                f"Committed: {'yes' if has_committed else 'no'}.\n"
                "Your sandbox tools are no longer available and cannot be called. "
                f"The only action you can take is to call {_TOOL_NAME}. Do that now, "
                "with your best current outcome."
            )
            final_args, closing_prompt, closing_completion = self._force_declaration(
                context, triggers, closing
            )
            total_prompt += closing_prompt
            total_completion += closing_completion
            if final_args is None:
                raise RuntimeError(
                    f"{self._role_config.model} could not be made to declare an outcome at the "
                    "end of an In Progress turn, so this ticket has no FSM transition to apply. "
                    "Deliberately raised rather than inventing a trigger: a fabricated outcome "
                    "would be indistinguishable from a real one in the results."
                )

        if not verify_declaration(final_args.get("trigger", ""), has_committed=has_committed):
            # Only reachable through the iteration-budget escape hatch
            # above, where rejecting would risk never terminating. See
            # verification.accept_under_protest for that trade.
            final_args = dict(final_args)
            final_args["narrative"] = accept_under_protest(final_args["narrative"])

        return AgentResponse(
            message=self._build_message(context, final_args, total_prompt, total_completion, message_id=turn_id),
            trigger=final_args["trigger"],
        )

    def _force_declaration(
        self, context: AgentContext, triggers: list[str], closing: str
    ) -> tuple[dict[str, Any] | None, int, int]:
        """Gets a declare_outcome call out of the model on a clean
        context. Returns (args or None, prompt_tokens, completion_tokens).

        Two attempts, because the first failure here is usually the
        model reaching for a sandbox tool that is no longer on offer,
        which a blunter instruction fixes. Tokens from every attempt are
        returned so the turn's recorded cost covers what was really
        spent, including the attempts that failed.
        """
        prompt_tokens = 0
        completion_tokens = 0
        attempts = [
            closing,
            (
                "Call "
                f"{_TOOL_NAME} and nothing else. No other tool exists in this request, and "
                "any other call will be rejected. Summarise what you did in its fields."
            ),
        ]
        messages = self._messages(context, triggers)
        for instruction in attempts:
            try:
                response = self._create(
                    model=self._role_config.model,
                    max_tokens=MAX_OUTPUT_TOKENS,
                    messages=[*messages, {"role": "user", "content": instruction}],
                    tools=[_to_openai_tool(_tool_schema(triggers))],
                    tool_choice={"type": "function", "function": {"name": _TOOL_NAME}},
                )
            except BadRequestError:
                continue
            usage = response.usage
            prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
            completion_tokens += getattr(usage, "completion_tokens", 0) or 0
            calls = [
                c
                for c in (response.choices[0].message.tool_calls or [])
                if c.function.name == _TOOL_NAME
            ]
            args = _parse_arguments(calls[0]) if calls else None
            if args is not None and _conforms(_tool_schema(triggers)["input_schema"], args):
                return args, prompt_tokens, completion_tokens
        return None, prompt_tokens, completion_tokens

    def _log_tool(
        self,
        context: AgentContext,
        name: str,
        args: dict[str, Any],
        result_text: str,
        is_error: bool,
        turn_id: UUID,
    ) -> None:
        if self._event_log is None:
            return
        sender = AgentRef(role=self.role, instance_id=self.instance_id)
        self._event_log.append_message(
            Message(
                sender=sender,
                channel_id=context.channel_id,
                ticket_ref=context.ticket.id,
                type=MessageType.TOOL_CALL,
                parent_id=turn_id,
                content=MessageContent(text=_truncate(f"{name}({args})")),
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
            token_cost=TokenCost(
                prompt=prompt_tokens,
                completion=completion_tokens,
                model=self._role_config.model,
            ),
            **fields,
        )
