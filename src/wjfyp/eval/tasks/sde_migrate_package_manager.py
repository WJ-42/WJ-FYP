"""Ported from TheAgentCompany's sde-migrate-package-manager
(github.com/TheAgentCompany/TheAgentCompany, workspaces/tasks/
sde-migrate-package-manager). Checkpoint logic transcribed from their
evaluator.py at upstream commit 98b68ef, read in full on 2026-11-19.

The target is sotopia (github.com/sotopia-lab/sotopia, MIT) at 71d6199,
the parent of ed626ec. ed626ec is the sotopia maintainers' own Poetry to
uv migration (PR #218), so it doubles as a human reference solution.
The template lives at ~/Projects/sotopia-target, a fresh one-commit
repository holding that tree plus a pytest.ini that scopes collection
away from tests/, which cannot import in the sandbox.

Upstream checkpoints and weights, unchanged here:

    workflows-use-uv       2   mypy.yml and publish-to-pypi.yml
    docs-use-uv            3   contribution.md, examples.mdx, index.mdx
    pyproject-uses-uv      3   pyproject.toml, then `uv sync --all-extras`

Each is all or nothing over its files. A workflow file passes when its
lowercased text has no "poetry" and contains a fixed command string,
"uv run mypy --strict ." for mypy.yml and "uv publish" for
publish-to-pypi.yml. Neither string appears in the prompt. Both are the
literal translation of the Poetry command already in the file
("poetry run mypy --strict ." and "poetry publish --build"), so an agent
that translates line by line can find them, but an agent that rewrites
the workflow in its own way can fail with a correct migration. A doc
passes when its lowercased text has no "poetry" and contains the
substring "uv". The pyproject check is case sensitive, as upstream
wrote it.

THE PYPROJECT CHECKPOINT IS UNEARNABLE FOR AN AGENT. Its last step runs
`uv sync --all-extras`. sotopia pins litellm>=1.23.12,<1.41.0 and every
litellm release in that range has since been removed from the PyPI
index (checked 2026-11-19, the simple index jumps from 1.14.4 straight
to 1.53.1). A fresh resolve therefore fails with "No solution found". The
only way through is a uv.lock written while those versions were still
listed, because uv then fetches the locked file URLs directly and those
files are still served. ed626ec ships such a lock, so the human
reference can earn the checkpoint. An agent cannot. The sandbox has no
network, and with network a fresh `uv lock` fails for the same reason.
This mirrors task 2's coverage checkpoint, documented in
sde_write_a_unit_test_strict.py, and is the reason the corrected
sibling in sde_migrate_package_manager_strict.py exists. Unearnable
here means 3 of the 8 points, so the faithful ceiling for an agent is
5/8.

Adaptations from the original:
- Paths are relative to ctx.repo_path instead of /workspace/sotopia.
- The prompt drops the internal GitLab URL and the sentence telling the
  agent to clone into /workspace. GitWorkspace has already checked the
  repository out, as with the other ports. Every other word is upstream's.
- `uv sync` gets a one hour timeout. Upstream has none. With a lockfile
  present the step downloads several gigabytes (torch and the CUDA
  wheels via the examples extra) and an unattended harness must not hang
  on it. A timeout counts as a failed checkpoint through score_task.
- The `uv` executable comes from WJFYP_UV, falling back to whatever `uv`
  is on PATH. If it is missing the checkpoint fails with an error rather
  than silently, as with WJFYP_GRADER_PYTHON in sde_write_a_unit_test.py.

One grading side effect is worth knowing. `uv sync` creates .venv inside the
graded repository, and with a lockfile it installs into it. Upstream has
the same effect. Nothing else in this module writes to the repo.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from wjfyp.eval.task import Checkpoint, EvalTask, GradingContext
from wjfyp.eval.tasks import register

TASK_ID = "sde-migrate-package-manager"

WORKFLOW_DIR = ".github/workflows"
# File name to the command its lowercased text must contain.
WORKFLOW_COMMANDS: dict[str, str] = {
    "mypy.yml": "uv run mypy --strict .",
    "publish-to-pypi.yml": "uv publish",
}
DOC_FILES: tuple[str, ...] = (
    "docs/pages/contribution/contribution.md",
    "docs/pages/examples/examples.mdx",
    "docs/pages/index.mdx",
)
PYPROJECT = "pyproject.toml"

UV = os.environ.get("WJFYP_UV", "uv")
UV_SYNC_TIMEOUT_SECONDS = 3600

# Upstream's task.md, verbatim apart from the two adaptations listed in
# the module docstring.
PROMPT = (
    "Your task is to migrate the Sotopia repository's dependency management system "
    "from poetry (https://python-poetry.org/) to UV (https://github.com/astral-sh/uv).\n"
    "\n"
    "This involves:\n"
    "1. Updating all GitHub workflow files to use UV instead of poetry for dependency management\n"
    "2. Updating documentation files to reflect the change in package manager\n"
    "3. Ensuring all commands and instructions are updated to use UV syntax\n"
    "\n"
    "The migration should maintain all existing functionality while taking advantage "
    "of UV's improved performance."
)


def workflow_file_uses_uv(repo_path: str, name: str) -> bool:
    """One workflow file, judged exactly as upstream judges each file."""
    path = Path(repo_path) / WORKFLOW_DIR / name
    if not path.exists():
        return False
    content = path.read_text().lower()
    return "poetry" not in content and WORKFLOW_COMMANDS[name] in content


def _checkpoint_workflows_use_uv(ctx: GradingContext) -> bool:
    return all(workflow_file_uses_uv(ctx.repo_path, name) for name in WORKFLOW_COMMANDS)


def _doc_file_uses_uv(repo_path: str, relative: str) -> bool:
    content = (Path(repo_path) / relative).read_text().lower()
    return "poetry" not in content and "uv" in content


def _checkpoint_docs_use_uv(ctx: GradingContext) -> bool:
    # Upstream wraps the loop in one try and returns False on any error,
    # so a missing doc fails the whole checkpoint. Raising here does the
    # same through score_task, with the error recorded.
    return all(_doc_file_uses_uv(ctx.repo_path, doc) for doc in DOC_FILES)


def _checkpoint_pyproject_uses_uv(ctx: GradingContext) -> bool:
    content = (Path(ctx.repo_path) / PYPROJECT).read_text()
    if "poetry" in content or "uv" not in content:
        return False
    result = subprocess.run(
        [UV, "sync", "--all-extras"],
        cwd=ctx.repo_path,
        capture_output=True,
        text=True,
        timeout=UV_SYNC_TIMEOUT_SECONDS,
    )
    return result.returncode == 0


TASK = register(
    EvalTask(
        task_id=TASK_ID,
        title="Migrate sotopia from Poetry to uv",
        prompt=PROMPT,
        checkpoints=[
            Checkpoint(
                id="workflows-use-uv",
                description="mypy.yml and publish-to-pypi.yml drop poetry and run uv",
                points=2,
                grader=_checkpoint_workflows_use_uv,
            ),
            Checkpoint(
                id="docs-use-uv",
                description="contribution.md, examples.mdx and index.mdx drop poetry and mention uv",
                points=3,
                grader=_checkpoint_docs_use_uv,
            ),
            Checkpoint(
                id="pyproject-uses-uv",
                description="pyproject.toml drops poetry and `uv sync --all-extras` succeeds (unearnable, see module docstring)",
                points=3,
                grader=_checkpoint_pyproject_uses_uv,
            ),
        ],
        source="TheAgentCompany:sde-migrate-package-manager",
    )
)
