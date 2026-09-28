from __future__ import annotations

import io
import unittest
from pathlib import Path

from wjfyp.eval.cli import list_tasks, main, run_compare, show_task
from wjfyp.eval.comparison import ConditionResult, ConditionSpec, StructureComparison
from wjfyp.eval.mast import MastFailureMode
from wjfyp.eval.rubric import AxisScore, RubricScore
from wjfyp.eval.runner import EvalRunResult
from wjfyp.eval.scoring import TaskScore
from wjfyp.eval.task import Checkpoint, EvalTask


def _sample_tasks() -> dict[str, EvalTask]:
    return {
        "demo-task": EvalTask(
            task_id="demo-task",
            title="A demo task",
            prompt="Do the demo thing.",
            checkpoints=[
                Checkpoint(id="cp1", description="first check", points=2, grader=lambda ctx: True),
                Checkpoint(id="cp2", description="second check", points=1, grader=lambda ctx: True),
            ],
            source="TheAgentCompany:demo-task",
        )
    }


class ListTasksTest(unittest.TestCase):
    def test_lists_id_title_and_point_total(self) -> None:
        out = io.StringIO()
        list_tasks(_sample_tasks(), out=out)
        text = out.getvalue()
        self.assertIn("demo-task", text)
        self.assertIn("3 pts", text)
        self.assertIn("2 checkpoints", text)
        self.assertIn("A demo task", text)


class ShowTaskTest(unittest.TestCase):
    def test_shows_prompt_source_and_every_checkpoint(self) -> None:
        out = io.StringIO()
        found = show_task(_sample_tasks(), "demo-task", out=out)
        text = out.getvalue()
        self.assertTrue(found)
        self.assertIn("Do the demo thing.", text)
        self.assertIn("TheAgentCompany:demo-task", text)
        self.assertIn("cp1: first check", text)
        self.assertIn("[2 pt]", text)
        self.assertIn("cp2: second check", text)

    def test_unknown_task_id_reports_cleanly_and_returns_false(self) -> None:
        out = io.StringIO()
        found = show_task(_sample_tasks(), "no-such-task", out=out)
        self.assertFalse(found)
        self.assertIn("unknown task id", out.getvalue())


class MainExitCodeTest(unittest.TestCase):
    """A CLI that always exits 0 would let a script silently miss a
    failed `show` lookup - these pin the exit code, not just the text.
    """

    def test_list_exits_zero(self) -> None:
        self.assertEqual(main(["list"]), 0)

    def test_show_known_task_exits_zero(self) -> None:
        self.assertEqual(main(["show", "sde-fix-factual-mistake"]), 0)

    def test_show_unknown_task_exits_nonzero(self) -> None:
        self.assertEqual(main(["show", "no-such-task"]), 1)


def _canned_comparison() -> StructureComparison:
    def run_result(status: str, score: float, tags: list[MastFailureMode]) -> EvalRunResult:
        return EvalRunResult(
            task_id="demo-task",
            ticket_id="demo-task",
            ticket_status=status,
            score=TaskScore(
                task_id="demo-task", checkpoint_results=[], points_earned=int(score * 2),
                points_total=2, score=score, fully_complete=score == 1.0,
            ),
            mast_tags=tags,
        )

    condition_a = ConditionResult(
        label="hierarchical",
        run=run_result("done", 1.0, []),
        rubric=RubricScore(
            task_id="demo-task",
            axis_scores=[AxisScore(axis_id="correctness", score=5, justification="looks right")],
            overall=5.0,
        ),
    )
    condition_b = ConditionResult(label="flat", run=run_result("halted", 0.0, [MastFailureMode.FM_1_3]))
    return StructureComparison(
        task_id="demo-task", condition_a=condition_a, condition_b=condition_b,
        score_delta=1.0, condition_a_better=True,
    )


class RunCompareTest(unittest.TestCase):
    """`runner` is injected so this never touches Docker or a real API
    key - same shape as list_tasks/show_task's own dependency injection.
    """

    def test_prints_both_conditions_and_the_verdict(self) -> None:
        out = io.StringIO()

        code = run_compare(
            _sample_tasks(), "demo-task",
            ConditionSpec(label="hierarchical", roles_path=Path("config/roles.yaml")),
            ConditionSpec(label="flat", roles_path=Path("config/roles_flat.yaml")),
            Path("/template"), Path("/work"),
            out=out, runner=lambda *a, **k: _canned_comparison(),
        )

        text = out.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("hierarchical", text)
        self.assertIn("flat", text)
        self.assertIn("done", text)
        self.assertIn("halted", text)
        self.assertIn("FM-1.3", text)
        self.assertIn("rubric[correctness]: 5", text)
        self.assertIn("hierarchical scored higher", text)

    def test_unknown_task_id_returns_one_without_calling_the_runner(self) -> None:
        out = io.StringIO()
        called = []

        code = run_compare(
            _sample_tasks(), "no-such-task",
            ConditionSpec(label="hierarchical", roles_path=Path("config/roles.yaml")),
            ConditionSpec(label="flat", roles_path=Path("config/roles_flat.yaml")),
            Path("/template"), Path("/work"),
            out=out, runner=lambda *a, **k: called.append(1),
        )

        self.assertEqual(code, 1)
        self.assertIn("unknown task id", out.getvalue())
        self.assertEqual(called, [])

    def test_use_judge_false_still_calls_the_runner(self) -> None:
        out = io.StringIO()
        captured_kwargs = {}

        def runner(*args, **kwargs):
            captured_kwargs.update(kwargs)
            return _canned_comparison()

        run_compare(
            _sample_tasks(), "demo-task",
            ConditionSpec(label="hierarchical", roles_path=Path("config/roles.yaml")),
            ConditionSpec(label="flat", roles_path=Path("config/roles_flat.yaml")),
            Path("/template"), Path("/work"),
            use_judge=False, out=out, runner=runner,
        )

        self.assertIsNone(captured_kwargs["judge"])


class MainCompareTest(unittest.TestCase):
    def test_unknown_task_id_exits_nonzero_without_needing_docker_or_a_real_repo(self) -> None:
        # run_compare's own tasks.get() check short-circuits before
        # run_structure_comparison (the real default) is ever called, so
        # this is safe to run through main() for real - it never touches
        # Docker, a git clone, or the API.
        self.assertEqual(main(["compare", "no-such-task", "--repo", "/nonexistent"]), 1)


class RealTaskRegistryTest(unittest.TestCase):
    """Smoke test against the actual registered ported tasks, not just
    the synthetic fixture above.
    """

    def test_list_includes_both_ported_tasks(self) -> None:
        from wjfyp.eval.tasks import TASKS

        out = io.StringIO()
        list_tasks(TASKS, out=out)
        text = out.getvalue()
        self.assertIn("sde-fix-factual-mistake", text)
        self.assertIn("sde-write-a-unit-test-for-append_file-function", text)


if __name__ == "__main__":
    unittest.main()
