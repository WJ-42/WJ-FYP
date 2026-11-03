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
import os
import sys
from pathlib import Path
from typing import Any, Callable, TextIO

from wjfyp.eval.comparison import ConditionSpec, StructureComparison, run_structure_comparison
from wjfyp.eval.rubric import LLMRubricJudge
from wjfyp.eval.task import EvalTask
from wjfyp.eval.tasks import TASKS
from wjfyp.orchestrator.budget import BudgetExceededError, SpendGuard
from wjfyp.orchestrator.openai_agent import DEFAULT_BASE_URL
from wjfyp.orchestrator.openai_agent import build_agent_pool as build_open_model_pool


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


def _open_model_pool_builder(
    guard: SpendGuard | None, base_url: str, model: str | None
) -> Callable[..., dict]:
    """Agent pool factory for the open-model backend (FYP-60).

    run_structure_comparison only wires its own spend guard into the
    DEFAULT builder, on the grounds that an injected one owns its
    agents. So this closes over the guard itself rather than letting the
    failsafe quietly vanish the moment a run switches backend - the
    whole point of a money backstop is that it does not have gaps.
    """
    # A self-hosted server (Ollama, vLLM, llama.cpp) needs no key and
    # usually rejects none, so a placeholder keeps the SDK happy rather
    # than forcing a pointless secret for a box on your own LAN. A
    # hosted provider still requires the real thing.
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        if base_url == DEFAULT_BASE_URL:
            raise SystemExit(
                "GROQ_API_KEY is not set, so the hosted open-model backend cannot run. "
                "Put it in a local .env and export it, pass --base-url to point at a "
                "self-hosted server instead (e.g. --base-url "
                "http://<your-box>:11434/v1), or pass --backend claude."
            )
        api_key = "not-needed"
    return lambda roles_path, event_log: build_open_model_pool(
        roles_path, event_log, spend_guard=guard, api_key=api_key, base_url=base_url, model=model
    )


def run_compare(
    tasks: dict[str, EvalTask],
    task_id: str,
    condition_a: ConditionSpec,
    condition_b: ConditionSpec,
    template_repo_path: Path,
    work_dir: Path,
    use_judge: bool = False,
    budget_cap: float | None = None,
    backend: str = "claude",
    base_url: str = DEFAULT_BASE_URL,
    model: str | None = None,
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
    # A run served from the user's own hardware costs nothing, and the
    # guard has to be told so: an unrecognised model id otherwise prices
    # at the Opus rate, which both reports spend that did not happen and
    # trips the cap partway through a long local batch. The caller knows
    # the endpoint; the guard cannot infer it from a model name.
    self_hosted = backend != "claude" and base_url != DEFAULT_BASE_URL
    guard = (
        SpendGuard(cap_usd=budget_cap, self_hosted=self_hosted)
        if budget_cap is not None
        else None
    )
    extra: dict[str, Any] = {}
    if backend != "claude":
        # Only passed when it is actually needed, so an injected test
        # runner keeps seeing the call shape it already expects.
        extra["agent_pool_builder"] = _open_model_pool_builder(guard, base_url, model)
    try:
        comparison = runner(
            task, condition_a, condition_b, template_repo_path, work_dir,
            judge=judge, spend_guard=guard, **extra,
        )
    except BudgetExceededError as exc:
        print(f"budget cap hit, aborting: {exc}", file=out)
        return 2
    finally:
        if guard is not None:
            if guard.self_hosted:
                print("spend this run: $0.0000 (self-hosted endpoint)", file=out)
            else:
                print(
                    f"estimated spend this run: ${guard.spent_usd:.4f} (cap ${guard.cap_usd:.2f})",
                    file=out,
                )
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
    # Opt IN, not opt out. The rubric judge is the only part of a run
    # that still calls a paid API, and this project's constraint is that
    # a run costs nothing unless it is asked to. A default-on paid call
    # behind a --no-judge flag means forgetting the flag spends money,
    # which is the wrong way round for a failsafe.
    #
    # It saturated on the runs there is data for: all six Claude runs of
    # 15/10/2026 scored 3/3 on the checkpoints and 5/5 on every rubric
    # axis. That is a fact about CLAUDE on that task, though, not about
    # the judge or the task alone, and it does not carry over to the
    # open models - a weaker team may well produce the spread those runs
    # lacked. So this flag exists for the cost, which is certain, rather
    # than for the saturation, which is not established beyond the
    # backend that produced it.
    #
    # The deterministic checkpoint and hidden-test scoring needs no
    # model at all and is unaffected either way.
    compare_parser.add_argument(
        "--judge",
        action="store_true",
        help=(
            "score the diffs with the LLM rubric judge as well as the deterministic "
            "checkpoints. Off by default: it is the one remaining paid API call "
            "(Anthropic, about 2 calls per comparison), and it stays on Claude even "
            "when --backend is open so the measuring instrument does not change when "
            "the thing being measured does"
        ),
    )
    compare_parser.add_argument(
        "--no-judge",
        action="store_true",
        help="deprecated and now the default; accepted so existing commands keep working",
    )
    compare_parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=(
            "OpenAI-compatible endpoint for --backend open. Point it at a "
            "self-hosted server to run with no rate limits and no account at "
            "all, e.g. http://10.126.129.12:11434/v1 for Ollama on another "
            f"machine (default: {DEFAULT_BASE_URL})"
        ),
    )
    compare_parser.add_argument(
        "--model",
        default=None,
        help=(
            "override the model on EVERY role, keeping each arm's tiers and "
            "personas as written. This is what lets one set of arm configs run "
            "on any backend instead of a duplicate set per provider. It "
            "collapses the capability ladder on purpose, which is correct for "
            "the persona arms since they hold models constant by design. Leave "
            "unset for an arm whose point is differing capability per tier"
        ),
    )
    compare_parser.add_argument(
        "--backend",
        choices=["claude", "open", "groq"],
        default="claude",
        help=(
            "which Agent implementation runs the team: claude (Anthropic API, "
            "paid) or open (any OpenAI-compatible endpoint - a self-hosted "
            "Ollama/vLLM/llama.cpp server via --base-url, or a hosted provider, "
            "which needs GROQ_API_KEY). 'groq' is the former name for 'open' and "
            "still works, since the implementation was never Groq-specific. The "
            "rubric judge is unaffected and stays on Claude, so that the "
            "measuring instrument does not change when the thing being measured "
            "does (default: claude)"
        ),
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
            use_judge=args.judge and not args.no_judge,
            budget_cap=args.budget_cap,
            backend=args.backend,
            base_url=args.base_url,
            model=args.model,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
