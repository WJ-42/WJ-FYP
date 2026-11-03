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


class SelfHostedSpendTest(unittest.TestCase):
    """A run on hardware the user owns costs nothing, and the guard has
    to know that rather than infer it.

    The unknown-model fallback prices anything unrecognised at the Opus
    rate, which is the right direction for an unrecognised HOSTED model
    and the wrong one here. A real local comparison on qwen3:8b reported
    $0.3364 of spend that did not happen, and at the default $5 cap that
    aborts a 22-comparison batch around number 15: the failsafe killing
    a run to prevent a cost that does not exist.
    """

    def test_a_self_hosted_run_accrues_nothing(self):
        guard = SpendGuard(cap_usd=5.0, self_hosted=True)
        guard.record("qwen3:8b", _usage(input_tokens=500_000, output_tokens=200_000))
        self.assertEqual(guard.spent_usd, 0.0)
        guard.check()

    def test_a_long_local_batch_never_trips_the_cap(self):
        """The actual failure this prevents: 22 comparisons at the
        volume one real run used."""
        guard = SpendGuard(cap_usd=5.0, self_hosted=True)
        for _ in range(22):
            guard.record("qwen3:8b", _usage(input_tokens=60_000, output_tokens=60_000))
            guard.check()
        self.assertEqual(guard.spent_usd, 0.0)

    def test_the_same_usage_hosted_still_costs_and_still_guards(self):
        """The failsafe must be untouched for anything not self-hosted,
        including a model it does not recognise."""
        guard = SpendGuard(cap_usd=5.0)
        guard.record("qwen3:8b", _usage(input_tokens=500_000, output_tokens=200_000))
        self.assertGreater(guard.spent_usd, 0.0)
        with self.assertRaises(BudgetExceededError):
            guard.check()

    def test_self_hosted_defaults_to_false(self):
        """Free has to be asked for. A guard constructed the old way
        keeps guarding exactly as before."""
        self.assertFalse(SpendGuard(cap_usd=1.0).self_hosted)
