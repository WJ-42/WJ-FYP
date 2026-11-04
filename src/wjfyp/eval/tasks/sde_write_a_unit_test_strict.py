"""A corrected rubric for sde-write-a-unit-test-for-append_file-function.

The ported original (sde_write_a_unit_test.py, transcribed verbatim from
TheAgentCompany's evaluator.py on 2026-09-25) cannot tell a real test
from an empty one. Measured on 2026-11-04 across a batch of runs:

    def test_append_file():
        # Implement test logic here
        pass

scored **4 of 5** under the upstream rubric. The arithmetic is not a
bug in the port, it is what the upstream checkpoint weights do:

    source-file-exists            1   passes, file_ops.py ships with the repo
    test-function-exists          1   passes, the AST sees the def
    test-passes                   2   passes, an empty body never raises
    coverage-drops-without-test   1   fails, it exercises nothing

Two consequences, both observed in real runs rather than reasoned about:

1. **80% of the marks are free.** Only the coverage checkpoint asks
   whether the test tests anything, and across an entire batch no run
   ever earned it. The score therefore ranked a stub above a genuine
   test that happened not to be merged.
2. **Deleting the test suite is not penalised.** The run above replaced
   a 1,602-line file holding 54 other tests with 64 bytes, and still
   scored 4/5. Nothing in the upstream rubric looks at what was
   destroyed, only at what was added.

This module does NOT modify the port. The upstream task stays
registered, byte-for-byte faithful, so results under the published
rubric remain reproducible and the two can be reported side by side.
This is a second task id over the same repo, prompt and target, with
the checkpoint weights corrected and one checkpoint added.

What changed, and why:

* **source-file-exists is dropped.** It measures the repository, not
  the agent. file_ops.py is present whatever the agent does, so the
  point was unearnable only by deleting the file under test.
* **existing-suite-preserved is new**, worth 1. The 54 test functions
  present on the base branch must still be defined. This is a set
  comparison on function names rather than a pytest run, deliberately:
  8 of the 54 already fail in the grader environment for missing
  optional dependencies (pdf parsing, linters), measured 2026-11-04, so
  requiring a green suite would fail every agent for reasons it did not
  cause.
* **test-passes drops from 2 to 1.** Passing is necessary and almost
  free; it should not outweigh exercising the code.
* **coverage-drops-without-test rises from 1 to 2.** It is the only
  checkpoint that distinguishes work from theatre, so it carries the
  most weight.

Totals stay at 5 so scores are directly comparable. The resulting
spread, against the same artefacts that produced the 4/5 above:

    nothing merged, pristine main        1/5
    stub that wiped the suite            2/5
    stub that left the suite alone       3/5
    a real test                          5/5

ORDERING: the upstream coverage checkpoint is destructive, it deletes
test_append_file from the file and leaves it that way. existing-suite-
preserved is listed first so it can never observe a mutated file. It
would survive the mutation anyway (test_append_file is not one of the
54 base names, the base has test_append_file_from_scratch and friends
but no exact test_append_file) but relying on that would be fragile.
"""

from __future__ import annotations

import ast
import xml.etree.ElementTree as ET
from pathlib import Path

from wjfyp.eval.task import Checkpoint, EvalTask, GradingContext
from wjfyp.eval.tasks import register
from wjfyp.eval.tasks.sde_write_a_unit_test import (
    COVERAGE_XML,
    FUNCTION_NAME,
    SOURCE_FILE,
    TEST_FILE,
    _checkpoint_test_function_exists,
    _checkpoint_test_passes,
    _run_pytest_with_stats,
)

# The test functions defined in tests/unit/test_agent_skill.py on the
# target repo's base branch, extracted 2026-11-04.
#
# Pinned here rather than read from git at grading time on purpose. By
# the time a grader runs, an approved ticket has already been MERGED
# into main (Review -> Done "approved" is the merge, see
# orchestrator/loop.py), so `git show main:TEST_FILE` would return the
# agent's own work and the comparison would be against itself. A fixed
# list is immune to the repo's state. tests/test_sde_write_a_unit_test_strict.py
# pins it against the real repo so drift is caught rather than silently
# weakening the check.
BASE_SUITE_TEST_NAMES: frozenset[str] = frozenset(
    {
        "test_append_file_from_scratch",
        "test_append_file_from_scratch_multiline",
        "test_append_file_not_opened",
        "test_create_file",
        "test_create_file_unexist_path",
        "test_edit_file_by_replace",
        "test_edit_file_by_replace_multiline",
        "test_edit_file_by_replace_no_diff",
        "test_edit_file_by_replace_sameline",
        "test_edit_file_by_replace_toreplace_empty",
        "test_edit_file_by_replace_unknown_file",
        "test_edit_file_by_replace_window",
        "test_edit_file_by_replace_with_multiple_errors",
        "test_edit_lint_file_pass",
        "test_find_file",
        "test_find_file_cwd",
        "test_find_file_not_exist_file",
        "test_find_file_not_exist_file_specific_path",
        "test_goto_line",
        "test_goto_line_negative",
        "test_goto_line_out_of_bound",
        "test_insert_content_at_line",
        "test_insert_content_at_line_emptyline",
        "test_insert_content_at_line_from_scratch",
        "test_insert_content_at_line_from_scratch_emptyfile",
        "test_insert_content_at_line_from_scratch_multiline",
        "test_insert_content_at_line_from_scratch_multiline_with_backticks_and_second_edit",
        "test_insert_content_at_line_not_opened",
        "test_lint_file_disabled_undefined_name",
        "test_lint_file_fail_non_python",
        "test_lint_file_fail_typescript",
        "test_lint_file_fail_undefined_name",
        "test_lint_file_fail_undefined_name_long",
        "test_open_file",
        "test_open_file_large_line_number",
        "test_open_file_long",
        "test_open_file_long_with_lineno",
        "test_open_file_unexist_path",
        "test_open_file_with_indentation",
        "test_parse_docx",
        "test_parse_latex",
        "test_parse_pdf",
        "test_parse_pptx",
        "test_print_window_internal",
        "test_scroll_down",
        "test_scroll_down_edge",
        "test_scroll_up",
        "test_search_dir",
        "test_search_dir_cwd",
        "test_search_dir_not_exist_term",
        "test_search_dir_too_much_match",
        "test_search_file",
        "test_search_file_not_exist_file",
        "test_search_file_not_exist_term",
    }
)


def _test_function_names(source: str) -> set[str]:
    return {
        node.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    }


def _checkpoint_existing_suite_preserved(ctx: GradingContext) -> bool:
    """Every test that existed on the base branch is still defined.

    Catches the failure the upstream rubric is blind to: an agent that
    overwrites the whole test file with its own small answer, destroying
    the surrounding suite, and is rewarded for it.

    Names, not pytest results, for the reason in the module docstring.
    A test can also be broken without being deleted and this will not
    catch that; the deletion case is the one observed in real runs, and
    a name check cannot be confounded by a missing optional dependency.
    """
    source = (Path(ctx.repo_path) / TEST_FILE).read_text()
    return BASE_SUITE_TEST_NAMES <= _test_function_names(source)



# The function under test, located by AST rather than by a line number
# so it survives the source file being edited.
SOURCE_FUNCTION_NAME = "append_file"


def _source_function_body_range(repo_path: str) -> tuple[int, int]:
    """First executable statement of append_file's body, to its end.

    NOT node.lineno. A FunctionDef's lineno is the `def` line, and that
    line runs when the MODULE is imported, not when the function is
    called - so a range starting there is hit by any test at all,
    including one that does nothing. Caught by the validation harness:
    an empty stub scored 5/5 because importing file_ops.py marked the
    `def append_file` line as covered.

    A leading docstring is skipped as well. It is a constant expression
    that coverage does not record as a statement, so including it would
    make the range start on a line that can never be hit.
    """
    source = (Path(repo_path) / SOURCE_FILE).read_text()
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.FunctionDef) and node.name == SOURCE_FUNCTION_NAME):
            continue
        body = node.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            body = body[1:]
        if not body:
            raise RuntimeError(f"{SOURCE_FUNCTION_NAME} has no executable body")
        return body[0].lineno, (node.end_lineno or body[-1].lineno)
    raise RuntimeError(f"{SOURCE_FUNCTION_NAME} not found in {SOURCE_FILE}")


def _covered_lines(repo_path: str) -> set[int]:
    tree = ET.parse(Path(repo_path) / COVERAGE_XML)
    for class_elem in tree.getroot().findall(".//class"):
        if class_elem.get("name") != "file_ops.py":
            continue
        return {
            int(line.get("number", "0"))
            for line in class_elem.findall(".//line")
            if int(line.get("hits", "0")) > 0
        }
    raise RuntimeError("file_ops.py absent from the coverage report")


def _checkpoint_test_exercises_the_function(ctx: GradingContext) -> bool:
    """The agent's test, run ALONE, must execute at least one line inside
    append_file's own body.

    This replaces the upstream coverage checkpoint, which cannot pass.
    Measured 2026-11-04: the upstream version runs the WHOLE test file,
    reads file_ops.py's line coverage, deletes the agent's test, runs the
    whole file again and requires coverage to have dropped. With the 54
    other tests present, coverage is 0.7258 before and 0.7258 after -
    byte identical - because those tests already cover everything the
    agent's test touches. Removing one test out of 55 cannot move a
    whole-file figure, so the checkpoint is unearnable by construction,
    and across an entire batch of runs no agent ever earned it. The only
    way to make that coverage drop is to have deleted the rest of the
    suite, which the upstream rubric separately fails to penalise.

    Running the agent's test in isolation measures the thing the
    checkpoint was reaching for. Measured on the same two artefacts: a
    real test covers 0.357 of file_ops.py alone, the stub covers 0.01655,
    the latter being nothing but the module's own import-time lines.

    No coverage threshold is used, deliberately. A ratio would be a
    magic number that drifts with the source file's length. Asking
    whether any line INSIDE append_file was executed is the actual
    question, and it is exact: a test that never calls the function
    cannot hit one, and a test that calls it must.
    """
    stats = _run_pytest_with_stats(ctx.repo_path, function_name=FUNCTION_NAME)
    if stats["passed"] != 1:
        return False
    start, end = _source_function_body_range(ctx.repo_path)
    return any(start <= line <= end for line in _covered_lines(ctx.repo_path))


TASK = register(
    EvalTask(
        task_id="sde-write-a-unit-test-for-append_file-function-strict",
        title="Write a unit test for the append_file function (corrected rubric)",
        prompt=(
            f"Find the function append_file in {SOURCE_FILE}. Write a unit test "
            f"named '{FUNCTION_NAME}' for it in {TEST_FILE}, in the same repo, "
            "using the existing Poetry-managed test setup."
        ),
        checkpoints=[
            Checkpoint(
                id="existing-suite-preserved",
                description="the test functions already on the base branch are still defined",
                points=1,
                grader=_checkpoint_existing_suite_preserved,
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
                points=1,
                grader=_checkpoint_test_passes,
            ),
            Checkpoint(
                id="test-exercises-the-function",
                description="the test, run alone, executes at least one line inside append_file",
                points=2,
                grader=_checkpoint_test_exercises_the_function,
            ),
        ],
        source="TheAgentCompany:sde-write-a-unit-test-for-append_file-function (reweighted)",
    )
)
