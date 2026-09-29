"""Unit tests for the real-money circuit breaker (SpendGuard) - no
network, no cost. See orchestrator/budget.py's own module docstring for
why this exists alongside (not instead of) FYP-27/FYP-28's FSM-level
retry/escalation caps.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from wjfyp.orchestrator.budget import BudgetExceededError, SpendGuard


def _usage(input_tokens=0, output_tokens=0, cache_creation_input_tokens=0, cache_read_input_tokens=0):
    return SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_input_tokens=cache_creation_input_tokens,
        cache_read_input_tokens=cache_read_input_tokens,
    )


class SpendGuardTest(unittest.TestCase):
    def test_check_passes_under_the_cap(self) -> None:
        guard = SpendGuard(cap_usd=5.0)
        guard.record("claude-sonnet-5", _usage(input_tokens=1000, output_tokens=1000))
        guard.check()  # must not raise

    def test_check_raises_once_the_cap_is_reached(self) -> None:
        guard = SpendGuard(cap_usd=1.0)
        # claude-opus-5: $5/$25 per MTok - 200,000 output tokens = $5.00.
        guard.record("claude-opus-5", _usage(output_tokens=200_000))
        with self.assertRaises(BudgetExceededError):
            guard.check()

    def test_record_never_raises_even_when_it_pushes_over_the_cap(self) -> None:
        """A call already in flight is allowed to finish and be recorded -
        the next check() is what refuses the FOLLOWING call, not this one
        retroactively.
        """
        guard = SpendGuard(cap_usd=0.01)
        guard.record("claude-opus-5", _usage(input_tokens=1_000_000))  # $5.00, way over
        self.assertGreater(guard.spent_usd, 0.01)

    def test_record_includes_cache_creation_and_cache_read_tokens_as_full_price_input(self) -> None:
        """The conservative-overestimate design: a real cache read bills
        far cheaper than a fresh input token, but this guard prices all
        three token kinds at the same full input rate rather than risk
        under-counting real spend - see SpendGuard's own docstring.
        """
        guard = SpendGuard(cap_usd=100.0)
        # claude-sonnet-5: $2/MTok input. 1,000,000 split across the three
        # input-token fields should cost the same as 1,000,000 plain
        # input tokens: $2.00.
        guard.record(
            "claude-sonnet-5",
            _usage(input_tokens=400_000, cache_creation_input_tokens=300_000, cache_read_input_tokens=300_000),
        )
        self.assertAlmostEqual(guard.spent_usd, 2.0, places=6)

    def test_unknown_model_falls_back_to_the_most_expensive_known_rate(self) -> None:
        guard_known = SpendGuard(cap_usd=100.0)
        guard_known.record("claude-opus-5", _usage(input_tokens=1000, output_tokens=1000))
        guard_unknown = SpendGuard(cap_usd=100.0)
        guard_unknown.record("some-future-model-id", _usage(input_tokens=1000, output_tokens=1000))
        self.assertAlmostEqual(guard_known.spent_usd, guard_unknown.spent_usd, places=6)

    def test_usage_object_without_cache_fields_is_handled(self) -> None:
        """A fake test client's usage stand-in often only has
        input_tokens/output_tokens (see test_claude_agent.py's
        _FakeUsage) - record() must not crash on that shape.
        """
        guard = SpendGuard(cap_usd=100.0)
        guard.record("claude-haiku-4-5", SimpleNamespace(input_tokens=100, output_tokens=50))
        self.assertGreater(guard.spent_usd, 0.0)


if __name__ == "__main__":
    unittest.main()
