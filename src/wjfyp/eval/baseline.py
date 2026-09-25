from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel

from wjfyp.eval.scoring import TaskScore
from wjfyp.orchestrator.agent import Agent

DEFAULT_ROLES = ("cto", "product", "engineering")


def single_agent_pool(agent: Agent, roles: Iterable[str] = DEFAULT_ROLES) -> dict[str, list[Agent]]:
    """Build the `agents` dict shape orchestrator.loop.run() expects,
    with one Agent instance playing every role - the baseline
    configuration confirmed in cs3ip-comm-protocol memory: "the
    single-agent baseline reuses the identical Message/Ticket/Sandbox
    infrastructure with one agent playing all roles - not a second
    system to maintain." No orchestrator changes needed for this: step()
    /run() just look up agents[role] each turn, and every role here
    resolves to the same instance, so the FSM, sandbox, and event log
    are exercised exactly as they are for the real multi-agent team.
    """
    return {role: [agent] for role in roles}


class BaselineComparison(BaseModel):
    task_id: str
    multi_agent_score: TaskScore
    baseline_score: TaskScore
    score_delta: float  # multi_agent_score.score - baseline_score.score
    multi_agent_better: bool


def compare_to_baseline(multi_agent_score: TaskScore, baseline_score: TaskScore) -> BaselineComparison:
    """Compare a multi-agent team's score against the single-agent
    baseline's score on the same task - the core comparison this
    project's premise depends on being able to make (see
    cs3ip-fyp-overview memory: showing organizational dynamics matter,
    not just that the system produces code).
    """
    if multi_agent_score.task_id != baseline_score.task_id:
        raise ValueError(
            "comparing scores for different tasks: "
            f"{multi_agent_score.task_id!r} vs {baseline_score.task_id!r}"
        )
    delta = multi_agent_score.score - baseline_score.score
    return BaselineComparison(
        task_id=multi_agent_score.task_id,
        multi_agent_score=multi_agent_score,
        baseline_score=baseline_score,
        score_delta=delta,
        multi_agent_better=delta > 0,
    )
