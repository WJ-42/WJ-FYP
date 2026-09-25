from __future__ import annotations

import io
import unittest

from wjfyp.eval.cli import list_tasks, main, show_task
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
