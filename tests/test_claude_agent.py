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
import unittest

from wjfyp.models.agent import RoleConfig
from wjfyp.models.message import HandoffNote, MessageType
from wjfyp.models.ticket import Ticket, TicketStatus
from wjfyp.orchestrator.agent import AgentContext
from wjfyp.orchestrator.claude_agent import ClaudeAgent


class _FakeToolUseBlock:
    type = "tool_use"

    def __init__(self, input: dict):
        self.input = input


class _FakeUsage:
    def __init__(self, input_tokens: int, output_tokens: int):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeResponse:
    def __init__(self, content: list, usage: _FakeUsage):
        self.content = content
        self.usage = usage


class _FakeMessagesApi:
    def __init__(self, response: _FakeResponse, captured_calls: list[dict]):
        self._response = response
        self._captured_calls = captured_calls

    def create(self, **kwargs):
        self._captured_calls.append(kwargs)
        return self._response


class _FakeClient:
    def __init__(self, tool_args: dict, input_tokens: int = 100, output_tokens: int = 50):
        self.captured_calls: list[dict] = []
        response = _FakeResponse(
            content=[_FakeToolUseBlock(tool_args)],
            usage=_FakeUsage(input_tokens, output_tokens),
        )
        self.messages = _FakeMessagesApi(response, self.captured_calls)


def _cto_role(personality: str | None = None) -> RoleConfig:
    return RoleConfig(id="cto", name="CTO", tier=3, model="claude-opus-5", personality=personality)


def _context(status: TicketStatus, handoff: HandoffNote | None = None) -> AgentContext:
    ticket = Ticket(id="TCK-1", title="Fix login bug", description="...", status=status)
    return AgentContext(
        ticket=ticket, channel_id="c1", role="cto", instance_id="cto-1", handoff=handoff
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

        self.assertIn("Be extremely terse.", client.captured_calls[0]["system"])

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


if __name__ == "__main__":
    unittest.main()
