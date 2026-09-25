from __future__ import annotations

import re

# pytest -v prints one line per test like:
#   tests/test_foo.py::test_bar PASSED   [ 50%]
# Matching on the literal node id (rather than parsing JUnit XML's
# classname/name split) keeps this exact and dependency-free - the node
# id echoed back is character-for-character what we asked pytest to run.
_VERBOSE_RESULT_LINE = re.compile(r"^(?P<node_id>\S+::\S+)\s+(?P<outcome>PASSED|FAILED|ERROR|SKIPPED)\b")

# pytest emits ANSI colour codes around the outcome word even when stdout
# is piped, not just in an interactive terminal - confirmed empirically
# (pytest 9.1.1 still wrote "\x1b[32mPASSED\x1b[0m" under
# subprocess.run(capture_output=True)), so this can't be assumed away.
# The caller also passes --color=no, but stripping here too is cheap
# insurance against a base image/plugin/env var forcing colour back on -
# an assumption that has already broken once for this project (see
# feedback-pdf-generation memory on the Chromium header/footer flag).
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from pytest output - exported since more
    than one caller needs this (see wjfyp.eval.tasks.sde_write_a_unit_test
    for the other), not just parse_pytest_verbose_output below.
    """
    return _ANSI_ESCAPE.sub("", text)


def parse_pytest_verbose_output(output: str) -> dict[str, bool]:
    """Map each pytest node id mentioned in `-v` output to whether it
    passed. FAILED/ERROR/SKIPPED all count as not-passed - a skipped
    hidden test hasn't verified anything either. A node id absent from
    the output (e.g. a collection error) is simply absent from the
    returned dict, not assumed True or False - callers should treat a
    missing id as not-passed.
    """
    outcomes: dict[str, bool] = {}
    for line in output.splitlines():
        line = strip_ansi(line).strip()
        match = _VERBOSE_RESULT_LINE.match(line)
        if match:
            outcomes[match.group("node_id")] = match.group("outcome") == "PASSED"
    return outcomes
