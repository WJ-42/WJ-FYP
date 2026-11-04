"""A timeout must cost an iteration, not a run.

On 04/11/2026 a 22-run batch died with zero scored runs. Both attempts
ended on openai.APITimeoutError, 68 and 19 minutes of completed work
discarded, and the inference server answered a test request in three
seconds immediately afterwards. It had never been at fault.

The arithmetic nobody had done:

    qwen3:8b generates 7.5 tokens/sec on realistic ~10k-token prompts,
      measured on the serving machine itself, because ~36% of the model
      runs on the CPU at a 16384 context on a 6 GB card
    MAX_OUTPUT_TOKENS is 4096
    4096 / 7.5 + 14s prompt read = 560 seconds
    REQUEST_TIMEOUT was 90 seconds

So any response over roughly 670 tokens could not finish, and this
agent routinely writes 2,000+ token turns. The 90 seconds was chosen
for Groq, where generation is about ten times faster, and was then
applied to a self-hosted endpoint where it was structurally impossible
to meet.

Two defences, and these tests pin both: the timeout is now derived from
the model's actual speed, and a timeout that still happens costs one
iteration instead of the run.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from openai import APIConnectionError, APITimeoutError

from wjfyp.orchestrator import openai_agent
from wjfyp.orchestrator.openai_agent import (
    DEFAULT_BASE_URL,
    MAX_OUTPUT_TOKENS,
    _client_settings,
)

# Measured on the SERVING machine on 04/11/2026 under real conditions.
# A laptop-side probe using short prompts reported 17.6 and was
# misleading: it never paid the CPU-offload cost that a long prompt does.
QWEN3_8B_TOKENS_PER_SECOND = 7.5


class ClientSettingsTest(unittest.TestCase):
    def test_a_self_hosted_endpoint_gets_the_long_timeout(self):
        timeout, retries = _client_settings("http://10.126.129.12:11434/v1")
        self.assertGreater(timeout.read, 300)
        self.assertLessEqual(retries, 2)

    def test_the_hosted_endpoint_keeps_its_own_numbers(self):
        """Groq is fast but rate limited, so it wants the opposite
        settings. Fixing one backend must not break the other."""
        timeout, retries = _client_settings(DEFAULT_BASE_URL)
        self.assertEqual(timeout.read, 90.0)
        self.assertEqual(retries, openai_agent.MAX_RETRIES)

    def test_the_timeout_can_actually_fit_a_full_length_generation(self):
        """The test that would have caught this before it cost a night.

        A timeout shorter than the time the model needs to emit
        max_tokens is not a timeout, it is a guarantee of failure for
        every long answer.
        """
        timeout, _ = _client_settings("http://localhost:11434/v1")
        needed = MAX_OUTPUT_TOKENS / QWEN3_8B_TOKENS_PER_SECOND
        self.assertGreater(
            timeout.read,
            needed,
            f"{timeout.read}s cannot fit {MAX_OUTPUT_TOKENS} tokens at "
            f"{QWEN3_8B_TOKENS_PER_SECOND} tok/s, which needs {needed:.0f}s",
        )

    def test_the_old_90_seconds_would_fail_this(self):
        """Pins the bug itself, so the regression is explicit rather than
        implied by the test above passing."""
        needed = MAX_OUTPUT_TOKENS / QWEN3_8B_TOKENS_PER_SECOND
        self.assertLess(90.0, needed)

    def test_retries_cannot_outlast_the_batch_stall_watchdog(self):
        """The supervisor kills a run after 30 minutes with no event log
        write. Timeout x (retries + 1) has to stay under that, or a run
        that is merely retrying gets killed as hung."""
        timeout, retries = _client_settings("http://10.126.129.12:11434/v1")
        worst_case_minutes = timeout.read * (retries + 1) / 60
        self.assertLess(worst_case_minutes, 30)

    def test_the_timeout_is_overridable_without_editing_code(self):
        """It is a property of the model's speed and the output cap, so
        it has to move when either does."""
        with patch.object(openai_agent, "SELF_HOSTED_REQUEST_TIMEOUT", openai_agent.httpx.Timeout(999.0, connect=10.0)):
            timeout, _ = _client_settings("http://elsewhere:11434/v1")
            self.assertEqual(timeout.read, 999.0)


class _Fn:
    def __init__(self, name, arguments):
        self.name, self.arguments = name, arguments


class _Call:
    def __init__(self, id, name, arguments):
        self.id, self.type, self.function = id, "function", _Fn(name, arguments)


class _Msg:
    def __init__(self, content=None, tool_calls=None):
        self.content, self.tool_calls = content, tool_calls


class _Usage:
    def __init__(self, prompt_tokens):
        # Must GROW. A constant prompt size makes _check_context_held
        # declare the transcript truncated and raise, which masked the
        # timeout path these tests exist to exercise.
        self.prompt_tokens, self.completion_tokens = prompt_tokens, 300


class _Response:
    def __init__(self, message, prompt_tokens):
        self.choices = [type("C", (), {"message": message})()]
        self.usage = _Usage(prompt_tokens)


DECLARE = {
    "trigger": "code_submission",
    "narrative": "Wrote the test.",
    "handoff_done": "done",
    "handoff_remaining": "none",
    "handoff_notes_for_next": "none",
}


class _Completions:
    """Times out `fail_first` times, then behaves."""

    def __init__(self, fail_first, exc=APITimeoutError):
        self.calls = 0
        self.worked = 0
        self.fail_first = fail_first
        self.exc = exc

    def create(self, **kwargs):
        self.calls += 1
        if self.calls <= self.fail_first:
            raise self.exc(request=None)  # type: ignore[arg-type]
        import json as _json

        self.worked += 1
        pt = 500 + self.calls * 400
        # Commit before declaring. A code_submission with no successful
        # git_commit is rejected by verify_declaration and sent back, so
        # a fake that skipped this would burn all twelve iterations and
        # land on the forced final turn - a pass, but not a test of the
        # timeout path.
        if self.worked == 1:
            return _Response(
                _Msg(tool_calls=[_Call("c1", "git_commit", _json.dumps({"message": "work"}))]), pt
            )
        return _Response(
            _Msg(tool_calls=[_Call("d1", "declare_outcome", _json.dumps(DECLARE))]), pt
        )


class _Client:
    def __init__(self, completions):
        self.chat = type("Chat", (), {"completions": completions})()
        self.base_url = "http://fake/v1"


def _run(fail_first, exc=APITimeoutError):
    from wjfyp.models.agent import RoleConfig
    from wjfyp.models.ticket import Ticket, TicketStatus
    from wjfyp.orchestrator.agent import AgentContext
    from wjfyp.orchestrator.openai_agent import OpenAIAgent
    from wjfyp.sandbox.fake import FakeSandboxController

    role = RoleConfig(id="engineering", name="Eng", tier=3, model="qwen3:8b", count=1)
    comp = _Completions(fail_first, exc)
    agent = OpenAIAgent(role, "engineering-1", client=_Client(comp))
    ticket = Ticket(
        id="TCK-1", title="t", description="d", status=TicketStatus.IN_PROGRESS
    )
    ctx = AgentContext(
        ticket=ticket,
        channel_id="c1",
        role="engineering",
        instance_id="engineering-1",
        sandbox=FakeSandboxController(),
    )
    return agent.invoke(ctx), comp


class TimeoutRecoveryTest(unittest.TestCase):
    def test_one_timeout_does_not_lose_the_turn(self):
        """The whole point. A slow generation costs an iteration."""
        response, comp = _run(fail_first=1)
        self.assertEqual(response.trigger, "code_submission")
        # one timeout, then the commit, then the declaration
        self.assertEqual(comp.calls, 3)
        self.assertNotIn("under protest", response.message.content.text.lower())

    def test_two_timeouts_in_a_row_still_recover(self):
        response, comp = _run(fail_first=2)
        self.assertEqual(response.trigger, "code_submission")

    def test_a_connection_error_is_treated_the_same(self):
        """Same class of fault from the client's point of view: nothing
        came back and the transcript is unchanged."""
        response, _ = _run(fail_first=1, exc=APIConnectionError)
        self.assertEqual(response.trigger, "code_submission")

    def test_persistent_timeouts_do_raise(self):
        """Recovering forever would hide a dead server behind a turn that
        quietly did nothing, which is the failure this project keeps
        trying to avoid."""
        with self.assertRaises(RuntimeError) as caught:
            _run(fail_first=99)
        message = str(caught.exception)
        self.assertIn("timed out", message)
        self.assertIn("SELF_HOSTED_TIMEOUT_SECONDS", message)

    def test_the_cap_is_consecutive_not_cumulative(self):
        """A timeout, a success, then more timeouts is a slow server, not
        a dead one. Counting cumulatively would kill a long turn that was
        making real progress."""
        from wjfyp.orchestrator.openai_agent import MAX_CONSECUTIVE_TIMEOUTS

        self.assertGreaterEqual(MAX_CONSECUTIVE_TIMEOUTS, 2)
        response, _ = _run(fail_first=MAX_CONSECUTIVE_TIMEOUTS - 1)
        self.assertEqual(response.trigger, "code_submission")


if __name__ == "__main__":
    unittest.main()
