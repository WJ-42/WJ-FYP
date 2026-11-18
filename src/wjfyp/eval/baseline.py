from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path

from pydantic import BaseModel

from wjfyp.config import load_roles
from wjfyp.eval.scoring import TaskScore
from wjfyp.eventlog import EventLog
from wjfyp.models.agent import RoleConfig
from wjfyp.orchestrator.agent import Agent, AgentContext, AgentResponse

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


# The working style the solo agent is given in place of a team persona.
#
# The two team arms differ only in their persona text (deference in the
# hierarchical file, peer negotiation in the flat one), so the persona IS
# the organisational structure in this harness. A baseline with no
# organisation should therefore carry no hierarchy or peer framing at all.
# It is told one plain fact instead, that it is the whole team, because
# every stage prompt talks about handing work to "the next agent" and an
# agent that believes someone else will check its work has a reason to
# hold back. The stage instructions themselves (_ROLE_RESPONSIBILITIES in
# claude_agent.py) are left exactly as the teams get them, so what the
# agent is asked to do at each FSM state does not change between arms.
SOLO_PERSONALITY = (
    "You are the only member of this team. You play every role on this ticket "
    "yourself, one stage at a time, so the CTO, Product and the Engineer are all "
    "you. Handoff notes you write are read by you at the next stage, and nobody "
    "else will review or fix your work."
)


class SoloAgent:
    """One agent instance that plays every role, for single_agent_pool.

    The real agents (OpenAIAgent, ClaudeAgent) take their stage
    instructions from the RoleConfig they were built with, not from
    AgentContext.role. Registering one of them under every role as it
    stands would make it answer every stage as whichever role it was
    built for, so an agent built as the CTO would reach In Progress
    without ever being told to commit. This wrapper points the single
    inner agent at the RoleConfig for the role it is acting as this turn
    and then delegates, so there is still exactly one instance, one
    client and one instance id across the whole run.
    """

    def __init__(self, inner: Agent, role_configs: dict[str, RoleConfig], instance_id: str = "solo-1"):
        self._inner = inner
        self._role_configs = role_configs
        self.role = "solo"
        self.instance_id = instance_id
        inner.instance_id = instance_id

    def invoke(self, context: AgentContext) -> AgentResponse:
        role_config = self._role_configs[context.role]
        # Both concrete agents read _role_config for the prompt and the
        # model, and `role` for the sender on what they log.
        self._inner._role_config = role_config  # type: ignore[attr-defined]
        self._inner.role = role_config.id
        return self._inner.invoke(context)


def build_solo_pool(
    roles_path: Path,
    event_log: EventLog | None,
    team_pool_builder: Callable[[Path, EventLog | None], dict[str, list[Agent]]],
) -> dict[str, list[Agent]]:
    """The single-agent baseline built from the same roles file and the
    same pool builder as a team arm, so the backend, client, timeouts,
    retry budget, spend guard and any --model override are exactly the
    ones a team agent gets. Builds the team, keeps its first agent as the
    solo agent's engine and discards the rest. Constructing an agent
    makes no API call, so the discarded ones cost nothing.

    Every role keeps its id, name and tier from the file (so the stage
    instructions are unchanged), takes the model the kept agent was
    actually built with, and swaps the team persona for SOLO_PERSONALITY.
    """
    team = team_pool_builder(roles_path, event_log)
    first_role = next(iter(team))
    inner = team[first_role][0]
    model = inner._role_config.model  # type: ignore[attr-defined]
    role_configs = {
        r.id: r.model_copy(update={"model": model, "personality": SOLO_PERSONALITY, "count": 1})
        for r in load_roles(roles_path)
    }
    solo = SoloAgent(inner, role_configs)
    return single_agent_pool(solo, roles=role_configs.keys())


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
