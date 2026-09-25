"""Verifies the two fully-portable checkpoints of the ported
sde-write-a-unit-test-for-append_file-function task against small
synthetic fixtures - file existence and AST function detection, no
Poetry/pytest/OpenHands needed. Checkpoints 3 and 4 (pytest execution,
coverage comparison) are reviewed but not exercised here - see the
module's own docstring for why: they need a real Poetry-managed repo
this environment doesn't have, same unverified status as the
Docker-dependent sandbox code pending FYP-22.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from wjfyp.eval.task import GradingContext
from wjfyp.eval.tasks import TASKS
from wjfyp.eval.tasks.sde_write_a_unit_test import (
    SOURCE_FILE,
    TEST_FILE,
    _checkpoint_source_file_exists,
    _checkpoint_test_function_exists,
    _remove_test_function,
)

TASK = TASKS["sde-write-a-unit-test-for-append_file-function"]


class CheckpointSourceFileExistsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.repo_path = Path(self._tmpdir.name)

    def test_missing_file_fails(self) -> None:
        ctx = GradingContext(ticket_id="TCK-1", repo_path=str(self.repo_path))
        self.assertFalse(_checkpoint_source_file_exists(ctx))

    def test_present_file_passes(self) -> None:
        source_path = self.repo_path / SOURCE_FILE
        source_path.parent.mkdir(parents=True)
        source_path.write_text("def append_file(path, content):\n    ...\n")
        ctx = GradingContext(ticket_id="TCK-1", repo_path=str(self.repo_path))
        self.assertTrue(_checkpoint_source_file_exists(ctx))


class CheckpointTestFunctionExistsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.repo_path = Path(self._tmpdir.name)
        self.test_path = self.repo_path / TEST_FILE
        self.test_path.parent.mkdir(parents=True)

    def _ctx(self) -> GradingContext:
        return GradingContext(ticket_id="TCK-1", repo_path=str(self.repo_path))

    def test_function_present_passes(self) -> None:
        self.test_path.write_text(
            "def test_append_file():\n    assert True\n\n\ndef test_other():\n    pass\n"
        )
        self.assertTrue(_checkpoint_test_function_exists(self._ctx()))

    def test_function_absent_fails(self) -> None:
        self.test_path.write_text("def test_something_else():\n    assert True\n")
        self.assertFalse(_checkpoint_test_function_exists(self._ctx()))

    def test_wrong_name_does_not_match(self) -> None:
        # A near-miss name shouldn't accidentally satisfy the checkpoint.
        self.test_path.write_text("def test_append_files():\n    assert True\n")
        self.assertFalse(_checkpoint_test_function_exists(self._ctx()))


class RemoveTestFunctionTest(unittest.TestCase):
    """Unit-tests the destructive mutation helper in isolation, with a
    tiny synthetic file, rather than only trusting the transcription -
    this is the part of the port most worth double-checking since it's
    the only checkpoint that changes the repo as a side effect.
    """

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.repo_path = Path(self._tmpdir.name)
        self.test_path = self.repo_path / TEST_FILE
        self.test_path.parent.mkdir(parents=True)

    def test_removes_only_the_target_function(self) -> None:
        self.test_path.write_text(
            "def test_before():\n"
            "    assert True\n"
            "\n"
            "\n"
            "def test_append_file():\n"
            "    content = 'hi'\n"
            "    assert content\n"
            "\n"
            "\n"
            "def test_after():\n"
            "    assert True\n"
        )

        removed = _remove_test_function(str(self.repo_path))

        self.assertTrue(removed)
        remaining = self.test_path.read_text()
        self.assertIn("def test_before():", remaining)
        self.assertIn("def test_after():", remaining)
        self.assertNotIn("def test_append_file():", remaining)
        self.assertNotIn("content = 'hi'", remaining)

    def test_missing_function_returns_false_without_modifying_the_file(self) -> None:
        original = "def test_unrelated():\n    assert True\n"
        self.test_path.write_text(original)

        removed = _remove_test_function(str(self.repo_path))

        self.assertFalse(removed)
        self.assertEqual(self.test_path.read_text(), original)

    def test_function_at_end_of_file_is_removed_cleanly(self) -> None:
        self.test_path.write_text(
            "def test_before():\n    assert True\n\n\ndef test_append_file():\n    assert True\n"
        )

        removed = _remove_test_function(str(self.repo_path))

        self.assertTrue(removed)
        remaining = self.test_path.read_text()
        self.assertIn("def test_before():", remaining)
        self.assertNotIn("def test_append_file():", remaining)


class TaskRegistrationTest(unittest.TestCase):
    def test_total_points_match_the_real_evaluator(self) -> None:
        self.assertEqual(TASK.total_points, 5)
        points_by_id = {c.id: c.points for c in TASK.checkpoints}
        self.assertEqual(
            points_by_id,
            {
                "source-file-exists": 1,
                "test-function-exists": 1,
                "test-passes": 2,
                "coverage-drops-without-test": 1,
            },
        )


if __name__ == "__main__":
    unittest.main()
