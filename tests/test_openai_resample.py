"""An endpoint that treats a forced tool choice as a hint rather than a
guarantee returns nothing at all some of the time.

Measured on a self-hosted Ollama server, 03/11/2026, over fifty
IDENTICAL forced calls: qwen3:4b returned no call at all on 3 of 50
(6.0%), finish_reason "stop" with empty content, and qwen3:8b on 1 of
10. Groq and Anthropic never did this, so the single-turn path used to
raise on the first occurrence. An arm makes hundreds of forced calls,
so at 6% that is a run that dies partway through rather than one that
fails cleanly at the start.

These tests use a fake client, so they cost nothing and do not need the
server up. What they cannot prove is that the real server behaves as
scripted here; that was established by the measurement above.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from wjfyp.eventlog import EventLog
from wjfyp.models.agent import RoleConfig
from wjfyp.models.message import MessageType
from wjfyp.models.ticket import Ticket, TicketStatus
from wjfyp.orchestrator.agent import AgentContext
from wjfyp.orchestrator.openai_agent import MAX_EMPTY_RESAMPLES, OpenAIAgent


class _Fn:
    def __init__(self, name: str, arguments: str):
        self.name = name
        self.arguments = arguments


class _Call:
    def __init__(self, name: str, arguments: str, id: str = "call_1"):
        self.id = id
        self.type = "function"
        self.function = _Fn(name, arguments)


class _Message:
    def __init__(self, tool_calls: list | None, content: str = ""):
        self.content = content
        self.tool_calls = tool_calls


class _Choice:
    def __init__(self, message: _Message, finish_reason: str):
        self.message = message
        self.finish_reason = finish_reason


class _Usage:
    def __init__(self, prompt_tokens: int, completion_tokens: int):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class _Response:
    def __init__(self, choice: _Choice, usage: _Usage):
        self.choices = [choice]
        self.usage = usage


def empty(prompt: int = 170, completion: int = 1177) -> _Response:
    """What the real server returned on the failing run: a normal stop,
    empty content, no call. NOT finish_reason "length" - the model was
    not cut off, it declined."""
    return _Response(_Choice(_Message(None, ""), "stop"), _Usage(prompt, completion))


def declared(trigger: str = "approved", prompt: int = 170, completion: int = 600) -> _Response:
    args = json.dumps(
        {
            "trigger": trigger,
            "narrative": "Looks right.",
            "handoff_done": "Reviewed the change.",
            "handoff_remaining": "Nothing.",
            "handoff_notes_for_next": "n/a",
        }
    )
    return _Response(
        _Choice(_Message([_Call("declare_outcome", args)]), "tool_calls"),
        _Usage(prompt, completion),
    )


def unparsable(prompt: int = 170, completion: int = 90) -> _Response:
    return _Response(
        _Choice(_Message([_Call("declare_outcome", "{not json")]), "tool_calls"),
        _Usage(prompt, completion),
    )


class _ScriptedClient:
    """Returns the scripted responses in order, repeating the last one
    once the script runs out."""

    def __init__(self, *responses: _Response):
        self.calls: list[dict] = []
        self._responses = list(responses)
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self._responses) > 1:
            return self._responses.pop(0)
        return self._responses[0]


ROLE = RoleConfig(id="cto", name="CTO", tier=1, model="qwen3:4b", count=1)


def context() -> AgentContext:
    return AgentContext(
        ticket=Ticket(
            id="X-1",
            title="Validate the expiry header",
            description="It is read but never validated.",
            acceptance_criteria=["Reject malformed values."],
            status=TicketStatus.REVIEW,
        ),
        channel_id="c",
        role="cto",
        instance_id="cto-1",
    )


class ResampleTest(unittest.TestCase):
    def test_a_single_empty_response_no_longer_kills_the_turn(self):
        client = _ScriptedClient(empty(), declared("approved"))
        agent = OpenAIAgent(ROLE, "cto-1", client=client)
        response = agent.invoke(context())
        self.assertEqual(response.trigger, "approved")
        self.assertEqual(len(client.calls), 2)

    def test_a_call_missing_required_fields_is_resampled(self):
        """Five fields are required and a hint-only endpoint constrains
        generation to none of them. Before the conformance check this
        surfaced as a KeyError from _build_message, several frames away
        from the cause."""
        partial = _Response(
            _Choice(
                _Message([_Call("declare_outcome", json.dumps({"trigger": "approved"}))]),
                "tool_calls",
            ),
            _Usage(170, 80),
        )
        client = _ScriptedClient(partial, declared("approved"))
        agent = OpenAIAgent(ROLE, "cto-1", client=client)
        self.assertEqual(agent.invoke(context()).trigger, "approved")
        self.assertEqual(len(client.calls), 2)

    def test_tokens_from_the_discarded_attempt_are_still_charged(self):
        """The empty attempt really consumed 1,177 completion tokens.
        Recording only the successful attempt would understate the cost
        of running an arm on a backend that needs resampling, which is
        exactly the number the comparison is meant to report.
        """
        client = _ScriptedClient(empty(prompt=170, completion=1177), declared(prompt=170, completion=600))
        agent = OpenAIAgent(ROLE, "cto-1", client=client)
        message = agent.invoke(context()).message
        self.assertEqual(message.token_cost.prompt, 340)
        self.assertEqual(message.token_cost.completion, 1777)
        self.assertEqual(message.token_cost.model, "qwen3:4b")

    def test_it_gives_up_rather_than_resampling_forever(self):
        client = _ScriptedClient(empty())
        agent = OpenAIAgent(ROLE, "cto-1", client=client)
        with self.assertRaises(ValueError) as caught:
            agent.invoke(context())
        self.assertEqual(len(client.calls), 1 + MAX_EMPTY_RESAMPLES)
        self.assertIn("finish_reason='stop'", str(caught.exception))

    def test_unparsable_arguments_are_resampled_too(self):
        client = _ScriptedClient(unparsable(), declared("changes_requested"))
        agent = OpenAIAgent(ROLE, "cto-1", client=client)
        self.assertEqual(agent.invoke(context()).trigger, "changes_requested")

    def test_a_trigger_outside_the_enum_is_rejected(self):
        """A hint-only endpoint does not constrain generation to the
        schema either, so an out-of-enum trigger is possible and would
        be an illegal FSM transition if it were trusted.
        """
        client = _ScriptedClient(declared("code_submission"), declared("approved"))
        agent = OpenAIAgent(ROLE, "cto-1", client=client)
        response = agent.invoke(context())
        self.assertEqual(response.trigger, "approved")
        self.assertEqual(len(client.calls), 2)

    def test_every_resample_is_recorded_in_the_event_log(self):
        """An unlogged resample would make the run look clean while
        quietly changing how the data was obtained. The rate is also a
        result in its own right, so it has to be recoverable per arm.
        """
        with tempfile.TemporaryDirectory() as tmp:
            log = EventLog(Path(tmp) / "events.db")
            client = _ScriptedClient(empty(), unparsable(), declared("approved"))
            agent = OpenAIAgent(ROLE, "cto-1", client=client, event_log=log)
            agent.invoke(context())

            notes = [
                m
                for m in log.get_all_messages()
                if m.type == MessageType.SYSTEM and "discarded attempt" in m.content.text
            ]
            self.assertEqual(len(notes), 2)
            self.assertIn("discarded attempt 1/4", notes[0].content.text)
            self.assertIn("no 'declare_outcome' call", notes[0].content.text)
            self.assertIn("discarded attempt 2/4", notes[1].content.text)
            self.assertIn("unparsable", notes[1].content.text)

    def test_nothing_is_logged_when_the_first_attempt_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = EventLog(Path(tmp) / "events.db")
            client = _ScriptedClient(declared("approved"))
            agent = OpenAIAgent(ROLE, "cto-1", client=client, event_log=log)
            agent.invoke(context())
            notes = [
                m
                for m in log.get_all_messages()
                if m.type == MessageType.SYSTEM and "discarded attempt" in m.content.text
            ]
            self.assertEqual(notes, [])


if __name__ == "__main__":
    unittest.main()
