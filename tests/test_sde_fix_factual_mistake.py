"""Verifies the ported sde-fix-factual-mistake checkpoints against a
small synthetic fixture matching the real agent.yaml's structure
(confirmed verbatim against TheAgentCompany's actual evaluator.py
before porting - see cs3ip-evaluation-detail memory), not a guessed
schema.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from wjfyp.eval.scoring import score_task
from wjfyp.eval.task import GradingContext
from wjfyp.eval.tasks import TASKS
from wjfyp.eval.tasks.sde_fix_factual_mistake import AGENT_YAML_PATH

TASK = TASKS["sde-fix-factual-mistake"]

_FIXED_YAML = """\
examples:
  - inputs:
      task: "What day of the week is 2099-01-01?"
    outputs:
      answer: "Thursday"
  - inputs:
      task: "What is the integral of sin(x^2) evaluated from -1 to 1?"
    outputs:
      answer: "0.620537"
"""

_UNFIXED_YAML = """\
examples:
  - inputs:
      task: "What day of the week is 2099-01-01?"
    outputs:
      answer: "Saturday"
  - inputs:
      task: "What is the integral of sin(x^2) evaluated from -1 to 1?"
    outputs:
      answer: "0.603848"
"""


class SdeFixFactualMistakeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.repo_path = Path(self._tmpdir.name)
        (self.repo_path / AGENT_YAML_PATH).parent.mkdir(parents=True)

    def _write(self, content: str) -> GradingContext:
        (self.repo_path / AGENT_YAML_PATH).write_text(content)
        return GradingContext(ticket_id="TCK-1", repo_path=str(self.repo_path))

    def test_fixed_yaml_earns_all_points(self) -> None:
        ctx = self._write(_FIXED_YAML)
        score = score_task(TASK, ctx)
        self.assertTrue(score.fully_complete)
        self.assertEqual(score.points_earned, 3)

    def test_unfixed_yaml_only_earns_the_file_exists_checkpoint(self) -> None:
        ctx = self._write(_UNFIXED_YAML)
        score = score_task(TASK, ctx)
        self.assertEqual(score.points_earned, 1)
        earned = {r.checkpoint_id for r in score.checkpoint_results if r.earned}
        self.assertEqual(earned, {"target-file-exists"})

    def test_missing_file_fails_every_checkpoint_without_crashing(self) -> None:
        # No agent.yaml written at all - every grader should fail
        # cleanly (via score_task's exception handling), not raise.
        ctx = GradingContext(ticket_id="TCK-1", repo_path=str(self.repo_path))
        score = score_task(TASK, ctx)
        self.assertEqual(score.points_earned, 0)

    def test_integral_within_tolerance_still_passes(self) -> None:
        yaml_content = _FIXED_YAML.replace("0.620537", "0.62090")  # within 0.001
        ctx = self._write(yaml_content)
        score = score_task(TASK, ctx)
        earned = {r.checkpoint_id for r in score.checkpoint_results if r.earned}
        self.assertIn("integral-fixed", earned)

    def test_integral_outside_tolerance_fails(self) -> None:
        yaml_content = _FIXED_YAML.replace("0.620537", "0.63")  # outside 0.001
        ctx = self._write(yaml_content)
        score = score_task(TASK, ctx)
        earned = {r.checkpoint_id for r in score.checkpoint_results if r.earned}
        self.assertNotIn("integral-fixed", earned)


if __name__ == "__main__":
    unittest.main()
