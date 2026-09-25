"""Ported from TheAgentCompany's sde-write-a-unit-test-for-append_file-
function (github.com/TheAgentCompany/TheAgentCompany). Checkpoint logic
transcribed from their evaluator.py, fetched verbatim 2026-09-25 - see
cs3ip-evaluation-detail memory.

Adaptations from the original:
- Paths are relative to the repo root (ctx.repo_path) rather than the
  hardcoded /workspace/openhands/ container path.
- checkpoint1 is adapted the same way as sde_fix_factual_mistake.py's:
  their check_repo_exists('openhands') verifies the agent's own clone
  step, which GitWorkspace already guarantees here, so this checks the
  actual source file the task is about instead.
- The pytest invocations add --color=no and strip ANSI escape codes
  before regex-matching the pass/fail counts, which the original
  doesn't do. This isn't cosmetic: confirmed empirically while building
  Layer B that pytest emits colour codes even through a piped
  subprocess, which silently breaks naive regex parsing of its output
  (see the Layer B bug-fix commit and cs3ip-fyp-overview memory) -
  porting the original's parsing logic verbatim here would have
  reintroduced the exact same class of bug.

Verification status, stated honestly rather than left implicit:
checkpoint1 (file exists) and checkpoint2 (AST function check) are
fully portable and unit-tested against small synthetic fixtures in
tests/test_sde_write_a_unit_test.py, no external tooling needed.
checkpoint3 and checkpoint4 shell out to `poetry run pytest` against
whatever repo is checked out, which needs a real Poetry-managed Python
project (the actual OpenHands repo, or an equivalent) to execute at
all - not available in this dev environment (no Poetry, no full
OpenHands checkout), so their logic is transcribed and reviewed
carefully but not executed end to end here. Same unverified-pending-
infrastructure status as the Docker-dependent sandbox code (FYP-22).

checkpoint4 is also worth flagging for what it actually does, not just
how: it deletes the agent's test function from the repo as part of
grading it (to prove the test genuinely exercises the code, by showing
coverage drops without it), then leaves the file in that mutated state.
That's the real evaluator.py's own design, not something introduced by
this port - kept faithful to it rather than smoothing it over, but a
caller relying on the repo's state being unchanged after grading needs
to know this checkpoint alone violates that.
"""

from __future__ import annotations

import ast
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from wjfyp.eval.task import Checkpoint, EvalTask, GradingContext
from wjfyp.eval.tasks import register
from wjfyp.sandbox.pytest_output import strip_ansi

SOURCE_FILE = "openhands/runtime/plugins/agent_skills/file_ops/file_ops.py"
TEST_FILE = "tests/unit/test_agent_skill.py"
COVERAGE_XML = "test_agent_skill_coverage.xml"
FUNCTION_NAME = "test_append_file"

_STAT_PATTERNS = {
    "passed": re.compile(r"(\d+) passed"),
    "failed": re.compile(r"(\d+) failed"),
    "skipped": re.compile(r"(\d+) skipped"),
}


def _checkpoint_source_file_exists(ctx: GradingContext) -> bool:
    return (Path(ctx.repo_path) / SOURCE_FILE).exists()


def _checkpoint_test_function_exists(ctx: GradingContext) -> bool:
    test_path = Path(ctx.repo_path) / TEST_FILE
    tree = ast.parse(test_path.read_text())
    return any(
        isinstance(node, ast.FunctionDef) and node.name == FUNCTION_NAME for node in ast.walk(tree)
    )


def _run_pytest_with_stats(repo_path: str, function_name: str = "") -> dict[str, int]:
    target = f"{TEST_FILE}::{function_name}" if function_name else TEST_FILE
    result = subprocess.run(
        [
            "poetry",
            "run",
            "pytest",
            "--forked",
            "--color=no",
            "--cov=openhands",
            f"--cov-report=xml:{COVERAGE_XML}",
            "-svv",
            target,
        ],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    output = strip_ansi(result.stdout)
    stats = {"passed": 0, "failed": 0, "skipped": 0}
    for key, pattern in _STAT_PATTERNS.items():
        match = pattern.search(output)
        if match:
            stats[key] = int(match.group(1))
    stats["total"] = stats["passed"] + stats["failed"] + stats["skipped"]
    return stats


def _line_coverage_rate(repo_path: str) -> float | None:
    tree = ET.parse(Path(repo_path) / COVERAGE_XML)
    for class_elem in tree.getroot().findall(".//class"):
        if class_elem.get("name") == "file_ops.py":
            rate = class_elem.get("line-rate")
            return float(rate) if rate is not None else None
    return None


def _remove_test_function(repo_path: str) -> bool:
    """Deletes the test_append_file function from TEST_FILE in place -
    see this module's docstring: this is a faithful port of the
    original's destructive verification step, not a bug.
    """
    path = Path(repo_path) / TEST_FILE
    lines = path.read_text().splitlines(keepends=True)

    start = next((i for i, line in enumerate(lines) if line.strip().startswith(f"def {FUNCTION_NAME}(")), None)
    if start is None:
        return False

    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].strip() and not lines[j].startswith(" "):
            end = j
            break

    del lines[start:end]
    path.write_text("".join(lines))
    return True


def _checkpoint_test_passes(ctx: GradingContext) -> bool:
    stats = _run_pytest_with_stats(ctx.repo_path, function_name=FUNCTION_NAME)
    return stats["passed"] == 1


def _checkpoint_coverage_drops_without_the_test(ctx: GradingContext) -> bool:
    before_stats = _run_pytest_with_stats(ctx.repo_path)
    before_coverage = _line_coverage_rate(ctx.repo_path)
    if before_coverage is None:
        return False

    if not _remove_test_function(ctx.repo_path):
        return False

    after_stats = _run_pytest_with_stats(ctx.repo_path)
    expected_after = {**before_stats, "passed": before_stats["passed"] - 1, "total": before_stats["total"] - 1}
    if after_stats != expected_after:
        return False

    after_coverage = _line_coverage_rate(ctx.repo_path)
    if after_coverage is None:
        return False

    return after_coverage < before_coverage


TASK = register(
    EvalTask(
        task_id="sde-write-a-unit-test-for-append_file-function",
        title="Write a unit test for the append_file function",
        prompt=(
            f"Find the function append_file in {SOURCE_FILE}. Write a unit test "
            f"named '{FUNCTION_NAME}' for it in {TEST_FILE}, in the same repo, "
            "using the existing Poetry-managed test setup."
        ),
        checkpoints=[
            Checkpoint(
                id="source-file-exists",
                description="file_ops.py is present in the repo",
                points=1,
                grader=_checkpoint_source_file_exists,
            ),
            Checkpoint(
                id="test-function-exists",
                description=f"{FUNCTION_NAME} is defined in {TEST_FILE}",
                points=1,
                grader=_checkpoint_test_function_exists,
            ),
            Checkpoint(
                id="test-passes",
                description=f"{FUNCTION_NAME} passes under pytest",
                points=2,
                grader=_checkpoint_test_passes,
            ),
            Checkpoint(
                id="coverage-drops-without-test",
                description="removing the test drops file_ops.py's line coverage, proving it isn't vacuous",
                points=1,
                grader=_checkpoint_coverage_drops_without_the_test,
            ),
        ],
        source="TheAgentCompany:sde-write-a-unit-test-for-append_file-function",
    )
)
