from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from pydantic import BaseModel

from wjfyp.config import Settings
from wjfyp.eval.rubric import RubricJudge, RubricScore
from wjfyp.eval.runner import EvalRunResult, run_eval_task
from wjfyp.eval.task import EvalTask
from wjfyp.eventlog import EventLog
from wjfyp.orchestrator.claude_agent import build_agent_pool
from wjfyp.orchestrator.loop import HiddenTestLookup, SandboxFactory
from wjfyp.sandbox.docker_controller import DockerAttemptSandbox
from wjfyp.sandbox.git_workspace import GitWorkspace

# Not an extension of eval/baseline.py: that file is specifically the
# single-agent-vs-team comparison (compare_to_baseline, BaselineComparison),
# with field names (multi_agent_*/baseline_*) that shouldn't be repurposed
# for this. This is a structurally different thing - two full team
# configs, two separate orchestrator runs, two separate git workspaces -
# see cs3ip-fyp-overview memory's "Evaluation direction confirmed"
# entry for why this comparison exists (Option B).


class ConditionSpec(BaseModel):
    """A caller-chosen label paired with which roles file to load - e.g.
    label="hierarchical", roles_path=Path("config/roles.yaml").
    """

    label: str
    roles_path: Path


class ConditionResult(BaseModel):
    """One team-config's full result for one task: the deterministic
    orchestrator run/score result, and - if a judge was supplied - the
    independent rubric score for that condition's diff.
    """

    label: str
    run: EvalRunResult
    rubric: RubricScore | None = None


class StructureComparison(BaseModel):
    """Compares two ConditionResults for the same task. Deliberately
    generic (label/run/rubric per side), not "hierarchical"/"flat"
    baked into the field names - this is for any two team configs;
    hierarchical-vs-flat is just the first concrete use.
    """

    task_id: str
    condition_a: ConditionResult
    condition_b: ConditionResult
    score_delta: float  # condition_a.run.score.score - condition_b.run.score.score
    condition_a_better: bool  # score_delta > 0 - a tie does not count as "better"


def compare_structures(condition_a: ConditionResult, condition_b: ConditionResult) -> StructureComparison:
    """Pure diff of two already-computed ConditionResults - the
    StructureComparison counterpart to eval.baseline.compare_to_baseline,
    same shape and same signature style (validates matching task_id,
    computes delta, ties count as not-better).
    """
    if condition_a.run.task_id != condition_b.run.task_id:
        raise ValueError(
            "comparing results for different tasks: "
            f"{condition_a.run.task_id!r} vs {condition_b.run.task_id!r}"
        )
    delta = condition_a.run.score.score - condition_b.run.score.score
    return StructureComparison(
        task_id=condition_a.run.task_id,
        condition_a=condition_a,
        condition_b=condition_b,
        score_delta=delta,
        condition_a_better=delta > 0,
    )


def _default_sandbox_factory_builder(workspace: GitWorkspace) -> SandboxFactory:
    return lambda ticket: DockerAttemptSandbox(ticket, workspace)


def _clone_repo(template_repo_path: Path, dest: Path) -> None:
    """A plain local git clone duplicating a fixture repo for test
    isolation - not the deferred "attach to a remote repo" user-facing
    feature GitWorkspace's own docstring mentions, which is about a
    different, not-yet-built flow. This just needs two independent
    working trees so the two conditions can't interfere with each
    other's git state (see the module-level isolation note below).
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", str(template_repo_path), str(dest)], check=True, capture_output=True)


def run_structure_comparison(
    task: EvalTask,
    condition_a: ConditionSpec,
    condition_b: ConditionSpec,
    template_repo_path: Path,
    work_dir: Path,
    settings: Settings | None = None,
    judge: RubricJudge | None = None,
    hidden_tests: HiddenTestLookup | None = None,
    sandbox_factory_builder: Callable[[GitWorkspace], SandboxFactory] | None = None,
    agent_pool_builder: Callable[[Path, EventLog], dict] | None = None,
) -> StructureComparison:
    """Run one EvalTask through two team configs end to end, each in its
    own cloned repo / GitWorkspace / EventLog, and diff the results.

    Isolation: each condition gets its own repo directory (cloned from
    `template_repo_path`) and its own EventLog file under
    `work_dir/<label>/`, rather than sharing one GitWorkspace or one
    EventLog between conditions. Two reasons this matters, both
    confirmed by reading the actual GitWorkspace/DockerAttemptSandbox
    code rather than assumed: GitWorkspace.merge() checks out `base`
    before merging, so two conditions finishing concurrently against a
    *shared* workspace would race on which branch is checked out; and
    run_eval_task builds `Ticket(id=task.task_id, ...)` directly from
    the task id, so running the identical id through two conditions in
    one shared EventLog would collide. Separate EventLog files avoid
    needing any ticket-id-suffixing scheme, at the cost of not getting a
    single shared live dashboard view across both conditions - this is a
    batch/report-style comparison, not an intervention-mode live-watch
    tool, so that tradeoff is fine here.

    Calls judge.score() at most once per condition (2 calls total for a
    2-condition comparison), never more, and only after that condition's
    run has already fully finished.

    `agent_pool_builder` defaults to build_agent_pool (real ClaudeAgent
    instances from a roles.yaml path) - injectable the same way
    `sandbox_factory_builder` is, so tests can supply scripted agents
    per condition without needing Docker, an API key, or the real
    config/roles*.yaml files at all.
    """
    build_factory = sandbox_factory_builder or _default_sandbox_factory_builder
    build_pool = agent_pool_builder or build_agent_pool
    # Docker bind mounts require an absolute host path - DockerAttemptSandbox
    # binds workspace.repo_path directly (docker_controller.py), and a
    # relative path there isn't just wrong, Docker silently reinterprets it
    # as a *named volume* instead of a bind mount and fails with a cryptic
    # "invalid characters for a local volume name" error. Resolving once
    # here, rather than trusting every caller (including the CLI's own
    # relative default, Path("data/comparisons")) to pass an absolute path,
    # is the one choke point that actually prevents this - found by hitting
    # it for real on the first live run.
    work_dir = Path(work_dir).resolve()
    results: dict[str, ConditionResult] = {}

    for spec in (condition_a, condition_b):
        repo_dir = work_dir / spec.label
        _clone_repo(template_repo_path, repo_dir)
        workspace = GitWorkspace(repo_dir)
        event_log = EventLog(work_dir / spec.label / "eventlog.db")
        try:
            agents = build_pool(spec.roles_path, event_log)
            run_result = run_eval_task(
                task,
                agents,
                build_factory(workspace),
                workspace,
                event_log,
                settings=settings,
                hidden_tests=hidden_tests,
            )
            rubric = None
            if judge is not None:
                diff_text = workspace.diff_against_base(f"ticket/{task.task_id}")
                rubric = judge.score(task, diff_text)
            results[spec.label] = ConditionResult(label=spec.label, run=run_result, rubric=rubric)
        finally:
            event_log.close()

    return compare_structures(results[condition_a.label], results[condition_b.label])
