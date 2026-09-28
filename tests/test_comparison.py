from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from wjfyp.eval.comparison import (
    ConditionResult,
    ConditionSpec,
    compare_structures,
    run_structure_comparison,
)
from wjfyp.eval.mast import MastFailureMode
from wjfyp.eval.runner import EvalRunResult
from wjfyp.eval.scoring import TaskScore
from wjfyp.eval.task import EvalTask
from wjfyp.models.message import AgentRef, HandoffNote, Message, MessageContent, MessageType
from wjfyp.models.ticket import Ticket
from wjfyp.orchestrator.agent import AgentContext, AgentResponse
from wjfyp.sandbox.controller import TestResult
from wjfyp.sandbox.fake import FakeSandboxController
from wjfyp.sandbox.git_workspace import GitWorkspace


def _run_result(task_id: str, status: str, score: float) -> EvalRunResult:
    return EvalRunResult(
        task_id=task_id,
        ticket_id=task_id,
        ticket_status=status,
        score=TaskScore(
            task_id=task_id, checkpoint_results=[], points_earned=0, points_total=0, score=score, fully_complete=False
        ),
        mast_tags=[MastFailureMode.FM_1_3] if status == "halted" else [],
    )


class CompareStructuresTest(unittest.TestCase):
    def test_condition_a_better_when_it_scores_higher(self) -> None:
        a = ConditionResult(label="hierarchical", run=_run_result("t1", "done", 0.9))
        b = ConditionResult(label="flat", run=_run_result("t1", "done", 0.4))

        comparison = compare_structures(a, b)

        self.assertAlmostEqual(comparison.score_delta, 0.5)
        self.assertTrue(comparison.condition_a_better)
        self.assertEqual(comparison.task_id, "t1")

    def test_condition_a_better_is_false_when_b_scores_higher(self) -> None:
        a = ConditionResult(label="hierarchical", run=_run_result("t1", "done", 0.3))
        b = ConditionResult(label="flat", run=_run_result("t1", "done", 0.8))

        comparison = compare_structures(a, b)

        self.assertAlmostEqual(comparison.score_delta, -0.5)
        self.assertFalse(comparison.condition_a_better)

    def test_a_tie_is_not_counted_as_condition_a_better(self) -> None:
        a = ConditionResult(label="hierarchical", run=_run_result("t1", "done", 0.5))
        b = ConditionResult(label="flat", run=_run_result("t1", "done", 0.5))

        comparison = compare_structures(a, b)

        self.assertEqual(comparison.score_delta, 0.0)
        self.assertFalse(comparison.condition_a_better)

    def test_mismatched_task_ids_raise(self) -> None:
        a = ConditionResult(label="hierarchical", run=_run_result("t1", "done", 0.9))
        b = ConditionResult(label="flat", run=_run_result("t2", "done", 0.4))

        with self.assertRaises(ValueError):
            compare_structures(a, b)


class _ScriptedAgent:
    def __init__(self, role: str, script: list[str]):
        self.role = role
        self.instance_id = f"{role}-1"
        self._script = list(script)

    def invoke(self, context: AgentContext) -> AgentResponse:
        trigger = self._script.pop(0)
        message = Message(
            sender=AgentRef(role=context.role, instance_id=self.instance_id),
            channel_id=context.channel_id,
            type=MessageType.STATUS_UPDATE,
            ticket_ref=context.ticket.id,
            content=MessageContent(
                text=f"{context.role} fires {trigger}",
                handoff=HandoffNote(done="done", remaining="n/a", notes_for_next="n/a"),
            ),
        )
        return AgentResponse(message=message, trigger=trigger)


def _agents(cto_script: list[str], product_script: list[str], engineering_script: list[str]) -> dict:
    return {
        "cto": [_ScriptedAgent("cto", cto_script)],
        "product": [_ScriptedAgent("product", product_script)],
        "engineering": [_ScriptedAgent("engineering", engineering_script)],
    }


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=True)
    (path / "README.md").write_text("hello\n")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-m", "initial"], check=True, capture_output=True)


def _passing_test_result() -> TestResult:
    return TestResult(
        passed=True, fail_to_pass_total=0, fail_to_pass_passed=0, pass_to_pass_total=0, pass_to_pass_passed=0, output=""
    )


def _failing_test_result() -> TestResult:
    return TestResult(
        passed=False, fail_to_pass_total=0, fail_to_pass_passed=0, pass_to_pass_total=0, pass_to_pass_passed=0, output=""
    )


class RunStructureComparisonIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.template_repo = Path(self._tmpdir.name) / "template"
        _init_repo(self.template_repo)
        self.work_dir = Path(self._tmpdir.name) / "work"

        # Two independently-scripted role pools: condition A completes
        # cleanly (a genuine merge happens), condition B exhausts its
        # retry cap and halts (no merge) - proving the comparison
        # reflects two truly independent runs, not the same result
        # duplicated twice.
        self._agents_a = _agents(
            cto_script=["decomposed", "approved"],  # acts twice: Intake, then Review
            product_script=["spec_ready"],
            engineering_script=["assigned", "code_submission"],
        )
        self._agents_b = _agents(
            cto_script=["decomposed", "cto_cannot_resolve"],
            product_script=["spec_ready"],
            engineering_script=["assigned", "code_submission", "code_submission", "code_submission", "code_submission"],
        )
        self._pools = {"hierarchical": self._agents_a, "flat": self._agents_b}
        self._results = {
            "hierarchical": [_passing_test_result()],
            "flat": [_failing_test_result() for _ in range(4)],
        }

    def _sandbox_factory_builder(self, workspace: GitWorkspace):
        # run_structure_comparison builds one workspace per label under
        # work_dir/<label>, so the workspace's own directory name is a
        # reliable way to tell which condition this factory is for.
        label = workspace.repo_path.name
        attempt = [0]

        def factory(ticket: Ticket) -> FakeSandboxController:
            # A real commit per attempt, same reason as test_runner.py's
            # own fake factory: FakeSandboxController never touches the
            # actual repo, so without this a completed condition would
            # merge a branch identical to base (nothing to observe), and
            # a retried condition's second commit attempt would be a
            # git no-op error (nothing changed to commit).
            branch = workspace.ensure_branch(ticket)
            attempt[0] += 1
            repo = workspace.repo_path
            marker_name = f"{label}.marker"
            subprocess.run(["git", "-C", str(repo), "checkout", branch], check=True, capture_output=True)
            (repo / marker_name).write_text(f"attempt {attempt[0]}\n")
            # Stage only the marker file, not "." - the event log this
            # condition writes to lives at repo/eventlog.db, colocated
            # with this clone (see run_structure_comparison), and a
            # broad `git add .` would stage it onto the ticket branch
            # too; checking back out to base afterward would then delete
            # it from the working tree entirely (it's untracked on
            # base), unlinking the file out from under the still-open
            # EventLog connection - caught by this test itself the first
            # time it was written, when the "flat" condition's
            # eventlog.db turned out not to exist on disk afterward.
            subprocess.run(["git", "-C", str(repo), "add", marker_name], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "commit", "-m", f"{label} attempt {attempt[0]}"],
                check=True, capture_output=True,
            )
            subprocess.run(
                ["git", "-C", str(repo), "checkout", workspace.default_base], check=True, capture_output=True
            )
            return FakeSandboxController(test_results=[self._results[label].pop(0)])

        return factory

    def _agent_pool_builder(self, roles_path: Path, event_log) -> dict:
        # Stands in for build_agent_pool(): real usage passes an actual
        # config/roles*.yaml path, but injecting this callable (the same
        # DI pattern sandbox_factory_builder already uses) means the
        # test never needs Docker, an API key, or the real yaml files -
        # roles_path here is just whatever distinguishing value each
        # ConditionSpec below was given.
        return self._pools[str(roles_path)]

    def test_two_conditions_run_in_isolated_repos_and_are_compared_correctly(self) -> None:
        condition_a = ConditionSpec(label="hierarchical", roles_path=Path("hierarchical"))
        condition_b = ConditionSpec(label="flat", roles_path=Path("flat"))
        # No checkpoints: FakeSandboxController never writes real files,
        # so a checkpoint grader has nothing genuine to distinguish
        # between the two conditions on - this test's job is proving
        # isolation and that the comparison reflects two independently
        # driven runs (status, MAST tags, git history), not manufacturing
        # a meaningful score differential from fakes that can't produce one.
        task = EvalTask(task_id="TCK-CMP", title="Demo comparison task", prompt="Do the thing.", checkpoints=[])

        comparison = run_structure_comparison(
            task, condition_a, condition_b, self.template_repo, self.work_dir,
            sandbox_factory_builder=self._sandbox_factory_builder,
            agent_pool_builder=self._agent_pool_builder,
        )

        self.assertEqual(comparison.task_id, "TCK-CMP")
        self.assertEqual(comparison.condition_a.label, "hierarchical")
        self.assertEqual(comparison.condition_a.run.ticket_status, "done")
        self.assertEqual(comparison.condition_a.run.mast_tags, [])
        self.assertEqual(comparison.condition_b.label, "flat")
        self.assertEqual(comparison.condition_b.run.ticket_status, "halted")
        self.assertEqual(
            set(comparison.condition_b.run.mast_tags), {MastFailureMode.FM_1_3, MastFailureMode.FM_3_1}
        )

        # Isolation: two independent repos, two independent event logs.
        repo_a = self.work_dir / "hierarchical"
        repo_b = self.work_dir / "flat"
        self.assertTrue((repo_a / ".git").exists())
        self.assertTrue((repo_b / ".git").exists())
        log_a = subprocess.run(
            ["git", "-C", str(repo_a), "log", "--oneline", "main"], check=True, capture_output=True, text=True
        ).stdout.strip().splitlines()
        log_b = subprocess.run(
            ["git", "-C", str(repo_b), "log", "--oneline", "main"], check=True, capture_output=True, text=True
        ).stdout.strip().splitlines()
        # Condition A reached Done, so its real marker commit actually
        # got merged into main (initial + marker + merge = 3 commits);
        # condition B halted, so its own marker commits exist only on
        # its own ticket branch and never touched main (still just the
        # 1 initial commit) - proving these are genuinely independent
        # runs against independent repos, not one result duplicated twice.
        self.assertEqual(len(log_a), 3)
        self.assertEqual(len(log_b), 1)
        self.assertTrue((repo_a / "hierarchical.marker").exists())
        self.assertFalse((repo_b / "flat.marker").exists())
        self.assertTrue((self.work_dir / "hierarchical" / "eventlog.db").exists())
        self.assertTrue((self.work_dir / "flat" / "eventlog.db").exists())

    def test_a_relative_work_dir_is_resolved_to_absolute(self) -> None:
        """Regression test: the first real live run (see cs3ip-fyp-overview
        memory) failed against Docker with 'invalid characters for a local
        volume name' - Docker bind mounts require an absolute host path,
        and workspace.repo_path ended up relative because work_dir was
        passed relative (the CLI's own default, Path("data/comparisons"),
        is relative too). FakeSandboxController doesn't touch Docker so it
        can't reproduce that exact failure, but it can prove the actual
        fix: work_dir gets resolved to absolute before any GitWorkspace/
        EventLog path is built from it, regardless of what the caller
        passed in.
        """
        original_cwd = Path.cwd()
        os.chdir(self._tmpdir.name)
        self.addCleanup(os.chdir, original_cwd)

        condition_a = ConditionSpec(label="hierarchical", roles_path=Path("hierarchical"))
        condition_b = ConditionSpec(label="flat", roles_path=Path("flat"))
        task = EvalTask(task_id="TCK-REL", title="Demo", prompt="Do the thing.", checkpoints=[])
        relative_work_dir = Path("relative-work")

        comparison = run_structure_comparison(
            task, condition_a, condition_b, self.template_repo, relative_work_dir,
            sandbox_factory_builder=self._sandbox_factory_builder,
            agent_pool_builder=self._agent_pool_builder,
        )

        self.assertEqual(comparison.condition_a.run.ticket_status, "done")
        resolved_repo = Path(self._tmpdir.name) / "relative-work" / "hierarchical"
        self.assertTrue(resolved_repo.is_absolute())
        self.assertTrue((resolved_repo / ".git").exists())
        self.assertTrue((resolved_repo / "eventlog.db").exists())


if __name__ == "__main__":
    unittest.main()
