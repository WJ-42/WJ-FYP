"""Keeping the prompt inside the window, rather than detecting that it
was not.

test_context_guard pins the DETECTION of a slid window. These pin the
prevention, and they exist because detection turned out not to be
enough. On 04/11/2026, with the grader finally returning real pass/fail
feedback, one In Progress turn reached a 28,076-token prompt against a
16,384-token server window. Ollama front-truncated it and reported the
PRE-truncation count, so _check_context_held saw a prompt growing
healthily with the transcript and said nothing, while the model was
being shown only the last 16,384 tokens.

The behavioural consequence is what makes this worth preventing rather
than merely logging. Having lost its own earlier attempts, the model
oscillated between two assertions it had already watched fail:

    "original contentappended content"      -> fails, there is a \n
    "original content\nappended content"    -> fails, trailing \n too

It never reached the correct third form in 45 minutes, because the
record of the first was gone by the time it came round again. So a trim
that merely freed space would reproduce the same bug more cheaply: these
tests pin the digest of what was dropped just as hard as the dropping.
"""

from __future__ import annotations

import copy
import json
import unittest
from unittest.mock import patch

from wjfyp.models.agent import RoleConfig
from wjfyp.orchestrator import openai_agent
from wjfyp.orchestrator.openai_agent import (
    OpenAIAgent,
    _digest_group,
    _exchange_groups,
)

ROLE = RoleConfig(id="engineering", name="Engineer", tier=3, model="qwen3:8b", count=1)

SYSTEM = {"role": "system", "content": "You are an engineer. " + "persona " * 40}
TICKET = "Write a unit test for append_file."


def agent() -> OpenAIAgent:
    return OpenAIAgent(ROLE, "engineering-1", client=object())


def exchange(call_id: str, name: str, arguments: str, result: str) -> list[dict]:
    """One assistant tool call and the reply that answers it."""
    return [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}
            ],
        },
        {"role": "tool", "tool_call_id": call_id, "content": result},
    ]


def transcript(exchanges: list[list[dict]]) -> list[dict]:
    return [SYSTEM, {"role": "user", "content": TICKET}, *[m for e in exchanges for m in e]]


def bulky(call_id: str, assertion: str, chars: int = 4000) -> list[dict]:
    """An exchange whose arguments are big enough to matter to a budget,
    carrying a recognisable assertion and a failing result."""
    args = json.dumps({"new_text": assertion, "pad": "x" * chars})
    return exchange(call_id, "replace_in_file", args, f"ERROR: AssertionError on {assertion}")


class ExchangeGroupingTest(unittest.TestCase):
    """The schema requires an assistant tool_call and its reply to travel
    together. Trimming by message count would produce transcripts no
    endpoint accepts, so the grouping is the part that has to be right.
    """

    def test_an_assistant_keeps_its_tool_replies(self):
        body = exchange("c1", "run_tests", "{}", "failed")
        groups = _exchange_groups(body)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]), 2)

    def test_several_replies_to_one_assistant_stay_in_one_group(self):
        body = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "a", "type": "function", "function": {"name": "read_file", "arguments": "{}"}},
                    {"id": "b", "type": "function", "function": {"name": "run_tests", "arguments": "{}"}},
                ],
            },
            {"role": "tool", "tool_call_id": "a", "content": "contents"},
            {"role": "tool", "tool_call_id": "b", "content": "failed"},
        ]
        groups = _exchange_groups(body)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]), 3)

    def test_a_user_nudge_stands_alone(self):
        body = [
            *exchange("c1", "run_tests", "{}", "failed"),
            {"role": "user", "content": "Continue working with your sandbox tools."},
        ]
        groups = _exchange_groups(body)
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[1][0]["role"], "user")

    def test_an_orphan_tool_reply_does_not_attach_to_a_user_message(self):
        """Defensive: a tool reply following a user message has no call
        to belong to, so it must not be glued onto one."""
        body = [{"role": "user", "content": "nudge"}, {"role": "tool", "tool_call_id": "x", "content": "y"}]
        self.assertEqual(len(_exchange_groups(body)), 2)


class DigestTest(unittest.TestCase):
    def test_a_failed_call_is_recorded_as_failed(self):
        lines = _digest_group(exchange("c1", "run_tests", "{}", "ERROR: boom"))
        self.assertEqual(len(lines), 1)
        self.assertIn("run_tests", lines[0])
        self.assertIn("FAILED", lines[0])

    def test_a_successful_call_is_recorded_as_ok(self):
        lines = _digest_group(exchange("c1", "run_tests", "{}", "1 passed"))
        self.assertIn("ok", lines[0])
        self.assertNotIn("FAILED", lines[0])

    def test_the_arguments_identify_WHICH_attempt_it_was(self):
        """The oscillation this whole mechanism exists to stop was
        between two assertions. A digest naming only the tool would not
        have distinguished them, and so would not have helped."""
        lines = _digest_group(bulky("c1", "original contentappended content"))
        self.assertIn("original contentappended content", lines[0])

    def test_a_huge_argument_is_cut_short(self):
        lines = _digest_group(bulky("c1", "assertion", chars=50_000))
        self.assertLess(len(lines[0]), 400)
        self.assertIn("...", lines[0])

    def test_a_user_message_has_nothing_to_digest(self):
        self.assertEqual(_digest_group([{"role": "user", "content": "nudge"}]), [])


class TrimTest(unittest.TestCase):
    def trim(self, messages, elided=None, budget=1000):
        with patch.object(openai_agent, "CONTEXT_BUDGET_TOKENS", budget):
            return agent()._trim_transcript(
                messages, SYSTEM, TICKET, fixed_chars=0, elided=elided or []
            )

    def test_a_transcript_within_budget_is_untouched(self):
        messages = transcript([exchange("c1", "run_tests", "{}", "1 passed")])
        out, elided = self.trim(messages, budget=100_000)
        self.assertIs(out, messages)
        self.assertEqual(elided, [])

    def test_an_oversized_transcript_loses_its_oldest_exchange(self):
        messages = transcript([bulky("c1", "first"), bulky("c2", "second"), bulky("c3", "third")])
        out, _ = self.trim(messages)
        sent = json.dumps(out)
        self.assertNotIn('"c1"', sent)
        self.assertIn('"c3"', sent)

    def test_the_persona_and_the_ticket_always_survive(self):
        """The system prompt carries the role persona, which is this
        project's independent variable. A turn that lost it would be a
        measurement of nothing, so it is not a trim candidate at any
        budget."""
        messages = transcript([bulky(f"c{i}", f"attempt {i}") for i in range(6)])
        out, _ = self.trim(messages, budget=50)
        self.assertEqual(out[0], SYSTEM)
        self.assertEqual(out[1]["role"], "user")
        self.assertTrue(out[1]["content"].startswith(TICKET))

    def test_every_replayed_call_still_has_its_reply(self):
        """The structural invariant. An assistant tool_call whose reply
        was trimmed away is rejected by the endpoint, and because the
        message stays in history every later request in the turn fails
        the same way."""
        messages = transcript([bulky(f"c{i}", f"attempt {i}") for i in range(8)])
        out, _ = self.trim(messages, budget=400)
        answered = {m.get("tool_call_id") for m in out if m.get("role") == "tool"}
        for message in out:
            for call in message.get("tool_calls") or []:
                self.assertIn(call["id"], answered)
        for message in out:
            if message.get("role") == "tool":
                self.assertTrue(
                    any(
                        call["id"] == message["tool_call_id"]
                        for m in out
                        for call in (m.get("tool_calls") or [])
                    )
                )

    def test_what_was_dropped_comes_back_as_a_warning_not_a_silence(self):
        """The whole point. Freeing the space without carrying the
        failures forward is the bug, not the fix."""
        messages = transcript([bulky("c1", "original contentappended content"), bulky("c2", "second"), bulky("c3", "third")])
        out, _ = self.trim(messages)
        note = out[1]["content"]
        self.assertIn("original contentappended content", note)
        self.assertIn("FAILED", note)
        self.assertIn("do not try it again", note)

    def test_the_digest_accumulates_across_successive_trims(self):
        """A failure elided on iteration 3 has to still be visible on
        iteration 11, or the oscillation simply happens more slowly."""
        first = transcript([bulky("c1", "assertion ONE"), bulky("c2", "second"), bulky("c3", "third")])
        out, elided = self.trim(first)
        self.assertTrue(any("assertion ONE" in line for line in elided))

        grown = [*out, *bulky("c4", "assertion FOUR"), *bulky("c5", "fifth")]
        out2, elided2 = self.trim(grown, elided=elided)
        self.assertTrue(any("assertion ONE" in line for line in elided2))
        self.assertIn("assertion ONE", out2[1]["content"])

    def test_the_note_does_not_pile_up_on_itself(self):
        """The ticket turn is rebuilt from the pristine text each time.
        Editing it in place would nest one note inside the next and the
        prompt would grow with every trim, which is the opposite of the
        job."""
        messages = transcript([bulky(f"c{i}", f"attempt {i}") for i in range(5)])
        out, elided = self.trim(messages)
        again, _ = self.trim([*out, *bulky("c9", "ninth")], elided=elided)
        self.assertEqual(again[1]["content"].count("EARLIER STEPS THIS TURN"), 1)

    def test_the_most_recent_exchange_is_never_dropped(self):
        """It holds the action just taken and the result the model has to
        react to. A turn without it cannot proceed at all."""
        messages = transcript([bulky(f"c{i}", f"attempt {i}", chars=30_000) for i in range(4)])
        out, _ = self.trim(messages, budget=10)
        sent = json.dumps(out)
        self.assertIn('"c3"', sent)

    def test_an_impossible_budget_returns_a_transcript_rather_than_hanging(self):
        """Down to the floor and still over budget is a misconfigured
        budget. Losing the run over it would be worse than proceeding
        and letting _check_context_held speak."""
        messages = transcript([bulky("c1", "only", chars=80_000)])
        out, _ = self.trim(messages, budget=1)
        self.assertEqual(out[0], SYSTEM)
        self.assertGreaterEqual(len(out), 2)

    def test_the_tool_schemas_count_against_the_budget(self):
        """They are re-sent with every request and occupy the window
        exactly as the transcript does. Budgeting without them
        under-counts by a fixed and substantial amount."""
        messages = transcript([exchange("c1", "run_tests", "{}", "1 passed")])
        with patch.object(openai_agent, "CONTEXT_BUDGET_TOKENS", 500):
            untrimmed, _ = agent()._trim_transcript(messages, SYSTEM, TICKET, 0, [])
            trimmed, _ = agent()._trim_transcript(messages, SYSTEM, TICKET, 40_000, [])
        self.assertIs(untrimmed, messages)
        self.assertIsNot(trimmed, messages)


class CalibrationTest(unittest.TestCase):
    def test_a_plausible_ratio_is_adopted(self):
        a = agent()
        a._calibrate(sent_chars=40_000, prompt_tokens=10_000)
        self.assertAlmostEqual(a._chars_per_token, 4.0)

    def test_an_implausible_ratio_is_refused(self):
        """A ratio read as very high makes every transcript look small
        and every prompt look like it fits, so the trim would never fire
        - the exact failure it exists to prevent. A truncated prompt
        produces readings of just that shape, because the reported count
        stops tracking what was sent.
        """
        a = agent()
        before = a._chars_per_token
        a._calibrate(sent_chars=120_000, prompt_tokens=2_050)
        self.assertEqual(a._chars_per_token, before)

    def test_a_zero_reading_is_ignored(self):
        a = agent()
        before = a._chars_per_token
        a._calibrate(sent_chars=0, prompt_tokens=0)
        self.assertEqual(a._chars_per_token, before)

    def test_calibration_changes_what_fits(self):
        """The point of measuring it. At 2 chars per token a transcript
        is twice the tokens it was at 4, and has to be trimmed sooner."""
        messages = transcript([exchange("c1", "run_tests", "{}", "x" * 1500)])
        a = agent()
        a._calibrate(sent_chars=40_000, prompt_tokens=10_000)
        with patch.object(openai_agent, "CONTEXT_BUDGET_TOKENS", 600):
            loose, _ = a._trim_transcript(messages, SYSTEM, TICKET, 0, [])
        a._calibrate(sent_chars=20_000, prompt_tokens=10_000)
        with patch.object(openai_agent, "CONTEXT_BUDGET_TOKENS", 600):
            tight, _ = a._trim_transcript(messages, SYSTEM, TICKET, 0, [])
        self.assertIs(loose, messages)
        self.assertIsNot(tight, messages)


if __name__ == "__main__":
    unittest.main()


class _Fn:
    def __init__(self, name: str, arguments: str):
        self.name = name
        self.arguments = arguments


class _Call:
    def __init__(self, id: str, name: str, arguments: str):
        self.id = id
        self.type = "function"
        self.function = _Fn(name, arguments)


class _Msg:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _Choice:
    def __init__(self, message):
        self.message = message


class _Usage:
    def __init__(self, prompt_tokens: int, completion_tokens: int):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class _Response:
    def __init__(self, message, prompt_tokens: int):
        self.choices = [_Choice(message)]
        self.usage = _Usage(prompt_tokens, 300)


class _Completions:
    """Writes a large file every iteration and never declares, so the
    turn runs its whole budget and the transcript has to be managed.

    prompt_tokens is reported HONESTLY here, as the real character count
    over the seeded ratio. The failure being guarded against is a server
    that reports the pre-truncation count, and a fake that did the same
    would make these assertions vacuous - so the fake tells the truth
    and the trim has to keep it true.
    """

    def __init__(self):
        self.sent: list[dict] = []

    def create(self, **kwargs):
        # A SNAPSHOT, not the live list. The loop appends to the same
        # `messages` object between requests, so storing the reference
        # would make every recorded request look like the final one -
        # and the budget assertions below would then be measuring
        # nothing. Found the hard way.
        self.sent.append({**kwargs, "messages": copy.deepcopy(kwargs["messages"])})
        chars = len(json.dumps(kwargs["messages"], default=str)) + len(
            json.dumps(kwargs["tools"], default=str)
        )
        prompt_tokens = int(chars / openai_agent._CHARS_PER_TOKEN_SEED)
        if isinstance(kwargs.get("tool_choice"), dict):
            args = json.dumps(
                {
                    "trigger": "code_submission",
                    "narrative": "Wrote the test.",
                    "handoff_done": "Test written.",
                    "handoff_remaining": "Nothing.",
                    "handoff_notes_for_next": "None.",
                }
            )
            return _Response(_Msg(tool_calls=[_Call("d1", "declare_outcome", args)]), prompt_tokens)
        n = len(self.sent)
        args = json.dumps({"path": f"tests/test_{n}.py", "content": f"# attempt {n}\n" + "a" * 3000})
        return _Response(_Msg(tool_calls=[_Call(f"c{n}", "write_file", args)]), prompt_tokens)


class _Chat:
    def __init__(self, completions):
        self.completions = completions


class _Client:
    def __init__(self):
        self.chat = _Chat(_Completions())
        self.base_url = "http://fake/v1"


class InProgressLoopTest(unittest.TestCase):
    """The wiring, end to end. The unit tests above prove the trim
    produces a valid smaller transcript; this proves the loop actually
    calls it, and with the tool schemas counted in.
    """

    def _run(self, budget: int):
        from wjfyp.models.message import HandoffNote  # noqa: F401
        from wjfyp.models.ticket import Ticket, TicketStatus
        from wjfyp.orchestrator.agent import AgentContext
        from wjfyp.sandbox.fake import FakeSandboxController

        sandbox = FakeSandboxController()
        ticket = Ticket(
            id="TCK-1",
            title="Write a unit test for append_file",
            description="Cover the append behaviour.",
            status=TicketStatus.IN_PROGRESS,
        )
        context = AgentContext(
            ticket=ticket,
            channel_id="c1",
            role="engineering",
            instance_id="engineering-1",
            sandbox=sandbox,
        )
        client = _Client()
        a = OpenAIAgent(ROLE, "engineering-1", client=client)
        with patch.object(openai_agent, "CONTEXT_BUDGET_TOKENS", budget):
            a.invoke(context)
        return client.chat.completions.sent

    def test_no_request_in_a_long_turn_exceeds_the_budget(self):
        """The property that matters. Twelve iterations each writing a
        3,000-character file is the shape of the run that broke, and
        every request has to stay inside the window anyway."""
        budget = 6000
        sent = self._run(budget)
        self.assertGreater(len(sent), 5)
        for i, call in enumerate(sent):
            if isinstance(call.get("tool_choice"), dict):
                continue  # the forced final turn builds its own small context
            chars = len(json.dumps(call["messages"], default=str)) + len(
                json.dumps(call["tools"], default=str)
            )
            estimated = chars / openai_agent._CHARS_PER_TOKEN_SEED
            self.assertLessEqual(
                estimated, budget, f"request {i} estimated at {estimated:.0f} tokens over {budget}"
            )

    def test_without_the_trim_the_same_turn_would_have_overflowed(self):
        """Confirms the test above is not passing because the transcript
        was small anyway. At a budget high enough never to trim, the
        prompt grows past where the 6,000-token budget sat."""
        sent = self._run(10_000_000)
        last = [c for c in sent if not isinstance(c.get("tool_choice"), dict)][-1]
        chars = len(json.dumps(last["messages"], default=str)) + len(
            json.dumps(last["tools"], default=str)
        )
        self.assertGreater(chars / openai_agent._CHARS_PER_TOKEN_SEED, 6000)

    def test_the_persona_survives_every_request_of_a_long_turn(self):
        sent = self._run(6000)
        for call in sent:
            self.assertEqual(call["messages"][0]["role"], "system")
            self.assertIn("engineer", call["messages"][0]["content"].lower())

    def test_the_model_is_told_what_it_already_tried(self):
        sent = self._run(6000)
        later = [c for c in sent if not isinstance(c.get("tool_choice"), dict)][-1]
        ticket_turn = later["messages"][1]["content"]
        self.assertIn("EARLIER STEPS THIS TURN", ticket_turn)
        self.assertIn("write_file", ticket_turn)
