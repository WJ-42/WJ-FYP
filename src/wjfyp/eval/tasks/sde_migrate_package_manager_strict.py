"""A corrected rubric for sde-migrate-package-manager.

The faithful port (sde_migrate_package_manager.py) keeps upstream's
three all or nothing checkpoints. Two things make it a poor instrument
for this project.

1. **3 of its 8 points cannot be earned.** The pyproject checkpoint ends
   in `uv sync --all-extras`, which fails on a fresh resolve because
   sotopia's litellm pin no longer exists on the PyPI index. The
   faithful module's docstring has the detail. An agent's ceiling there
   is 5/8 however good its pyproject.toml is.
2. **The spread is coarse.** With three checkpoints worth 2, 3 and 3,
   an agent that fixes four of the five files it is graded on can still
   score 0. Partial progress, which is what a team comparison needs to
   see, is invisible.

This module does NOT modify the port. The upstream task stays
registered unchanged so results under the published rubric remain
reproducible and the two can be reported side by side. This is a second
task id over the same repository, prompt and target.

What changed, and why:

* **One checkpoint per graded file**, five files at 1 point each. The
  per file test is the upstream one, so the bar for a file is unchanged
  and only the all or nothing grouping goes. The two workflow strings
  stay exactly as upstream wrote them ("uv run mypy --strict ." and
  "uv publish"), because each is the literal translation of the Poetry
  command already in its file.
* **The docs require uv as a word.** Upstream looks for the substring
  "uv", which words such as "uvicorn" or "uvloop" also contain. Here
  the match is `\\buv\\b` on the lowercased text. No other change to the
  doc test.
* **The resolve step is replaced by an offline structural check** of
  pyproject.toml, split three ways at 1 point each. It parses the file
  with tomllib and every requirement string with packaging, so it needs
  no network and gives the same answer every day. The three parts are
  the [project] table (name present, every dependency a valid PEP 508
  string, every runtime dependency of the base still declared), the
  extras (every extra of the base still present with the same packages,
  every entry valid PEP 508), and the build system (a backend is
  declared, it is not Poetry's, nothing in build requires names poetry,
  and no [tool.poetry] table is left behind).
* **The substring tests on pyproject.toml are dropped.** Upstream fails
  the file if the text "poetry" appears anywhere and passes it only if
  "uv" appears somewhere. The first is covered structurally by the
  build system part. The second rewards an incidental [tool.uv] table
  and fails a valid PEP 621 file that never names uv, which is what
  `uv init` itself can produce.

Weights, totalling 8 so scores compare directly with the port:

    mypy-workflow-uses-uv          1
    publish-workflow-uses-uv       1
    contribution-doc-uses-uv       1
    examples-doc-uses-uv           1
    index-doc-uses-uv              1
    pyproject-project-table        1
    pyproject-extras-preserved     1
    pyproject-build-not-poetry     1

Equal weights are deliberate. The upstream split gives the workflows
2/8, the docs 3/8 and pyproject.toml 3/8, and this keeps exactly that
split by file group while letting each file count on its own.

The base's dependency names and extras are pinned below rather than
read from git at grading time, for the reason given in
sde_write_a_unit_test_strict.py. By the time a grader runs the agent's
work has been merged, so git would return the agent's own file. A test
pins the lists against the real template so drift is caught.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from wjfyp.eval.task import Checkpoint, EvalTask, GradingContext
from wjfyp.eval.tasks import register
from wjfyp.eval.tasks.sde_migrate_package_manager import (
    DOC_FILES,
    PROMPT,
    PYPROJECT,
    TASK_ID as UPSTREAM_TASK_ID,
    workflow_file_uses_uv,
)

TASK_ID = f"{UPSTREAM_TASK_ID}-strict"

# Runtime dependencies in [tool.poetry.dependencies] at sotopia 71d6199,
# excluding python itself and the entries marked optional (those belong
# to extras and are checked there).
BASE_DEPENDENCIES: frozenset[str] = frozenset(
    {
        "lxml",
        "openai",
        "langchain",
        "rich",
        "pettingzoo",
        "redis-om",
        "gin-config",
        "absl-py",
        "together",
        "pydantic",
        "beartype",
        "langchain-openai",
        "litellm",
        "hiredis",
    }
)

# [tool.poetry.extras] at sotopia 71d6199.
BASE_EXTRAS: dict[str, frozenset[str]] = {
    "anthropic": frozenset({"anthropic"}),
    "groq": frozenset({"groq"}),
    "cohere": frozenset({"cohere"}),
    "google-generativeai": frozenset({"google-generativeai"}),
    "examples": frozenset({"transformers", "datasets", "scipy", "torch", "pandas"}),
    "chat": frozenset({"fastapi"}),
}

_UV_WORD = re.compile(r"\buv\b")


def _doc_uses_uv(repo_path: str, relative: str) -> bool:
    content = (Path(repo_path) / relative).read_text().lower()
    return "poetry" not in content and _UV_WORD.search(content) is not None


def _load_pyproject(repo_path: str) -> dict[str, Any]:
    with (Path(repo_path) / PYPROJECT).open("rb") as handle:
        return tomllib.load(handle)


def _requirement_names(entries: Any) -> set[str] | None:
    """Canonical names of a list of PEP 508 strings, or None when the
    value is not a list of strings or any entry fails to parse."""
    if not isinstance(entries, list):
        return None
    names: set[str] = set()
    for entry in entries:
        if not isinstance(entry, str):
            return None
        try:
            names.add(canonicalize_name(Requirement(entry).name))
        except InvalidRequirement:
            return None
    return names


def _checkpoint_pyproject_project_table(ctx: GradingContext) -> bool:
    project = _load_pyproject(ctx.repo_path).get("project")
    if not isinstance(project, dict) or not project.get("name"):
        return False
    names = _requirement_names(project.get("dependencies", []))
    return names is not None and BASE_DEPENDENCIES <= names


def _checkpoint_pyproject_extras_preserved(ctx: GradingContext) -> bool:
    project = _load_pyproject(ctx.repo_path).get("project")
    if not isinstance(project, dict):
        return False
    extras = project.get("optional-dependencies")
    if not isinstance(extras, dict):
        return False
    parsed: dict[str, set[str]] = {}
    for extra, entries in extras.items():
        names = _requirement_names(entries)
        if names is None:
            return False
        parsed[canonicalize_name(extra)] = names
    return all(
        canonicalize_name(extra) in parsed and packages <= parsed[canonicalize_name(extra)]
        for extra, packages in BASE_EXTRAS.items()
    )


def _checkpoint_pyproject_build_not_poetry(ctx: GradingContext) -> bool:
    data = _load_pyproject(ctx.repo_path)
    build = data.get("build-system")
    if not isinstance(build, dict):
        return False
    backend = build.get("build-backend")
    if not isinstance(backend, str) or not backend or backend.startswith("poetry"):
        return False
    requires = _requirement_names(build.get("requires", []))
    if requires is None or any(name.startswith("poetry") for name in requires):
        return False
    tool = data.get("tool", {})
    return not (isinstance(tool, dict) and "poetry" in tool)


def _workflow_checkpoint(name: str):
    def grade(ctx: GradingContext) -> bool:
        return workflow_file_uses_uv(ctx.repo_path, name)

    return grade


def _doc_checkpoint(relative: str):
    def grade(ctx: GradingContext) -> bool:
        return _doc_uses_uv(ctx.repo_path, relative)

    return grade


_CONTRIBUTION, _EXAMPLES, _INDEX = DOC_FILES

TASK = register(
    EvalTask(
        task_id=TASK_ID,
        title="Migrate sotopia from Poetry to uv (corrected rubric)",
        prompt=PROMPT,
        checkpoints=[
            Checkpoint(
                id="mypy-workflow-uses-uv",
                description="mypy.yml drops poetry and runs `uv run mypy --strict .`",
                points=1,
                grader=_workflow_checkpoint("mypy.yml"),
            ),
            Checkpoint(
                id="publish-workflow-uses-uv",
                description="publish-to-pypi.yml drops poetry and runs `uv publish`",
                points=1,
                grader=_workflow_checkpoint("publish-to-pypi.yml"),
            ),
            Checkpoint(
                id="contribution-doc-uses-uv",
                description="contribution.md drops poetry and mentions uv as a word",
                points=1,
                grader=_doc_checkpoint(_CONTRIBUTION),
            ),
            Checkpoint(
                id="examples-doc-uses-uv",
                description="examples.mdx drops poetry and mentions uv as a word",
                points=1,
                grader=_doc_checkpoint(_EXAMPLES),
            ),
            Checkpoint(
                id="index-doc-uses-uv",
                description="index.mdx drops poetry and mentions uv as a word",
                points=1,
                grader=_doc_checkpoint(_INDEX),
            ),
            Checkpoint(
                id="pyproject-project-table",
                description="[project] has a name and valid PEP 508 dependencies keeping every base dependency",
                points=1,
                grader=_checkpoint_pyproject_project_table,
            ),
            Checkpoint(
                id="pyproject-extras-preserved",
                description="every base extra survives in [project.optional-dependencies] with its packages",
                points=1,
                grader=_checkpoint_pyproject_extras_preserved,
            ),
            Checkpoint(
                id="pyproject-build-not-poetry",
                description="build backend and requires are not Poetry and no [tool.poetry] table remains",
                points=1,
                grader=_checkpoint_pyproject_build_not_poetry,
            ),
        ],
        source="TheAgentCompany:sde-migrate-package-manager (reweighted, offline pyproject check)",
    )
)
