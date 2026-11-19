"""Score one sde-migrate-package-manager arm under both rubrics.

A comparison run grades each arm under the task id it was launched
with. This scores the arm's repository again under the faithful port and
the corrected sibling, each on its own fresh clone, so neither sees the
.venv that the faithful `uv sync` step may leave behind. A clone of the
arm's repository is main as committed, which is what the grader saw.

Prints one line, `upstream=a/8 strict=b/8`, then each checkpoint.

    .venv/bin/python experiments/scripts/rescore_migrate.py data/sde-migrate-package-manager-batch/run1/flat
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from wjfyp.eval.scoring import TaskScore, score_task  # noqa: E402
from wjfyp.eval.task import GradingContext  # noqa: E402
from wjfyp.eval.tasks import TASKS  # noqa: E402

UPSTREAM = "sde-migrate-package-manager"
STRICT = UPSTREAM + "-strict"


def score(source: Path, task_id: str) -> TaskScore:
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        subprocess.run(["git", "clone", "-q", str(source), str(repo)], check=True)
        return score_task(TASKS[task_id], GradingContext(ticket_id=task_id, repo_path=str(repo)))


def main() -> int:
    source = Path(sys.argv[1]).resolve()
    results = {task_id: score(source, task_id) for task_id in (UPSTREAM, STRICT)}
    up, st = results[UPSTREAM], results[STRICT]
    print(f"upstream={up.points_earned}/{up.points_total} strict={st.points_earned}/{st.points_total}")
    for task_id, result in results.items():
        for r in result.checkpoint_results:
            mark = "PASS" if r.earned else "fail"
            print(f"  {task_id} [{r.points}] {r.checkpoint_id} {mark} {r.error or ''}".rstrip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
