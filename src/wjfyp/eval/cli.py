"""Eval CLI: `python -m wjfyp.eval.cli list|show`.

Only list/show are exposed here, not a `run` subcommand - running a
task for real needs a real Agent, and no LLM-backed implementation
exists yet (model/API selection is still deferred, see
cs3ip-fyp-overview memory). Building a `run` command that can't
actually do anything without one would just be an unusable stub; the
real integration point is wjfyp.eval.runner.run_eval_task(), which is
fully built and tested (see tests/test_runner.py) and ready to use the
moment a real `agents` dict exists - list/show don't need that at all,
so they're the part that's genuinely usable today.
"""

from __future__ import annotations

import argparse
import sys
from typing import TextIO

from wjfyp.eval.task import EvalTask
from wjfyp.eval.tasks import TASKS


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wjfyp-eval", description="CS3IP evaluation harness CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list", help="list every ported eval task")
    show_parser = subparsers.add_parser("show", help="show one task's prompt and checkpoints")
    show_parser.add_argument("task_id")

    args = parser.parse_args(argv)

    if args.command == "list":
        list_tasks(TASKS)
        return 0
    elif args.command == "show":
        return 0 if show_task(TASKS, args.task_id) else 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
