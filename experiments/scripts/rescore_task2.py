"""Score one task-2 arm under both rubrics, upstream and corrected.

Why this exists. The upstream rubric's coverage checkpoint deletes
test_append_file from the working tree and leaves it deleted, so the
file left behind after a run is NOT what either rubric saw. Each rubric
is therefore scored here on its own fresh copy of the pre-grading state.

Two ways to get that state:

    repo <dir>       a run's own repo, still on disk. A clone of it is
                     main as committed, which is exactly what the grader
                     started from, since the grader's edits are never
                     committed. Used for the solo runs.
    artifacts <dir>  a kept arm directory (data/task2-batch/runN/<arm>),
                     whose repo was reclaimed. Rebuilt as the template
                     plus the test file from the ticket branch if the
                     branch was merged, or the plain template if it was
                     not. Used for the team runs, and checked against the
                     score each team run recorded at the time.

Prints one line, `upstream=a/5 strict=b/5`.

    WJFYP_GRADER_PYTHON=... .venv/bin/python experiments/scripts/rescore_task2.py repo data/task2-solo/run1/solo
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from wjfyp.eval.scoring import score_task  # noqa: E402
from wjfyp.eval.task import GradingContext  # noqa: E402
from wjfyp.eval.tasks import TASKS  # noqa: E402

TEMPLATE = Path.home() / "Projects/openhands-target-append-file"
UPSTREAM = "sde-write-a-unit-test-for-append_file-function"
STRICT = UPSTREAM + "-strict"
TEST_FILE = "tests/unit/test_agent_skill.py"


def _fresh_copy(mode: str, source: Path, dest: Path) -> None:
    if mode == "repo":
        subprocess.run(["git", "clone", "-q", str(source), str(dest)], check=True)
        return
    subprocess.run(["git", "clone", "-q", str(TEMPLATE), str(dest)], check=True)
    if (source / "merged").read_text().strip() == "merged":
        shutil.copyfile(source / "test_agent_skill.written.py", dest / TEST_FILE)


def score(mode: str, source: Path, task_id: str) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        _fresh_copy(mode, source, repo)
        result = score_task(TASKS[task_id], GradingContext(ticket_id=task_id, repo_path=str(repo)))
        return f"{result.points_earned}/{result.points_total}"


def main() -> int:
    if not os.environ.get("WJFYP_GRADER_PYTHON"):
        print("WJFYP_GRADER_PYTHON is not set, 3 of 5 points would be unscoreable", file=sys.stderr)
        return 1
    mode, source = sys.argv[1], Path(sys.argv[2]).resolve()
    print(f"upstream={score(mode, source, UPSTREAM)} strict={score(mode, source, STRICT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
