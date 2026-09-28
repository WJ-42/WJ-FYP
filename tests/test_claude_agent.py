"""Unit tests use a fake Anthropic client (no network, no cost) to check
the request-building and response-parsing logic. test_real_api_call is
the exception - it makes one real, tiny, billed call to prove the
mechanism actually works against the live API, not just against a
hand-written fixture (see feedback_verify_consumption_not_presence
memory: this project has been burned before by fixtures that don't
match real tool output). It self-skips when ANTHROPIC_API_KEY isn't set.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from wjfyp.eventlog import EventLog
from wjfyp.models.agent import RoleConfig
from wjfyp.models.message import HandoffNote, MessageType
from wjfyp.models.ticket import Ticket, TicketStatus
from wjfyp.orchestrator.agent import AgentContext
from wjfyp.orchestrator.claude_agent import ClaudeAgent
from wjfyp.sandbox.controller import TestResult
from wjfyp.sandbox.fake import FakeSandboxController


class _FakeToolUseBlock:
    type = "tool_use"

    def __init__(self, input: dict, name: str = "declare_outcome", id: str = "toolu_1"):
        self.input = input
        self.name = name
        self.id = id


class _FakeTextBlock:
    type = "text"

    def __init__(self, text: str):
        self.text = text


class _FakeUsage:
    def __init__(self, input_tokens: int, output_tokens: int):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeResponse:
    def __init__(self, content: list, usage: _FakeUsage):
        self.content = content
        self.usage = usage


def _tool_use_response(*calls: tuple[str, dict], input_tokens: int = 100, output_tokens: int = 50) -> _FakeResponse:
    blocks = [
        _FakeToolUseBlock(args, name=name, id=f"toolu_{i}") for i, (name, args) in enumerate(calls)
    ]
    return _FakeResponse(content=blocks, usage=_FakeUsage(input_tokens, output_tokens))


def _text_response(text: str, input_tokens: int = 100, output_tokens: int = 50) -> _FakeResponse:
    return _FakeResponse(content=[_FakeTextBlock(text)], usage=_FakeUsage(input_tokens, output_tokens))


def _declare_args(trigger: str = "code_submission") -> dict:
    return {
        "trigger": trigger,
        "narrative": "Implemented and committed.",
        "handoff_done": "Added the endpoint.",
        "handoff_remaining": "None.",
        "handoff_notes_for_next": "n/a",
    }


def _snapshot_call(kwargs: dict) -> dict:
    """The agent mutates its `messages` list in place across loop
    iterations and reuses the same list object on every API call - a
    fake that stores kwargs by reference ends up with every captured
    call aliasing that same, later-mutated list, so a test inspecting
    an earlier call sees the FINAL state instead of what was actually
    sent at that point. Snapshotting `messages` (the only mutable,
    reused argument) at capture time avoids that."""
    captured = dict(kwargs)
    if "messages" in captured:
        captured["messages"] = list(captured["messages"])
    return captured


class _FakeMessagesApi:
    def __init__(self, responses: list[_FakeResponse], captured_calls: list[dict]):
        self._responses = list(responses)
        self._captured_calls = captured_calls

    def create(self, **kwargs):
        self._captured_calls.append(_snapshot_call(kwargs))
        # Once only one scripted response is left, keep returning it - lets
        # a "runaway agent" test script one repeating tool-use response
        # instead of enumerating MAX_SANDBOX_ITERATIONS copies by hand.
        if len(self._responses) > 1:
            return self._responses.pop(0)
        return self._responses[0]


class _RunawayFakeClient:
    """Always returns `runaway_response`, unless this call's tool_choice
    specifically forces declare_outcome - then it returns
    `forced_response` instead. Simulates what the real API guarantees
    for a forced tool choice (see ClaudeAgent's own forced-call usage),
    which the simpler queue-based _FakeMessagesApi can't represent
    since it ignores tool_choice entirely."""

    def __init__(self, runaway_response: _FakeResponse, forced_response: _FakeResponse):
        self.captured_calls: list[dict] = []
        self._runaway_response = runaway_response
        self._forced_response = forced_response
        self.messages = self

    def create(self, **kwargs):
        self.captured_calls.append(_snapshot_call(kwargs))
        if kwargs.get("tool_choice") == {"type": "tool", "name": "declare_outcome"}:
            return self._forced_response
        return self._runaway_response


class _FakeClient:
    def __init__(self, tool_args: dict, input_tokens: int = 100, output_tokens: int = 50):
        self.captured_calls: list[dict] = []
        response = _FakeResponse(
            content=[_FakeToolUseBlock(tool_args)],
            usage=_FakeUsage(input_tokens, output_tokens),
        )
        self.messages = _FakeMessagesApi([response], self.captured_calls)


class _ScriptedFakeClient:
    """Like _FakeClient, but scripted with a distinct response per call -
    what the in-progress multi-turn loop tests need."""

    def __init__(self, responses: list[_FakeResponse]):
        self.captured_calls: list[dict] = []
        self.messages = _FakeMessagesApi(responses, self.captured_calls)


def _cto_role(personality: str | None = None) -> RoleConfig:
    return RoleConfig(id="cto", name="CTO", tier=3, model="claude-opus-5", personality=personality)


def _engineering_role(model: str = "claude-haiku-4-5") -> RoleConfig:
    return RoleConfig(id="engineering", name="Engineering", tier=1, model=model)


def _context(status: TicketStatus, handoff: HandoffNote | None = None) -> AgentContext:
    ticket = Ticket(id="TCK-1", title="Fix login bug", description="...", status=status)
    return AgentContext(
        ticket=ticket, channel_id="c1", role="cto", instance_id="cto-1", handoff=handoff
    )


def _in_progress_context(sandbox, handoff: HandoffNote | None = None) -> AgentContext:
    ticket = Ticket(
        id="TCK-1", title="Add a health check endpoint", description="...", status=TicketStatus.IN_PROGRESS
    )
    return AgentContext(
        ticket=ticket,
        channel_id="c1",
        role="engineering",
        instance_id="engineering-1",
        handoff=handoff,
        sandbox=sandbox,
    )


class ClaudeAgentTest(unittest.TestCase):
    def test_forced_tool_schema_matches_valid_triggers_for_the_status(self) -> None:
        client = _FakeClient(
            {
                "trigger": "approved",
                "narrative": "Looks good.",
                "handoff_done": "Reviewed.",
                "handoff_remaining": "n/a",
                "handoff_notes_for_next": "n/a",
            }
        )
        agent = ClaudeAgent(_cto_role(), "cto-1", client=client)

        agent.invoke(_context(TicketStatus.REVIEW))

        call = client.captured_calls[0]
        self.assertEqual(call["tool_choice"], {"type": "tool", "name": "declare_outcome"})
        schema = call["tools"][0]["input_schema"]
        self.assertEqual(
            set(schema["properties"]["trigger"]["enum"]), {"approved", "changes_requested"}
        )
        self.assertTrue(call["tools"][0]["strict"])

    def test_response_becomes_a_message_with_handoff_and_token_cost(self) -> None:
        client = _FakeClient(
            {
                "trigger": "approved",
                "narrative": "Ship it.",
                "handoff_done": "Reviewed and approved.",
                "handoff_remaining": "None.",
                "handoff_notes_for_next": "Nothing outstanding.",
            },
            input_tokens=321,
            output_tokens=45,
        )
        agent = ClaudeAgent(_cto_role(), "cto-1", client=client)

        result = agent.invoke(_context(TicketStatus.REVIEW))

        self.assertEqual(result.trigger, "approved")
        self.assertEqual(result.message.type, MessageType.REVIEW_FEEDBACK)
        self.assertEqual(result.message.content.text, "Ship it.")
        self.assertEqual(result.message.content.handoff.done, "Reviewed and approved.")
        self.assertEqual(result.message.token_cost.prompt, 321)
        self.assertEqual(result.message.token_cost.completion, 45)

    def test_personality_is_appended_to_the_system_prompt(self) -> None:
        client = _FakeClient(
            {
                "trigger": "approved",
                "narrative": "n/a",
                "handoff_done": "n/a",
                "handoff_remaining": "n/a",
                "handoff_notes_for_next": "n/a",
            }
        )
        agent = ClaudeAgent(_cto_role(personality="Be extremely terse."), "cto-1", client=client)

        agent.invoke(_context(TicketStatus.REVIEW))

        # system is a cacheable block list (see _cached_system), not a bare string
        self.assertIn("Be extremely terse.", client.captured_calls[0]["system"][0]["text"])

    def test_handoff_is_relayed_into_the_user_turn(self) -> None:
        client = _FakeClient(
            {
                "trigger": "approved",
                "narrative": "n/a",
                "handoff_done": "n/a",
                "handoff_remaining": "n/a",
                "handoff_notes_for_next": "n/a",
            }
        )
        agent = ClaudeAgent(_cto_role(), "cto-1", client=client)
        handoff = HandoffNote(
            done="Fixed the bug.", remaining="Needs review.", notes_for_next="Check the edge case."
        )

        agent.invoke(_context(TicketStatus.REVIEW, handoff=handoff))

        user_content = client.captured_calls[0]["messages"][0]["content"]
        self.assertIn("Check the edge case.", user_content)

    def test_raises_clearly_when_invoked_at_a_status_with_no_outgoing_transitions(self) -> None:
        agent = ClaudeAgent(_cto_role(), "cto-1", client=_FakeClient({}))

        with self.assertRaises(ValueError):
            agent.invoke(_context(TicketStatus.DONE))

    def test_specd_stage_still_uses_the_single_forced_call_path(self) -> None:
        """Regression check: engineering acts at both Specd (no sandbox,
        single forced call) and In Progress (sandbox present, multi-turn
        loop - see ClaudeAgentInProgressTest below). Specd must still
        behave exactly like every other non-sandbox stage.
        """
        client = _FakeClient(
            {
                "trigger": "assigned",
                "narrative": "Taking this on.",
                "handoff_done": "n/a",
                "handoff_remaining": "Implementation.",
                "handoff_notes_for_next": "n/a",
            }
        )
        ticket = Ticket(id="TCK-1", title="Add health check", description="...", status=TicketStatus.SPECD)
        context = AgentContext(
            ticket=ticket, channel_id="c1", role="engineering", instance_id="engineering-1", sandbox=None
        )
        agent = ClaudeAgent(_engineering_role(), "engineering-1", client=client)

        result = agent.invoke(context)

        self.assertEqual(result.trigger, "assigned")
        self.assertEqual(len(client.captured_calls), 1)
        self.assertEqual(client.captured_calls[0]["tool_choice"], {"type": "tool", "name": "declare_outcome"})
        # only the single declare_outcome tool, no sandbox tools, at this stage
        self.assertEqual(len(client.captured_calls[0]["tools"]), 1)


class ClaudeAgentInProgressTest(unittest.TestCase):
    def test_uses_sandbox_tools_then_submits(self) -> None:
        sandbox = FakeSandboxController()
        client = _ScriptedFakeClient(
            [
                _tool_use_response(("write_file", {"path": "app.py", "content": "def health(): ..."})),
                _tool_use_response(("git_commit", {"message": "add health check"})),
                _tool_use_response(("declare_outcome", _declare_args())),
            ]
        )
        agent = ClaudeAgent(_engineering_role(), "engineering-1", client=client)

        result = agent.invoke(_in_progress_context(sandbox))

        self.assertEqual(result.trigger, "code_submission")
        self.assertEqual(result.message.type, MessageType.CODE_SUBMISSION)
        self.assertEqual(sandbox.files["app.py"], "def health(): ...")
        self.assertEqual(len(sandbox.commits), 1)
        # usage from all 3 turns is summed onto the one FSM-transitioning message
        self.assertEqual(result.message.token_cost.prompt, 300)
        self.assertEqual(result.message.token_cost.completion, 150)

    def test_a_failed_tool_call_is_reported_back_as_an_error_not_a_crash(self) -> None:
        sandbox = FakeSandboxController()  # no files - read_file will KeyError
        client = _ScriptedFakeClient(
            [
                _tool_use_response(("read_file", {"path": "missing.py"})),
                _tool_use_response(("declare_outcome", _declare_args())),
            ]
        )
        agent = ClaudeAgent(_engineering_role(), "engineering-1", client=client)

        result = agent.invoke(_in_progress_context(sandbox))

        self.assertEqual(result.trigger, "code_submission")
        second_call_content = client.captured_calls[1]["messages"][-1]["content"]
        tool_result = next(b for b in second_call_content if b.get("tool_use_id") == "toolu_0")
        self.assertTrue(tool_result["is_error"])
        self.assertIn("KeyError", tool_result["content"])

    def test_text_only_response_gets_nudged_to_act_or_finish(self) -> None:
        sandbox = FakeSandboxController()
        client = _ScriptedFakeClient(
            [
                _text_response("Let me think about this first."),
                _tool_use_response(("declare_outcome", _declare_args())),
            ]
        )
        agent = ClaudeAgent(_engineering_role(), "engineering-1", client=client)

        result = agent.invoke(_in_progress_context(sandbox))

        self.assertEqual(result.trigger, "code_submission")
        # the nudge is a plain string in the loop's own messages list, but
        # _cached_messages wraps the last message's content into a cacheable
        # block list before it's sent, so the captured call sees blocks
        nudge = client.captured_calls[1]["messages"][-1]["content"]
        self.assertIn("declare_outcome", nudge[0]["text"])

    def test_exhausting_the_iteration_budget_forces_a_final_declare_outcome_call(self) -> None:
        sandbox = FakeSandboxController()
        # Always calls run_command, never declare_outcome, unless this
        # specific call's tool_choice forces declare_outcome - a runaway
        # agent that only stops once the harness forces it to.
        client = _RunawayFakeClient(
            runaway_response=_tool_use_response(("run_command", {"cmd": "echo hi"})),
            forced_response=_tool_use_response(("declare_outcome", _declare_args())),
        )
        agent = ClaudeAgent(_engineering_role(), "engineering-1", client=client)

        result = agent.invoke(_in_progress_context(sandbox))

        self.assertEqual(result.trigger, "code_submission")
        final_call = client.captured_calls[-1]
        self.assertEqual(final_call["tool_choice"], {"type": "tool", "name": "declare_outcome"})
        # the forced final call, plus every iteration of the main loop
        from wjfyp.orchestrator.claude_agent import MAX_SANDBOX_ITERATIONS

        self.assertEqual(len(client.captured_calls), MAX_SANDBOX_ITERATIONS + 1)

    def test_declare_outcome_without_committing_is_rejected_and_retried(self) -> None:
        sandbox = FakeSandboxController()
        client = _ScriptedFakeClient(
            [
                _tool_use_response(("declare_outcome", _declare_args())),  # no commit yet - rejected
                _tool_use_response(("git_commit", {"message": "add health check"})),
                _tool_use_response(("declare_outcome", _declare_args())),  # now accepted
            ]
        )
        agent = ClaudeAgent(_engineering_role(), "engineering-1", client=client)

        result = agent.invoke(_in_progress_context(sandbox))

        self.assertEqual(result.trigger, "code_submission")
        self.assertEqual(len(sandbox.commits), 1)
        self.assertNotIn("submitted without committing", result.message.content.text)
        # the rejected first attempt got a tool_result telling it to commit first
        second_call_messages = client.captured_calls[1]["messages"]
        rejection = next(m for m in second_call_messages if m["role"] == "user" and isinstance(m["content"], list))
        self.assertTrue(rejection["content"][0]["is_error"])
        self.assertIn("commit", rejection["content"][0]["content"])

    def test_exhausting_the_budget_while_still_uncommitted_flags_it_visibly(self) -> None:
        sandbox = FakeSandboxController()
        client = _RunawayFakeClient(
            runaway_response=_tool_use_response(("run_command", {"cmd": "echo hi"})),
            forced_response=_tool_use_response(("declare_outcome", _declare_args())),
        )
        agent = ClaudeAgent(_engineering_role(), "engineering-1", client=client)

        result = agent.invoke(_in_progress_context(sandbox))

        self.assertEqual(result.trigger, "code_submission")
        self.assertIn("submitted without committing", result.message.content.text)

    def test_sandbox_tool_activity_is_logged_to_the_event_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            event_log = EventLog(Path(tmp) / "eventlog.db")
            self.addCleanup(event_log.close)
            sandbox = FakeSandboxController()
            client = _ScriptedFakeClient(
                [
                    _tool_use_response(("write_file", {"path": "app.py", "content": "code"})),
                    _tool_use_response(("declare_outcome", _declare_args())),
                ]
            )
            agent = ClaudeAgent(_engineering_role(), "engineering-1", client=client, event_log=event_log)

            result = agent.invoke(_in_progress_context(sandbox))

            logged = event_log.get_messages_for_ticket("TCK-1")
            # write_file's tool_call + tool_result got logged directly by the
            # agent; the final code_submission message is only returned, not
            # logged here - that's loop.py's job in real usage, same as
            # every other stage.
            self.assertEqual([m.type for m in logged], [MessageType.TOOL_CALL, MessageType.TOOL_RESULT])
            self.assertIn("write_file", logged[0].content.text)
            self.assertIn("wrote 4 bytes", logged[1].content.text)
            self.assertEqual(result.trigger, "code_submission")
            # both detail messages correlate back to the one narrative
            # message this turn produces, so the dashboard can hide them by
            # default and reveal them as a "Logs" toggle on that message.
            self.assertEqual(logged[0].parent_id, result.message.id)
            self.assertEqual(logged[1].parent_id, result.message.id)

    def test_narrative_instructions_ask_for_plain_language_not_code(self) -> None:
        client = _FakeClient(_declare_args("approved"))
        agent = ClaudeAgent(_cto_role(), "cto-1", client=client)

        agent.invoke(_context(TicketStatus.REVIEW))

        call = client.captured_calls[0]
        self.assertIn("plain", call["system"][0]["text"].lower())
        narrative_description = call["tools"][0]["input_schema"]["properties"]["narrative"]["description"]
        self.assertIn("no code, paths, commands, or error output", narrative_description)


@unittest.skipUnless(
    os.environ.get("ANTHROPIC_API_KEY"), "ANTHROPIC_API_KEY not set - skipping real API call"
)
class RealApiIntegrationTest(unittest.TestCase):
    def test_real_api_call_against_haiku(self) -> None:
        role = RoleConfig(id="engineering", name="Engineering", tier=1, model="claude-haiku-4-5")
        agent = ClaudeAgent(role, "engineering-1")
        ticket = Ticket(
            id="TCK-1",
            title="Add a health check endpoint",
            description="Add a GET /health endpoint that returns 200 OK.",
            status=TicketStatus.SPECD,
        )
        context = AgentContext(
            ticket=ticket, channel_id="c1", role="engineering", instance_id="engineering-1"
        )

        result = agent.invoke(context)

        self.assertEqual(result.trigger, "assigned")
        self.assertTrue(result.message.content.text)
        self.assertIsNotNone(result.message.content.handoff)
        self.assertGreater(result.message.token_cost.prompt, 0)
        self.assertGreater(result.message.token_cost.completion, 0)

    def test_real_api_call_uses_sandbox_tools_and_submits(self) -> None:
        """Uses a FakeSandboxController (not real Docker - that mechanism
        is already separately verified against real Docker per FYP-22)
        so this test isolates what it's actually checking: that the real
        API genuinely drives a multi-turn tool-use loop end to end and
        ends it correctly, not that the sandbox itself works.

        The ticket explicitly says the repo is empty - FakeSandboxController
        can't simulate a real filesystem for run_command to explore (every
        command returns empty output regardless of what it is), so a real
        model given an ambiguous "existing codebase" ticket reasonably
        burns its whole turn budget on legitimate reconnaissance instead of
        writing anything. That's a limitation of this fake, not something
        this test needs to exercise - real Docker exploration is what
        FYP-22's own tests already cover.
        """
        sandbox = FakeSandboxController(
            test_results=[
                TestResult(
                    passed=True,
                    fail_to_pass_total=0,
                    fail_to_pass_passed=0,
                    pass_to_pass_total=0,
                    pass_to_pass_passed=0,
                    output="1 passed",
                )
            ]
        )
        role = RoleConfig(id="engineering", name="Engineering", tier=1, model="claude-haiku-4-5")
        agent = ClaudeAgent(role, "engineering-1")
        ticket = Ticket(
            id="TCK-1",
            title="Add a health check endpoint",
            description=(
                "Create a new file app.py implementing a single Flask route, GET /health, "
                "that returns 200 OK. This is a fresh, empty repository - there is nothing "
                "to inspect first, just create the file directly."
            ),
            status=TicketStatus.IN_PROGRESS,
        )
        context = AgentContext(
            ticket=ticket, channel_id="c1", role="engineering", instance_id="engineering-1", sandbox=sandbox
        )

        result = agent.invoke(context)

        self.assertEqual(result.trigger, "code_submission")
        self.assertEqual(result.message.type, MessageType.CODE_SUBMISSION)
        self.assertGreaterEqual(len(sandbox.commits), 1)
        self.assertIsNotNone(result.message.content.handoff)
        self.assertGreater(result.message.token_cost.prompt, 0)
        self.assertGreater(result.message.token_cost.completion, 0)


if __name__ == "__main__":
    unittest.main()
