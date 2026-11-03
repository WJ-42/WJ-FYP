"""Rebuilding an assistant turn for replay has to produce something
every endpoint will accept, because the message stays in history: one
malformed entry does not fail one request, it fails every remaining
request in the turn.

Found on a real qwen3:4b run, 03/11/2026. qwen3 returns its text in a
`reasoning` field and leaves `content` empty, so an iteration where it
also made no usable tool call produced {"role": "assistant"} with
neither key. Ollama answers that with 400 "invalid message content
type: <nil>", and every later request in that turn then failed the
same way - a burst of refusals in milliseconds, an emptied iteration
budget, and a turn that appeared to do nothing.
"""

from __future__ import annotations

import unittest

from wjfyp.orchestrator.openai_agent import _assistant_message


class _Fn:
    def __init__(self, name: str, arguments: str):
        self.name = name
        self.arguments = arguments


class _Call:
    def __init__(self, id: str, name: str, arguments: str = "{}"):
        self.id = id
        self.type = "function"
        self.function = _Fn(name, arguments)


class _Msg:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class AssistantReplayTest(unittest.TestCase):
    def test_content_is_always_present_even_with_nothing_to_say(self):
        """The exact shape that broke it: no content, no tool calls."""
        out = _assistant_message(_Msg(content=None, tool_calls=None))
        self.assertIn("content", out)
        self.assertEqual(out["content"], "")

    def test_an_empty_string_content_also_survives(self):
        """Empty string is falsy, so a truthiness check dropped it just
        as readily as None did."""
        out = _assistant_message(_Msg(content="", tool_calls=None))
        self.assertEqual(out["content"], "")

    def test_content_is_present_when_every_call_is_filtered_out(self):
        """The other route to the same bare message: the model made
        calls, but all of them were hallucinated and kept out of
        history."""
        msg = _Msg(content=None, tool_calls=[_Call("c1", "run_file")])
        out = _assistant_message(msg, keep_call_ids=set())
        self.assertEqual(out["content"], "")
        self.assertNotIn("tool_calls", out)

    def test_real_content_is_not_clobbered(self):
        out = _assistant_message(_Msg(content="Reading the file now.", tool_calls=None))
        self.assertEqual(out["content"], "Reading the file now.")

    def test_kept_calls_still_replay_alongside_empty_content(self):
        msg = _Msg(content=None, tool_calls=[_Call("c1", "read_file", '{"path": "a.py"}')])
        out = _assistant_message(msg, keep_call_ids={"c1"})
        self.assertEqual(out["content"], "")
        self.assertEqual(out["tool_calls"][0]["function"]["name"], "read_file")
        self.assertEqual(out["tool_calls"][0]["id"], "c1")

    def test_provider_specific_fields_are_still_not_replayed(self):
        """The reason this builds field by field rather than dumping the
        model: a `reasoning` field replayed to a different endpoint is
        the kind of thing that works on one and 400s on the next."""
        msg = _Msg(content="hi", tool_calls=None)
        msg.reasoning = "a long internal monologue"  # type: ignore[attr-defined]
        self.assertEqual(set(_assistant_message(msg)), {"role", "content"})


if __name__ == "__main__":
    unittest.main()
