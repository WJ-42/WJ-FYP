from __future__ import annotations

import unittest

from wjfyp.eval.scoring import calculate_score, score_task
from wjfyp.eval.task import Checkpoint, EvalTask, GradingContext


class CalculateScoreTest(unittest.TestCase):
    def test_full_completion_gets_the_bonus(self) -> None:
        # TheAgentCompany's own ds-janusgraph-exercise: 6 total points, all earned.
        self.assertEqual(calculate_score(total=6, result=6), 1.0)

    def test_partial_credit_no_bonus(self) -> None:
        # 1 + 1 of 1/1/4-weighted checkpoints earned, out of 6 total.
        self.assertAlmostEqual(calculate_score(total=6, result=2), (2 / 6) * 0.5)

    def test_zero_earned(self) -> None:
        self.assertEqual(calculate_score(total=6, result=0), 0.0)

    def test_zero_total_does_not_raise(self) -> None:
        self.assertEqual(calculate_score(total=0, result=0), 0.0)


class ScoreTaskTest(unittest.TestCase):
    def _ctx(self) -> GradingContext:
        return GradingContext(ticket_id="T-1", repo_path="/tmp/does-not-matter")

    def test_mixed_checkpoints_sum_correctly(self) -> None:
        task = EvalTask(
            task_id="janusgraph-like",
            title="Stand up a service and verify its state",
            prompt="...",
            checkpoints=[
                Checkpoint(id="repo-exists", description="repo cloned", points=1, grader=lambda ctx: True),
                Checkpoint(id="service-up", description="service responds", points=1, grader=lambda ctx: True),
                Checkpoint(id="data-correct", description="data matches", points=4, grader=lambda ctx: False),
            ],
        )
        score = task_score = score_task(task, self._ctx())
        self.assertEqual(score.points_total, 6)
        self.assertEqual(score.points_earned, 2)
        self.assertAlmostEqual(score.score, (2 / 6) * 0.5)
        self.assertFalse(score.fully_complete)
        self.assertEqual(
            {r.checkpoint_id: r.earned for r in task_score.checkpoint_results},
            {"repo-exists": True, "service-up": True, "data-correct": False},
        )

    def test_all_checkpoints_pass_is_fully_complete(self) -> None:
        task = EvalTask(
            task_id="trivial",
            title="Trivial task",
            prompt="...",
            checkpoints=[Checkpoint(id="only", description="the one thing", points=3, grader=lambda ctx: True)],
        )
        score = score_task(task, self._ctx())
        self.assertTrue(score.fully_complete)
        self.assertEqual(score.score, 1.0)

    def test_a_raising_grader_is_recorded_as_failed_not_a_crash(self) -> None:
        def broken_grader(ctx: GradingContext) -> bool:
            raise RuntimeError("service unreachable")

        task = EvalTask(
            task_id="flaky",
            title="Task with a broken checkpoint",
            prompt="...",
            checkpoints=[
                Checkpoint(id="ok", description="fine", points=1, grader=lambda ctx: True),
                Checkpoint(id="broken", description="raises", points=1, grader=broken_grader),
            ],
        )
        score = score_task(task, self._ctx())
        self.assertEqual(score.points_earned, 1)
        broken_result = next(r for r in score.checkpoint_results if r.checkpoint_id == "broken")
        self.assertFalse(broken_result.earned)
        self.assertIn("service unreachable", broken_result.error or "")

    def test_task_with_no_checkpoints_is_not_fully_complete(self) -> None:
        task = EvalTask(task_id="empty", title="No checkpoints", prompt="...", checkpoints=[])
        score = score_task(task, self._ctx())
        self.assertEqual(score.points_total, 0)
        self.assertFalse(score.fully_complete)
        self.assertEqual(score.score, 0.0)


if __name__ == "__main__":
    unittest.main()
