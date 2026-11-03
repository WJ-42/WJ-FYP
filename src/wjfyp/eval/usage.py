"""Tokens and cost, broken down by role and by model.

FYP-61. The poster claimed a per-run cost that could not be backed up
(see FYP-53), because the only figure ever recorded was one undifferentiated
total. A single total is the wrong unit for this project: the whole
premise is a team whose tiers run on different models, so summing
Opus-rate and Haiku-rate tokens produces a number that answers no
question. The interesting claims are per role and per structure - what
the CTO tier costs against what Engineering costs, and whether the
hierarchical team is more expensive than the flat one.

It is also the before half of the open-models comparison. Moving to
Groq (FYP-60) only means something if the Anthropic baseline was
measured first, and that measurement cannot be recovered after the fact
because the event logs from the early runs were not kept.

Cost reuses budget.MODEL_PRICING_USD_PER_MTOK rather than a second
table. That table is the spend guard's, which deliberately overestimates
by pricing every input token at the full rate with no discount for cache
reads, so a figure here is an upper bound on real spend, not an invoice.
Reporting it as "at most" is honest; reporting it as actual is not.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from pydantic import BaseModel

from wjfyp.models.message import Message
from wjfyp.orchestrator.budget import MODEL_PRICING_USD_PER_MTOK

# Tokens whose model was not recorded. Event logs written before
# TokenCost.model existed still parse, and their counts are real, so
# they are reported under this label rather than dropped or priced.
# Silently costing them at zero would quietly understate a run.
UNATTRIBUTED = "unrecorded"


class UsageTotals(BaseModel):
    """Tokens and an upper-bound cost for one slice of a run."""

    prompt: int = 0
    completion: int = 0
    messages: int = 0
    cost_usd: float | None = None

    @property
    def total(self) -> int:
        return self.prompt + self.completion


class UsageReport(BaseModel):
    """A run's token use, sliced the three ways that get asked about.

    `by_model` is what cost is actually derived from. `by_role` is what
    the hierarchy argument needs. `by_role_and_model` is the join, kept
    because a role can move between models across conditions and the
    other two views would hide that.

    `cost_usd` is None when no priced model accounts for any of the
    tokens, which is different from zero. Zero is a real free-tier run;
    None means the question cannot be answered from this log.
    """

    total: UsageTotals
    by_role: dict[str, UsageTotals]
    by_model: dict[str, UsageTotals]
    by_role_and_model: dict[str, UsageTotals]
    unpriced_models: list[str]

    def summary(self) -> str:
        cost = "cost unknown" if self.total.cost_usd is None else f"at most ${self.total.cost_usd:.4f}"
        parts = ", ".join(
            f"{role} {t.total:,}" for role, t in sorted(self.by_role.items(), key=lambda kv: -kv[1].total)
        )
        return f"{self.total.total:,} tokens ({parts}), {cost}"


def _cost_usd(model: str, prompt: int, completion: int) -> float | None:
    pricing = MODEL_PRICING_USD_PER_MTOK.get(model)
    if pricing is None:
        return None
    input_rate, output_rate = pricing
    return (prompt * input_rate + completion * output_rate) / 1_000_000


def _add(bucket: UsageTotals, prompt: int, completion: int, cost: float | None) -> None:
    bucket.prompt += prompt
    bucket.completion += completion
    bucket.messages += 1
    if cost is not None:
        bucket.cost_usd = (bucket.cost_usd or 0.0) + cost


def summarise_usage(messages: Iterable[Message]) -> UsageReport:
    """Aggregates the token costs already carried on each message.

    Nothing new is measured here. Every agent turn already records its
    own prompt/completion counts on the message it produces, so this
    reads what the run wrote rather than re-estimating it, and the same
    function therefore works on a live event log and on one from months
    ago.

    Messages with no token_cost are skipped rather than counted as
    zero-token messages: tool_call and tool_result rows carry no cost of
    their own, and letting them inflate the message counts would make
    "tokens per message" meaningless.
    """
    total = UsageTotals()
    by_role: dict[str, UsageTotals] = defaultdict(UsageTotals)
    by_model: dict[str, UsageTotals] = defaultdict(UsageTotals)
    by_both: dict[str, UsageTotals] = defaultdict(UsageTotals)
    unpriced: set[str] = set()

    for message in messages:
        cost_record = message.token_cost
        if cost_record is None:
            continue
        model = cost_record.model or UNATTRIBUTED
        role = message.sender.role
        cost = _cost_usd(model, cost_record.prompt, cost_record.completion)
        if cost is None:
            unpriced.add(model)

        for bucket in (total, by_role[role], by_model[model], by_both[f"{role} / {model}"]):
            _add(bucket, cost_record.prompt, cost_record.completion, cost)

    return UsageReport(
        total=total,
        by_role=dict(by_role),
        by_model=dict(by_model),
        by_role_and_model=dict(by_both),
        unpriced_models=sorted(unpriced),
    )
