"""Eval CLI: `python -m wjfyp.eval.cli list|show|compare`.

`compare` runs one task through two team configs (e.g. config/roles.yaml
vs. config/roles_flat.yaml - the hierarchical-vs-flat comparison, see
cs3ip-fyp-overview memory's "Evaluation direction confirmed" entry) and
diffs the results. This was withheld until a real `agents` dict was
possible - that's now the case (orchestrator.claude_agent.ClaudeAgent),
so the real integration points (wjfyp.eval.runner.run_eval_task and
wjfyp.eval.comparison.run_structure_comparison) are wired up here rather
than left unused.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable, TextIO

from wjfyp.eval.comparison import ConditionSpec, StructureComparison, run_structure_comparison
from wjfyp.eval.rubric import LLMRubricJudge
from wjfyp.eval.task import EvalTask
from wjfyp.eval.tasks import TASKS
from wjfyp.orchestrator.budget import BudgetExceededError, SpendGuard


def list_tasks(tasks: dict[str, EvalTask], out: TextIO = sys.stdout) -> None:
    for task in sorted(tasks.values(), key=lambda t: t.task_id):
        print(f"{task.task_id}  ({task.total_points} pts, {len(task.checkpoints)} checkpoints)", file=out)
        print(f"    {task.title}", file=out)


def show_task(tasks: dict[str, EvalTask], task_id: str, out: TextIO = sys.stdout) -> bool:
    """Returns whether task_id was found - main() uses this for the
    process exit code, so a script calling `wjfyp-eval show <bad-id>`
    actually fails rather than silently exiting 0.
    """
    task = tasks.get(task_id)
    if task is None:
        print(f"unknown task id: {task_id!r}", file=out)
        return False

    print(f"{task.task_id}: {task.title}", file=out)
    if task.source:
        print(f"source: {task.source}", file=out)
    print(file=out)
    print(task.prompt, file=out)
    print(file=out)
    print(f"Checkpoints ({task.total_points} points total):", file=out)
    for checkpoint in task.checkpoints:
        print(f"  [{checkpoint.points} pt] {checkpoint.id}: {checkpoint.description}", file=out)
    return True


def print_comparison(comparison: StructureComparison, out: TextIO = sys.stdout) -> None:
    print(f"Comparison for {comparison.task_id}", file=out)
    for condition in (comparison.condition_a, comparison.condition_b):
        run = condition.run
        print(file=out)
        print(f"{condition.label}:", file=out)
        print(f"  status: {run.ticket_status}", file=out)
        print(f"  score: {run.score.points_earned}/{run.score.points_total} ({run.score.score:.2f})", file=out)
        if run.mast_tags:
            print(f"  MAST tags: {', '.join(tag.value for tag in run.mast_tags)}", file=out)
        if condition.rubric is not None:
            for axis_score in condition.rubric.axis_scores:
                print(f"  rubric[{axis_score.axis_id}]: {axis_score.score} - {axis_score.justification}", file=out)
            print(f"  rubric overall: {condition.rubric.overall:.2f}", file=out)

    print(file=out)
    better = comparison.condition_a if comparison.condition_a_better else comparison.condition_b
    if comparison.score_delta == 0:
        print(f"Verdict: tied at {comparison.condition_a.run.score.score:.2f}", file=out)
    else:
        print(f"Verdict: {better.label} scored higher by {abs(comparison.score_delta):.2f}", file=out)


def run_compare(
    tasks: dict[str, EvalTask],
    task_id: str,
    condition_a: ConditionSpec,
    condition_b: ConditionSpec,
    template_repo_path: Path,
    work_dir: Path,
    use_judge: bool = True,
    budget_cap: float | None = None,
    out: TextIO = sys.stdout,
    runner: Callable[..., StructureComparison] = run_structure_comparison,
) -> int:
    """`runner` is injectable so tests don't need Docker or a real API
    key - same dependency-injection shape list_tasks/show_task already
    use (`tasks`/`out` as explicit params, not module globals).

    `budget_cap`, if given, is a USD ceiling shared across both
    conditions' real API calls (see orchestrator/budget.py's SpendGuard)
    - a runaway loop (known or not yet found) gets a hard stop instead of
    an unbounded real bill. Exit code 2 distinguishes a budget trip from
    every other failure (1), so a driver script can tell the two apart.
    """
    task = tasks.get(task_id)
    if task is None:
        print(f"unknown task id: {task_id!r}", file=out)
        return 1
    judge = LLMRubricJudge() if use_judge else None
    guard = SpendGuard(cap_usd=budget_cap) if budget_cap is not None else None
    try:
        comparison = runner(
            task, condition_a, condition_b, template_repo_path, work_dir, judge=judge, spend_guard=guard
        )
    except BudgetExceededError as exc:
        print(f"budget cap hit, aborting: {exc}", file=out)
        return 2
    finally:
        if guard is not None:
            print(f"estimated spend this run: ${guard.spent_usd:.4f} (cap ${guard.cap_usd:.2f})", file=out)
    print_comparison(comparison, out=out)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wjfyp-eval", description="CS3IP evaluation harness CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list", help="list every ported eval task")
    show_parser = subparsers.add_parser("show", help="show one task's prompt and checkpoints")
    show_parser.add_argument("task_id")

    compare_parser = subparsers.add_parser(
        "compare", help="run one task through two team configs and compare"
    )
    compare_parser.add_argument("task_id")
    compare_parser.add_argument("--repo", type=Path, required=True, help="template repo (outside this tree)")
    compare_parser.add_argument("--work-dir", type=Path, default=Path("data/comparisons"))
    compare_parser.add_argument("--roles-a", type=Path, default=Path("config/roles.yaml"))
    compare_parser.add_argument("--label-a", default="hierarchical")
    compare_parser.add_argument("--roles-b", type=Path, default=Path("config/roles_flat.yaml"))
    compare_parser.add_argument("--label-b", default="flat")
    compare_parser.add_argument(
        "--no-judge", action="store_true", help="skip the rubric judge (2 fewer API calls)"
    )
    compare_parser.add_argument(
        "--budget-cap",
        type=float,
        default=5.0,
        help=(
            "USD safety cap shared across both conditions' real API calls, a "
            "conservative estimate (see orchestrator/budget.py) - the run "
            "aborts (exit code 2) the moment it's reached rather than "
            "spending past it (default: 5.00)"
        ),
    )

    args = parser.parse_args(argv)

    if args.command == "list":
        list_tasks(TASKS)
        return 0
    elif args.command == "show":
        return 0 if show_task(TASKS, args.task_id) else 1
    elif args.command == "compare":
        return run_compare(
            TASKS,
            args.task_id,
            ConditionSpec(label=args.label_a, roles_path=args.roles_a),
            ConditionSpec(label=args.label_b, roles_path=args.roles_b),
            args.repo,
            args.work_dir,
            use_judge=not args.no_judge,
            budget_cap=args.budget_cap,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
