"""Both rubrics for sde-migrate-package-manager, checked against small
synthetic repositories shaped like sotopia at 71d6199 and at ed626ec.

The faithful port's last step shells out to `uv sync`. These tests swap
the executable for `true` or `false` so the logic around it is covered
without a network or a multi-gigabyte install. The real step is
exercised by hand against the reference solution and is unearnable for
an agent, see the module docstring.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from wjfyp.eval import tasks
from wjfyp.eval.scoring import score_task
from wjfyp.eval.task import GradingContext
from wjfyp.eval.tasks import TASKS
from wjfyp.eval.tasks import sde_migrate_package_manager as upstream
from wjfyp.eval.tasks import sde_migrate_package_manager_strict as strict

UPSTREAM_ID = "sde-migrate-package-manager"
STRICT_ID = "sde-migrate-package-manager-strict"

TEMPLATE = Path.home() / "Projects" / "sotopia-target"

_POETRY_PYPROJECT = """\
[tool.poetry]
name = "sotopia"
version = "0.1.0"

[tool.poetry.dependencies]
python = ">=3.10, <3.13"
litellm = ">=1.23.12,<1.41.0"

[build-system]
requires = ["poetry-core"]
build-backend = "poetry.core.masonry.api"
"""

_EXTRAS = """\
[project.optional-dependencies]
anthropic = ["anthropic"]
groq = ["groq"]
cohere = ["cohere"]
google-generativeai = ["google-generativeai"]
examples = ["transformers", "datasets", "scipy", "torch", "pandas"]
chat = ["fastapi"]
test = ["pytest"]
"""

_DEPENDENCIES = """\
dependencies = [
    "lxml>=4.9.3,<6.0.0",
    "openai>=1.11.0,<2.0.0",
    "langchain>=0.2.5,<0.3.0",
    "rich>=13.6.0,<14.0.0",
    "PettingZoo==1.24.3",
    "redis-om>=0.3.0,<0.4.0",
    "gin-config>=0.5.0,<0.6.0",
    "absl-py>=2.0.0,<3.0.0",
    "together>=0.2.4,<0.3.0",
    "pydantic>=2.5.0,<3.0.0",
    "beartype>=0.14.0,<0.15.0",
    "langchain-openai>=0.1.8,<0.2.0",
    "litellm>=1.23.12,<1.41.0",
    "hiredis>=3.0.0",
]
"""

_UV_PYPROJECT = (
    '[project]\nname = "sotopia"\nversion = "0.1.0"\n'
    + _DEPENDENCIES
    + "\n"
    + _EXTRAS
    + '\n[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n'
    + '\n[tool.uv]\ndev-dependencies = ["mypy"]\n'
)

_MIGRATED_FILES = {
    ".github/workflows/mypy.yml": "steps:\n  - run: uv sync --extra test\n  - run: uv run mypy --strict .\n",
    ".github/workflows/publish-to-pypi.yml": "steps:\n  - run: |\n      uv build\n      uv publish\n",
    "docs/pages/contribution/contribution.md": "Install uv with `pip install uv; uv sync --all-extras`.\n",
    "docs/pages/examples/examples.mdx": "Use `uv run --extra examples <script>` if you are using uv.\n",
    "docs/pages/index.mdx": "If you are developing with uv, run `uv sync --extra examples`.\n",
    "pyproject.toml": _UV_PYPROJECT,
}

_BASE_FILES = {
    ".github/workflows/mypy.yml": "steps:\n  - run: poetry install\n  - run: poetry run mypy --strict .\n",
    ".github/workflows/publish-to-pypi.yml": "steps:\n  - run: poetry publish --build\n",
    "docs/pages/contribution/contribution.md": "Please manually install Redis.\n",
    "docs/pages/examples/examples.mdx": "Or `poetry install -E examples` if you are using poetry.\n",
    "docs/pages/index.mdx": "If you are using poetry, run `poetry install -E examples`.\n",
    "pyproject.toml": _POETRY_PYPROJECT,
}


def _repo(tmp_path: Path, files: dict[str, str], **overrides: str) -> Path:
    for relative, content in {**files, **overrides}.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return tmp_path


def _score(task_id: str, repo: Path):
    return score_task(TASKS[task_id], GradingContext(ticket_id="T", repo_path=str(repo)))


def _earned(task_id: str, repo: Path) -> dict[str, bool]:
    return {r.checkpoint_id: r.earned for r in _score(task_id, repo).checkpoint_results}


@pytest.fixture
def uv_succeeds(monkeypatch):
    monkeypatch.setattr(upstream, "UV", "true")


@pytest.fixture
def uv_fails(monkeypatch):
    monkeypatch.setattr(upstream, "UV", "false")


# Registration and weights


def test_both_rubrics_are_registered_and_score_out_of_eight():
    assert TASKS[UPSTREAM_ID].total_points == 8
    assert TASKS[STRICT_ID].total_points == 8
    assert TASKS[UPSTREAM_ID].prompt == TASKS[STRICT_ID].prompt


def test_the_port_keeps_upstream_weights():
    assert [(c.id, c.points) for c in TASKS[UPSTREAM_ID].checkpoints] == [
        ("workflows-use-uv", 2),
        ("docs-use-uv", 3),
        ("pyproject-uses-uv", 3),
    ]


def test_the_strict_rubric_keeps_the_upstream_split_by_file_group():
    points = {c.id: c.points for c in TASKS[STRICT_ID].checkpoints}
    assert all(p == 1 for p in points.values())
    assert sum(points[k] for k in points if "workflow" in k) == 2
    assert sum(points[k] for k in points if "doc" in k) == 3
    assert sum(points[k] for k in points if k.startswith("pyproject")) == 3


def test_the_prompt_does_not_leak_the_hidden_strings():
    prompt = TASKS[UPSTREAM_ID].prompt
    assert "mypy" not in prompt
    assert "uv publish" not in prompt
    assert "the-agent-company.com" not in prompt


def test_the_earlier_tasks_are_still_registered():
    for task_id in (
        "sde-fix-factual-mistake",
        "sde-write-a-unit-test-for-append_file-function",
        "sde-write-a-unit-test-for-append_file-function-strict",
    ):
        assert task_id in tasks.TASKS


# The faithful port


def test_upstream_base_scores_nothing(tmp_path, uv_succeeds):
    assert _score(UPSTREAM_ID, _repo(tmp_path, _BASE_FILES)).points_earned == 0


def test_upstream_full_migration_with_a_working_sync_scores_eight(tmp_path, uv_succeeds):
    score = _score(UPSTREAM_ID, _repo(tmp_path, _MIGRATED_FILES))
    assert score.points_earned == 8
    assert score.fully_complete


def test_upstream_full_migration_without_a_working_sync_scores_five(tmp_path, uv_fails):
    assert _earned(UPSTREAM_ID, _repo(tmp_path, _MIGRATED_FILES)) == {
        "workflows-use-uv": True,
        "docs-use-uv": True,
        "pyproject-uses-uv": False,
    }


def test_upstream_workflows_are_all_or_nothing(tmp_path, uv_succeeds):
    repo = _repo(tmp_path, _MIGRATED_FILES, **{".github/workflows/publish-to-pypi.yml": "run: uv build\n"})
    assert not _earned(UPSTREAM_ID, repo)["workflows-use-uv"]


def test_upstream_mypy_workflow_needs_the_exact_hidden_command(tmp_path, uv_succeeds):
    repo = _repo(tmp_path, _MIGRATED_FILES, **{".github/workflows/mypy.yml": "run: uv run mypy .\n"})
    assert not _earned(UPSTREAM_ID, repo)["workflows-use-uv"]


def test_upstream_missing_workflow_fails(tmp_path, uv_succeeds):
    repo = _repo(tmp_path, _MIGRATED_FILES)
    (repo / ".github/workflows/mypy.yml").unlink()
    assert not _earned(UPSTREAM_ID, repo)["workflows-use-uv"]


def test_upstream_one_poetry_mention_fails_every_doc_point(tmp_path, uv_succeeds):
    repo = _repo(tmp_path, _MIGRATED_FILES, **{"docs/pages/index.mdx": "uv sync, or Poetry if you prefer\n"})
    assert not _earned(UPSTREAM_ID, repo)["docs-use-uv"]


def test_upstream_doc_substring_accepts_uvicorn(tmp_path, uv_succeeds):
    repo = _repo(tmp_path, _MIGRATED_FILES, **{"docs/pages/contribution/contribution.md": "Run uvicorn.\n"})
    assert _earned(UPSTREAM_ID, repo)["docs-use-uv"]


def test_upstream_pyproject_with_poetry_never_reaches_the_sync(tmp_path, monkeypatch):
    monkeypatch.setattr(upstream, "UV", str(tmp_path / "no-such-uv"))
    repo = _repo(tmp_path, _MIGRATED_FILES, **{"pyproject.toml": _UV_PYPROJECT + "\n# was poetry\n"})
    result = next(r for r in _score(UPSTREAM_ID, repo).checkpoint_results if r.checkpoint_id == "pyproject-uses-uv")
    assert not result.earned
    assert result.error is None


def test_upstream_missing_uv_is_an_error_not_a_silent_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(upstream, "UV", str(tmp_path / "no-such-uv"))
    repo = _repo(tmp_path, _MIGRATED_FILES)
    result = next(r for r in _score(UPSTREAM_ID, repo).checkpoint_results if r.checkpoint_id == "pyproject-uses-uv")
    assert not result.earned
    assert result.error is not None and "FileNotFoundError" in result.error


# The corrected rubric


def test_strict_base_scores_nothing(tmp_path):
    assert _score(STRICT_ID, _repo(tmp_path, _BASE_FILES)).points_earned == 0


def test_strict_full_migration_scores_eight_offline(tmp_path):
    assert _score(STRICT_ID, _repo(tmp_path, _MIGRATED_FILES)).points_earned == 8


def test_strict_gives_partial_credit_per_file(tmp_path):
    repo = _repo(
        tmp_path,
        _BASE_FILES,
        **{
            ".github/workflows/mypy.yml": _MIGRATED_FILES[".github/workflows/mypy.yml"],
            "docs/pages/index.mdx": _MIGRATED_FILES["docs/pages/index.mdx"],
        },
    )
    earned = _earned(STRICT_ID, repo)
    assert {k for k, v in earned.items() if v} == {"mypy-workflow-uses-uv", "index-doc-uses-uv"}
    # The same repo earns nothing upstream, because each group is all or nothing.
    assert _score(UPSTREAM_ID, repo).points_earned == 0


def test_strict_docs_need_uv_as_a_word(tmp_path):
    repo = _repo(tmp_path, _MIGRATED_FILES, **{"docs/pages/contribution/contribution.md": "Run uvicorn.\n"})
    assert not _earned(STRICT_ID, repo)["contribution-doc-uses-uv"]


def test_strict_pyproject_needs_no_uv_mention(tmp_path):
    no_tool_uv = _UV_PYPROJECT.split("[tool.uv]")[0]
    earned = _earned(STRICT_ID, _repo(tmp_path, _MIGRATED_FILES, **{"pyproject.toml": no_tool_uv}))
    assert earned["pyproject-project-table"]
    assert earned["pyproject-extras-preserved"]
    assert earned["pyproject-build-not-poetry"]


def test_strict_invalid_requirement_fails_the_project_table(tmp_path):
    broken = _UV_PYPROJECT.replace('"hiredis>=3.0.0"', '"hiredis = ^3.0.0"')
    assert not _earned(STRICT_ID, _repo(tmp_path, _MIGRATED_FILES, **{"pyproject.toml": broken}))[
        "pyproject-project-table"
    ]


def test_strict_dropped_dependency_fails_the_project_table(tmp_path):
    dropped = _UV_PYPROJECT.replace('    "litellm>=1.23.12,<1.41.0",\n', "")
    assert not _earned(STRICT_ID, _repo(tmp_path, _MIGRATED_FILES, **{"pyproject.toml": dropped}))[
        "pyproject-project-table"
    ]


def test_strict_dependency_names_are_compared_canonically(tmp_path):
    renamed = _UV_PYPROJECT.replace("PettingZoo==1.24.3", "pettingzoo==1.24.3").replace("redis-om", "redis_om")
    assert _earned(STRICT_ID, _repo(tmp_path, _MIGRATED_FILES, **{"pyproject.toml": renamed}))[
        "pyproject-project-table"
    ]


def test_strict_dropped_extra_fails(tmp_path):
    dropped = _UV_PYPROJECT.replace('chat = ["fastapi"]\n', "")
    assert not _earned(STRICT_ID, _repo(tmp_path, _MIGRATED_FILES, **{"pyproject.toml": dropped}))[
        "pyproject-extras-preserved"
    ]


def test_strict_extra_missing_a_package_fails(tmp_path):
    thinned = _UV_PYPROJECT.replace('"scipy", "torch", ', "")
    assert not _earned(STRICT_ID, _repo(tmp_path, _MIGRATED_FILES, **{"pyproject.toml": thinned}))[
        "pyproject-extras-preserved"
    ]


def test_strict_leftover_poetry_table_fails_the_build_check(tmp_path):
    leftover = _UV_PYPROJECT + '\n[tool.poetry]\nname = "sotopia"\n'
    assert not _earned(STRICT_ID, _repo(tmp_path, _MIGRATED_FILES, **{"pyproject.toml": leftover}))[
        "pyproject-build-not-poetry"
    ]


def test_strict_poetry_core_backend_fails_the_build_check(tmp_path):
    poetry_backend = _UV_PYPROJECT.replace('requires = ["hatchling"]', 'requires = ["poetry-core>=2"]').replace(
        '"hatchling.build"', '"poetry.core.masonry.api"'
    )
    assert not _earned(STRICT_ID, _repo(tmp_path, _MIGRATED_FILES, **{"pyproject.toml": poetry_backend}))[
        "pyproject-build-not-poetry"
    ]


def test_strict_unparseable_toml_is_an_error(tmp_path):
    repo = _repo(tmp_path, _MIGRATED_FILES, **{"pyproject.toml": "[project\n"})
    results = {r.checkpoint_id: r for r in _score(STRICT_ID, repo).checkpoint_results}
    assert not results["pyproject-project-table"].earned
    assert "TOMLDecodeError" in (results["pyproject-project-table"].error or "")


# Against the real template

requires_template = pytest.mark.skipif(not TEMPLATE.exists(), reason="sotopia-target not present")


@requires_template
def test_the_pinned_base_lists_match_the_real_template():
    data = tomllib.loads((TEMPLATE / "pyproject.toml").read_text())
    poetry = data["tool"]["poetry"]
    runtime = {
        name.lower()
        for name, spec in poetry["dependencies"].items()
        if name != "python" and not (isinstance(spec, dict) and spec.get("optional"))
    }
    assert runtime == set(strict.BASE_DEPENDENCIES)
    assert {k: set(v) for k, v in poetry["extras"].items()} == {k: set(v) for k, v in strict.BASE_EXTRAS.items()}


@requires_template
def test_the_untouched_template_scores_nothing_under_both(monkeypatch):
    # uv is never reached on the base, the Poetry text fails the check first.
    monkeypatch.setattr(upstream, "UV", "false")
    assert _score(UPSTREAM_ID, TEMPLATE).points_earned == 0
    assert _score(STRICT_ID, TEMPLATE).points_earned == 0
