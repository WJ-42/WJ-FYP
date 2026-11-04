"""Tests for the corrected rubric.

Two of these exist specifically because the bug they pin was found by
running the thing, not by reading it:

* test_the_def_line_alone_does_not_count pins the ast.FunctionDef.lineno
  mistake. A function's `def` line executes at import, so a body range
  starting there is hit by any test at all and an empty stub scored 5/5.
* test_upstream_cannot_separate_a_stub_from_a_real_test records the
  finding this module exists for, so a future change to either rubric
  that quietly removes the difference fails here.
"""

from __future__ import annotations

import ast
import subprocess
import tempfile
from pathlib import Path

import pytest

from wjfyp.eval.scoring import score_task
from wjfyp.eval.task import GradingContext
from wjfyp.eval.tasks import TASKS
from wjfyp.eval.tasks.sde_write_a_unit_test import SOURCE_FILE, TEST_FILE
from wjfyp.eval.tasks.sde_write_a_unit_test_strict import (
    BASE_SUITE_TEST_NAMES,
    _checkpoint_existing_suite_preserved,
    _source_function_body_range,
    _test_function_names,
)

TEMPLATE = Path.home() / "Projects" / "openhands-target-append-file"
UPSTREAM_ID = "sde-write-a-unit-test-for-append_file-function"
STRICT_ID = "sde-write-a-unit-test-for-append_file-function-strict"

STUB = "def test_append_file():\n    # Implement test logic here\n    pass\n"


def _fake_repo(tmp_path: Path, test_source: str, source_source: str | None = None) -> Path:
    """A repo-shaped directory with just the two files the graders read.
    No git, no pytest - for the checkpoints that are pure file analysis.
    """
    (tmp_path / Path(TEST_FILE).parent).mkdir(parents=True, exist_ok=True)
    (tmp_path / TEST_FILE).write_text(test_source)
    (tmp_path / Path(SOURCE_FILE).parent).mkdir(parents=True, exist_ok=True)
    (tmp_path / SOURCE_FILE).write_text(source_source or "def append_file(a, b):\n    x = 1\n    return x\n")
    return tmp_path


def _ctx(repo: Path) -> GradingContext:
    return GradingContext(ticket_id="t", repo_path=str(repo))


# --- the two rubrics are registered and comparable -------------------


def test_both_rubrics_are_registered_and_score_out_of_the_same_total():
    assert UPSTREAM_ID in TASKS
    assert STRICT_ID in TASKS
    totals = {tid: sum(c.points for c in TASKS[tid].checkpoints) for tid in (UPSTREAM_ID, STRICT_ID)}
    assert totals[UPSTREAM_ID] == totals[STRICT_ID] == 5


def test_the_port_is_left_alone():
    """The corrected rubric must not have edited the upstream one - the
    whole point is that both remain reportable."""
    upstream = {c.id: c.points for c in TASKS[UPSTREAM_ID].checkpoints}
    assert upstream == {
        "source-file-exists": 1,
        "test-function-exists": 1,
        "test-passes": 2,
        "coverage-drops-without-test": 1,
    }


def test_the_free_point_is_gone():
    """source-file-exists measured the repo, not the agent."""
    assert "source-file-exists" not in {c.id for c in TASKS[STRICT_ID].checkpoints}


def test_exercising_the_function_carries_the_most_weight():
    points = {c.id: c.points for c in TASKS[STRICT_ID].checkpoints}
    assert points["test-exercises-the-function"] == 2
    assert all(
        points["test-exercises-the-function"] > p
        for cid, p in points.items()
        if cid != "test-exercises-the-function"
    )


# --- existing-suite-preserved ---------------------------------------


def test_suite_preserved_passes_when_the_agent_appends(tmp_path):
    source = "".join(f"def {name}():\n    pass\n\n" for name in BASE_SUITE_TEST_NAMES)
    repo = _fake_repo(tmp_path, source + "\n" + STUB)
    assert _checkpoint_existing_suite_preserved(_ctx(repo)) is True


def test_suite_preserved_fails_when_the_file_is_overwritten(tmp_path):
    """Tonight's actual failure: a 1,602-line file replaced by 64 bytes."""
    repo = _fake_repo(tmp_path, STUB)
    assert _checkpoint_existing_suite_preserved(_ctx(repo)) is False


def test_suite_preserved_fails_when_one_single_test_is_dropped(tmp_path):
    kept = sorted(BASE_SUITE_TEST_NAMES)[1:]
    source = "".join(f"def {name}():\n    pass\n\n" for name in kept)
    repo = _fake_repo(tmp_path, source + "\n" + STUB)
    assert _checkpoint_existing_suite_preserved(_ctx(repo)) is False


def test_the_pinned_base_names_still_match_the_real_repo():
    """Drift guard. If the target repo's suite changes, this fails loudly
    rather than letting the check silently weaken."""
    if not TEMPLATE.exists():
        pytest.skip("target repo not present")
    source = subprocess.run(
        ["git", "-C", str(TEMPLATE), "show", f"main:{TEST_FILE}"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert _test_function_names(source) == set(BASE_SUITE_TEST_NAMES)


# --- the body range, and the bug it had -----------------------------


def test_the_def_line_alone_does_not_count(tmp_path):
    """THE bug. `def append_file` executes at import, so a range that
    includes it is satisfied by a test that never calls the function."""
    src = "def append_file(a, b):\n    body = 1\n    return body\n"
    repo = _fake_repo(tmp_path, STUB, source_source=src)
    start, end = _source_function_body_range(repo)
    assert start == 2, "the range must start at the first statement, not the def"
    assert end == 3


def test_a_docstring_is_skipped(tmp_path):
    src = 'def append_file(a, b):\n    """Doc."""\n    body = 1\n'
    repo = _fake_repo(tmp_path, STUB, source_source=src)
    start, _ = _source_function_body_range(repo)
    assert start == 3


def test_a_body_that_is_only_a_docstring_is_an_error(tmp_path):
    src = 'def append_file(a, b):\n    """Doc only."""\n'
    repo = _fake_repo(tmp_path, STUB, source_source=src)
    with pytest.raises(RuntimeError):
        _source_function_body_range(repo)


def test_a_missing_source_function_is_an_error(tmp_path):
    repo = _fake_repo(tmp_path, STUB, source_source="def something_else():\n    pass\n")
    with pytest.raises(RuntimeError):
        _source_function_body_range(repo)


# --- the end-to-end difference, on real clones -----------------------

REAL_TEST = '''

def test_append_file(tmp_path):
    import io, contextlib
    temp_file_path = tmp_path / 'strict_check.txt'
    create_file(str(temp_file_path))
    with io.StringIO() as buf:
        with contextlib.redirect_stdout(buf):
            append_file(str(temp_file_path), content='APPENDED TEXT')
    assert 'APPENDED TEXT' in temp_file_path.read_text()
'''


def _clone_and_score(task_id: str, mutate) -> int:
    d = Path(tempfile.mkdtemp(prefix="rubric-test-"))
    repo = d / "repo"
    subprocess.run(["git", "clone", "-q", str(TEMPLATE), str(repo)], check=True)
    mutate(repo)
    return score_task(TASKS[task_id], _ctx(repo)).points_earned


def _wipe(repo: Path) -> None:
    (repo / TEST_FILE).write_text(STUB)


def _append_stub(repo: Path) -> None:
    p = repo / TEST_FILE
    p.write_text(p.read_text() + "\n\n" + STUB)


def _append_real(repo: Path) -> None:
    p = repo / TEST_FILE
    p.write_text(p.read_text() + REAL_TEST)


requires_repo = pytest.mark.skipif(
    not (Path.home() / "Projects" / "openhands-target-append-file").exists(),
    reason="target repo not present",
)


@requires_repo
def test_upstream_cannot_separate_a_stub_from_a_real_test():
    """The finding. All three score identically under the published
    rubric, including the one that destroyed 54 other tests."""
    scores = {
        "wiped": _clone_and_score(UPSTREAM_ID, _wipe),
        "stub": _clone_and_score(UPSTREAM_ID, _append_stub),
        "real": _clone_and_score(UPSTREAM_ID, _append_real),
    }
    assert scores["wiped"] == scores["stub"] == scores["real"] == 4


@requires_repo
def test_the_corrected_rubric_separates_all_three():
    assert _clone_and_score(STRICT_ID, _wipe) == 2
    assert _clone_and_score(STRICT_ID, _append_stub) == 3
    assert _clone_and_score(STRICT_ID, _append_real) == 5


@requires_repo
def test_doing_nothing_scores_one_under_both():
    assert _clone_and_score(UPSTREAM_ID, lambda r: None) == 1
    assert _clone_and_score(STRICT_ID, lambda r: None) == 1
