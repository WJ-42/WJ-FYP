"""Ported from TheAgentCompany's sde-fix-factual-mistake (github.com/
TheAgentCompany/TheAgentCompany, workspaces/tasks/sde-fix-factual-mistake).
Checkpoint logic transcribed from their evaluator.py, fetched verbatim
2026-09-25 rather than summarised - see cs3ip-evaluation-detail memory.

Adaptation from the original: their checkpoint1 is check_repo_exists
('openhands'), verifying the agent's own clone step succeeded. Our
GitWorkspace already guarantees a repo exists before any ticket work
starts (see cs3ip-sandbox-design memory), so that check is meaningless
here - adapted to check the specific file this task actually cares
about is present instead. Checkpoints 2 and 3 are otherwise a direct
port: same field lookups, same tolerance on the float comparison.

Per-checkpoint try/except in the original (each grader catches its own
exceptions and logs a warning) isn't reproduced here - score_task()
already catches a raising grader uniformly and records it as a failed
checkpoint with its error (see wjfyp.eval.scoring), so duplicating that
per-checkpoint would just be redundant.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from wjfyp.eval.task import Checkpoint, EvalTask, GradingContext
from wjfyp.eval.tasks import register

AGENT_YAML_PATH = "agenthub/micro/math_agent/agent.yaml"


def _load_examples(ctx: GradingContext) -> list[dict]:
    path = Path(ctx.repo_path) / AGENT_YAML_PATH
    data = yaml.safe_load(path.read_text())
    return data.get("examples", []) if isinstance(data, dict) else []


def _checkpoint_target_file_exists(ctx: GradingContext) -> bool:
    return (Path(ctx.repo_path) / AGENT_YAML_PATH).exists()


def _checkpoint_day_of_week_fixed(ctx: GradingContext) -> bool:
    for example in _load_examples(ctx):
        if example.get("inputs", {}).get("task") == "What day of the week is 2099-01-01?":
            return example.get("outputs", {}).get("answer") == "Thursday"
    return False


def _checkpoint_integral_fixed(ctx: GradingContext) -> bool:
    for example in _load_examples(ctx):
        if example.get("inputs", {}).get("task") == "What is the integral of sin(x^2) evaluated from -1 to 1?":
            answer = example.get("outputs", {}).get("answer")
            try:
                return abs(float(answer) - 0.620537) < 0.001
            except (TypeError, ValueError):
                return False
    return False


TASK = register(
    EvalTask(
        task_id="sde-fix-factual-mistake",
        title="Fix two factual mistakes in a math agent's example answers",
        prompt=(
            f"Fix two factual mistakes in {AGENT_YAML_PATH}. The example answering "
            '"What day of the week is 2099-01-01?" currently answers "Saturday" - '
            'it should answer "Thursday". The example answering "What is the '
            'integral of sin(x^2) evaluated from -1 to 1?" currently answers '
            "0.603848 - it should answer approximately 0.620537."
        ),
        checkpoints=[
            Checkpoint(
                id="target-file-exists",
                description="agent.yaml is present in the repo",
                points=1,
                grader=_checkpoint_target_file_exists,
            ),
            Checkpoint(
                id="day-of-week-fixed",
                description="2099-01-01 day-of-week example answers Thursday",
                points=1,
                grader=_checkpoint_day_of_week_fixed,
            ),
            Checkpoint(
                id="integral-fixed",
                description="sin(x^2) integral example answers ~0.620537",
                points=1,
                grader=_checkpoint_integral_fixed,
            ),
        ],
        source="TheAgentCompany:sde-fix-factual-mistake",
    )
)
