"""A real-money circuit breaker for ClaudeAgent's live API calls.

Built the same session as FYP-27/FYP-28's FSM-level retry/escalation
caps, but deliberately independent of them: those fix specific bugs in
the ticket lifecycle, while this guards against ANY runaway spend
(including bugs not yet found) by refusing to make another API call once
a caller-chosen dollar ceiling is reached. See cs3ip_budget_crunch
memory - the project's dedicated Anthropic Console account has a real,
finite balance, and a previous real run already burned significant spend
before anyone noticed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Any

# USD per million tokens (input, output), by the exact model id strings
# used in config/roles*.yaml. Source: the same figures used to size the
# dedicated Anthropic Console account for this project (see
# cs3ip_budget_crunch memory).
MODEL_PRICING_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
}

# Priced as the most expensive known tier (Opus) if a model id isn't in
# the table above - an unrecognised or future model still gets guarded
# rather than silently costing nothing against the cap. The safe
# direction for a failsafe is to overestimate, never to assume free.
_UNKNOWN_MODEL_PRICING_USD_PER_MTOK = MODEL_PRICING_USD_PER_MTOK["claude-opus-5"]


class BudgetExceededError(RuntimeError):
    """Raised by SpendGuard.check() once the tracked spend estimate has
    reached its cap. Callers should treat this as a hard stop - it isn't
    something to catch and retry past, only something to catch, report,
    and abort on.
    """


@dataclass
class SpendGuard:
    """Shared across every ClaudeAgent in a run (or a comparison of
    several runs via the same agent_pool_builder), so a runaway loop
    can't spend past a caller-chosen ceiling before a human ever sees it.
    This is a backstop independent of the FSM-level retry/escalation
    caps, not a replacement for them - it protects against any future
    runaway shape, known or not, not just the two already found.

    Cost estimation is deliberately conservative, not exact: every input
    token - base, cache-write, and cache-read alike - is priced at the
    model's full input rate, even though a real cache read actually
    bills far cheaper (see FYP-25's own prompt-caching work). This always
    overestimates true spend, the safe direction for a failsafe - the
    real Anthropic bill will never come in higher than what this
    tracked, only potentially lower. Thread-safe (a Lock guards the
    running total), since nothing here assumes single-threaded callers.
    """

    cap_usd: float
    spent_usd: float = 0.0
    _lock: Lock = field(default_factory=Lock, repr=False, compare=False)

    def check(self) -> None:
        """Call immediately before making an API call. Raises
        BudgetExceededError if the running estimate has already reached
        the cap - never lets a new call start once at or past the
        ceiling.
        """
        with self._lock:
            spent = self.spent_usd
        if spent >= self.cap_usd:
            raise BudgetExceededError(
                f"spend guard tripped: ${spent:.4f} spent >= ${self.cap_usd:.4f} cap "
                "- refusing to make another API call"
            )

    def record(self, model: str, usage: Any) -> None:
        """Call after an API call returns, with its real `usage` object.
        Never raises itself - a call that pushes spend over the cap is
        still allowed to finish; the next check() catches it before the
        following call.
        """
        input_rate, output_rate = MODEL_PRICING_USD_PER_MTOK.get(
            model, _UNKNOWN_MODEL_PRICING_USD_PER_MTOK
        )
        billable_input_tokens = (
            (usage.input_tokens or 0)
            + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
            + (getattr(usage, "cache_read_input_tokens", 0) or 0)
        )
        cost = (billable_input_tokens * input_rate + (usage.output_tokens or 0) * output_rate) / 1_000_000
        with self._lock:
            self.spent_usd += cost
