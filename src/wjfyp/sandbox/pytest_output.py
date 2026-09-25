from __future__ import annotations

import re

# pytest -v prints one line per test like:
#   tests/test_foo.py::test_bar PASSED   [ 50%]
# Matching on the literal node id (rather than parsing JUnit XML's
# classname/name split) keeps this exact and dependency-free - the node
# id echoed back is character-for-character what we asked pytest to run.
_VERBOSE_RESULT_LINE = re.compile(r"^(?P<node_id>\S+::\S+)\s+(?P<outcome>PASSED|FAILED|ERROR|SKIPPED)\b")


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
        match = _VERBOSE_RESULT_LINE.match(line.strip())
        if match:
            outcomes[match.group("node_id")] = match.group("outcome") == "PASSED"
    return outcomes
