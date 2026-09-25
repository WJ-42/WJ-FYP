from __future__ import annotations

from pydantic import BaseModel

from wjfyp.eval.task import EvalTask, GradingContext


def calculate_score(total: int, result: int) -> float:
    """TheAgentCompany's weighted partial-credit formula, confirmed
    verbatim against their evaluation/summarise_results.py (see
    cs3ip-evaluation-detail memory): 0.5 for the fraction of points
    earned, plus a 0.5 bonus that only fires (via integer division) when
    every point was earned. `total == 0` isn't a case their harness
    guards against but is a real possible input here (an EvalTask with
    no checkpoints), so it returns 0.0 rather than raising.
    """
    if total == 0:
        return 0.0
    return (result / total) * 0.5 + (result // total) * 0.5


class CheckpointResult(BaseModel):
    checkpoint_id: str
    points: int
    earned: bool
    error: str | None = None  # set when the grader raised, for debugging a run


class TaskScore(BaseModel):
    task_id: str
    checkpoint_results: list[CheckpointResult]
    points_earned: int
    points_total: int
    score: float
    fully_complete: bool


def score_task(task: EvalTask, ctx: GradingContext) -> TaskScore:
    """Run every checkpoint's grader against the finished run and compute
    the task's score. Checkpoints are independent (see EvalTask's
    docstring) so one grader raising doesn't abort the others - it's
    recorded as a failed checkpoint with its error, same as a checkpoint
    that legitimately returned False, since a checkpoint that can't
    verify its condition is not a verified pass either way.
    """
    results: list[CheckpointResult] = []
    for checkpoint in task.checkpoints:
        try:
            earned = bool(checkpoint.grader(ctx))
            error = None
        except Exception as exc:  # noqa: BLE001 - grader code is untrusted/ported, must not crash the run
            earned = False
            error = f"{type(exc).__name__}: {exc}"
        results.append(
            CheckpointResult(
                checkpoint_id=checkpoint.id,
                points=checkpoint.points,
                earned=earned,
                error=error,
            )
        )

    points_total = task.total_points
    points_earned = sum(r.points for r in results if r.earned)
    return TaskScore(
        task_id=task.task_id,
        checkpoint_results=results,
        points_earned=points_earned,
        points_total=points_total,
        score=calculate_score(points_total, points_earned),
        fully_complete=points_total > 0 and points_earned == points_total,
    )
