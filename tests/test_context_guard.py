"""A server that truncates silently is worse than one that refuses.

Groq answered an oversized In Progress request with 413 Request too
large. A self-hosted Ollama server answers it with 200 OK and a reply
computed from a prompt whose FRONT has been discarded - and the front
is the system prompt and the role persona. Measured 03/11/2026 against
qwen3:8b with a marker planted at each end of the prompt: over the
window the model quoted the back marker and not the front one, while
usage.prompt_tokens reported 2,050 of the ~12,000 tokens sent.

For a project whose independent variable is the persona, that is
measuring nothing while producing data that looks fine, so these tests
pin the detection rather than the recovery. There is no recovery.
"""

from __future__ import annotations

import unittest

from wjfyp.models.agent import RoleConfig
from wjfyp.orchestrator.openai_agent import ContextWindowExceeded, OpenAIAgent

ROLE = RoleConfig(id="engineering", name="Engineer", tier=3, model="qwen3:8b", count=1)


def agent() -> OpenAIAgent:
    return OpenAIAgent(ROLE, "engineering-1", client=object())


class ContextGuardTest(unittest.TestCase):
    def test_a_plateaued_prompt_under_a_growing_transcript_is_truncation(self):
        """The real signature. At the ceiling the reported prompt stops
        moving no matter how much goes in, so the plateau is the signal
        - NOT a decrease, which never happens because the window caps
        the number rather than lowering it.
        """
        with self.assertRaises(ContextWindowExceeded) as caught:
            agent()._check_context_held(
                prompt_tokens=2050, last_prompt_tokens=2048, appended_chars=4000
            )
        message = str(caught.exception)
        self.assertIn("4,000 characters were appended", message)
        self.assertIn("2,048 to 2,050 tokens", message)
        self.assertIn("OLLAMA_CONTEXT_LENGTH", message)

    def test_a_healthy_loop_does_not_trip_it(self):
        """2,000 characters of tool result landing as ~450 tokens is
        normal English at ~4.4 chars per token. If this fired, every
        real run would die on its second iteration.
        """
        agent()._check_context_held(
            prompt_tokens=2500, last_prompt_tokens=2050, appended_chars=2000
        )

    def test_it_tolerates_a_tokenizer_half_as_dense_as_expected(self):
        """The threshold is appended/8, about half the growth the
        least dense plausible tokenizer would give. A guard that fired
        on an honest tokenizer difference would be worse than no guard,
        because it would be switched off.
        """
        agent()._check_context_held(
            prompt_tokens=1_300, last_prompt_tokens=1_000, appended_chars=2_400
        )

    def test_the_first_iteration_has_nothing_to_compare_against(self):
        agent()._check_context_held(prompt_tokens=1163, last_prompt_tokens=0, appended_chars=9999)

    def test_a_tiny_append_cannot_trip_it(self):
        """A one-line nudge appended to the transcript genuinely may not
        move the token count much, so small appends are not evidence."""
        agent()._check_context_held(
            prompt_tokens=2050, last_prompt_tokens=2050, appended_chars=120
        )

    def test_the_measured_overflow_is_caught(self):
        """The actual numbers from the 03/11/2026 probe: ~12,000 tokens
        of prompt sent, 2,050 reported, repeatedly."""
        with self.assertRaises(ContextWindowExceeded):
            agent()._check_context_held(
                prompt_tokens=2050, last_prompt_tokens=2050, appended_chars=40_000
            )


if __name__ == "__main__":
    unittest.main()
