from __future__ import annotations

from typing import Callable

from pydantic import BaseModel, ConfigDict, Field

from wjfyp.sandbox.controller import SandboxController


class GradingContext(BaseModel):
    """What a checkpoint grader receives to assess a completed task run:
    the finished ticket's id, the host-side repo path (GitWorkspace.repo_path)
    for graders that want to inspect files/git-log directly, and the
    sandbox controller still open on that final state, if one is - mirrors
    AgentContext's shape (see wjfyp.orchestrator.agent) so graders and
    agents pull from the same kind of "what do you have access to" payload.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    ticket_id: str
    repo_path: str
    sandbox: SandboxController | None = None


GraderFn = Callable[[GradingContext], bool]


class Checkpoint(BaseModel):
    """One point-weighted, binary grading unit - ported from
    TheAgentCompany's evaluator.py `grade_checkpointN` functions (see
    cs3ip-evaluation-detail memory: confirmed via repo research that
    checkpoints are independent and unordered - grade_checkpoints() sums
    them with no dependency/gating between them - so this mirrors that
    rather than inventing an ordering their own harness doesn't have).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: str
    description: str
    points: int
    grader: GraderFn


class EvalTask(BaseModel):
    """One benchmark task: a raw requirement fed through the Intake phase
    (see cs3ip-comm-protocol memory), scored by its checkpoints once the
    run completes. `fail_to_pass`/`pass_to_pass` are pytest node ids and
    are only populated for tasks ported with SWE-bench-style hidden tests
    (see cs3ip-evaluation-detail memory) - wiring them into a live sandbox
    run is a later layer (FYP-19 Layer B); here they're just carried as
    task data. TheAgentCompany-derived tasks instead rely on arbitrary
    assertion checkpoints and leave these two lists empty.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    task_id: str
    title: str
    prompt: str
    checkpoints: list[Checkpoint]
    fail_to_pass: list[str] = Field(default_factory=list)
    pass_to_pass: list[str] = Field(default_factory=list)
    source: str = ""  # provenance, e.g. "TheAgentCompany:ds-janusgraph-exercise"

    @property
    def total_points(self) -> int:
        return sum(c.points for c in self.checkpoints)
