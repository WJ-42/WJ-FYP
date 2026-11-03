"""Run-to-run agreement, reported with its chance level.

FYP-57, from Bai, Zhang and Wang (MDT Agent, 2026). Repeating a task and
counting how often the runs land on the same outcome looks like a
reliability measure and is not one. Their personas raised full agreement
across three runs from 50% to 85-90% of cases while Fleiss kappa did not
move at all (0.31 with no persona, 0.20 and 0.31 with them), because the
chance level implied by those personas' own outcome distributions was
already 81-86%. A condition that nearly always says the same thing
agrees with itself nearly always, and that is arithmetic rather than
evidence.

So this module deliberately has no public function returning raw
agreement on its own. `measure_agreement` returns all three numbers
together in one `AgreementReport`, because the raw figure is only
interpretable next to the other two. Quoting it alone in the report
would make exactly the claim the supervisor's own paper exists to
refute.

Nothing here is specific to this project's outcomes. An "outcome" is any
hashable label: an FSM trigger, a pass/fail verdict, a rubric band. The
inputs are per-item lists of the outcomes seen across that item's
repeated runs.
"""

from __future__ import annotations

from collections import Counter
from typing import Hashable, Mapping, Sequence

from pydantic import BaseModel


class AgreementReport(BaseModel):
    """Every number needed to read run-to-run agreement honestly.

    `excess_agreement` is the headline: how far observed full agreement
    sits above what the outcome distribution alone would produce. A
    condition can show 90% agreement and near-zero excess, which is the
    trap this module exists to make visible.

    `kappa` is None when it is genuinely undefined rather than zero.
    That happens when every run of every item produced the same single
    outcome: chance agreement is then 1.0, the denominator vanishes, and
    the honest answer is "this tells you nothing", not "perfect".
    """

    items: int
    runs_per_item: dict[int, int]
    outcome_distribution: dict[str, float]
    full_agreement: float
    chance_full_agreement: float
    excess_agreement: float
    kappa: float | None
    kappa_undefined_reason: str | None = None

    def summary(self) -> str:
        """One line fit to paste into a report or a meeting note, with
        the caveat attached rather than left to the reader.
        """
        kappa = "undefined" if self.kappa is None else f"{self.kappa:.2f}"
        return (
            f"full agreement {self.full_agreement:.0%} "
            f"(chance {self.chance_full_agreement:.0%}, "
            f"excess {self.excess_agreement:+.0%}), Fleiss kappa {kappa}, "
            f"over {self.items} items"
        )


def _validate(runs_by_item: Mapping[str, Sequence[Hashable]]) -> None:
    if not runs_by_item:
        raise ValueError("no items given - agreement over nothing is not defined")
    thin = [item for item, runs in runs_by_item.items() if len(runs) < 2]
    if thin:
        raise ValueError(
            f"items with fewer than 2 runs cannot show agreement: {sorted(thin)[:5]}"
        )


def measure_agreement(runs_by_item: Mapping[str, Sequence[Hashable]]) -> AgreementReport:
    """Measures how far repeated runs agree beyond chance.

    `runs_by_item` maps an item (a task id, a case id) to the outcomes
    its repeated runs produced, e.g.
    {"sde-fix-factual-mistake": ["approved", "approved", "changes_requested"]}.

    Items may have different numbers of runs. Fleiss kappa handles that
    natively, since each item's observed agreement is computed against
    its own run count, and the chance level is averaged per item for the
    same reason.
    """
    _validate(runs_by_item)

    labels = {str(outcome) for runs in runs_by_item.values() for outcome in runs}
    counts_by_item = {item: Counter(str(o) for o in runs) for item, runs in runs_by_item.items()}
    runs_per_item = {item: sum(c.values()) for item, c in counts_by_item.items()}
    total_runs = sum(runs_per_item.values())

    # p_j, the share of all runs that landed on each outcome. This is
    # what drives the chance level: a condition that picks one outcome
    # 95% of the time will agree with itself about 90% of the time
    # whatever else is true of it.
    marginals = {
        label: sum(c[label] for c in counts_by_item.values()) / total_runs for label in sorted(labels)
    }

    observed = _observed_agreement(counts_by_item, runs_per_item)
    chance = sum(p * p for p in marginals.values())

    # Expected rate of UNANIMOUS runs, which is the quantity the raw
    # "how often did all three runs agree" figure is reporting. Computed
    # per item because items may have different run counts: with n runs
    # and outcome shares p_j, all n runs coincide by chance with
    # probability sum_j p_j^n.
    chance_full = sum(
        sum(p**n for p in marginals.values()) for n in runs_per_item.values()
    ) / len(runs_per_item)
    full = sum(1 for c in counts_by_item.values() if len(c) == 1) / len(counts_by_item)

    kappa: float | None
    reason: str | None = None
    if chance >= 1.0:
        kappa = None
        reason = (
            f"every run produced the same outcome ({next(iter(marginals))!r}), so chance "
            "agreement is already 1.0 and kappa has no denominator. The runs agreeing "
            "says nothing about reliability here."
        )
    else:
        kappa = (observed - chance) / (1.0 - chance)

    return AgreementReport(
        items=len(runs_by_item),
        runs_per_item=dict(Counter(runs_per_item.values())),
        outcome_distribution=marginals,
        full_agreement=full,
        chance_full_agreement=chance_full,
        excess_agreement=full - chance_full,
        kappa=kappa,
        kappa_undefined_reason=reason,
    )


def _observed_agreement(
    counts_by_item: Mapping[str, Counter], runs_per_item: Mapping[str, int]
) -> float:
    """Mean P_i over items, the pairwise-agreement term in Fleiss kappa.

    For one item with n runs and n_ij runs in each outcome j, the
    proportion of agreeing run PAIRS is
    (sum_j n_ij^2 - n) / (n(n - 1)). Averaging that over items gives
    P-bar. Pairs rather than unanimity is what makes kappa degrade
    gracefully: two of three runs agreeing is partial credit, not zero.
    """
    per_item = []
    for item, counts in counts_by_item.items():
        n = runs_per_item[item]
        squared = sum(c * c for c in counts.values())
        per_item.append((squared - n) / (n * (n - 1)))
    return sum(per_item) / len(per_item)
