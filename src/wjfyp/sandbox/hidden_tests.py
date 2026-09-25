from __future__ import annotations

from pydantic import BaseModel


class HiddenTestSpec(BaseModel):
    """SWE-bench-style hidden test definition for one ticket's fix (see
    cs3ip-evaluation-detail memory): FAIL_TO_PASS tests must flip from
    failing to passing, PASS_TO_PASS tests must stay passing throughout.

    `test_files` holds the actual pytest source to inject - a ticket's
    hidden tests are never present in the sandbox during in_progress,
    only written in at the deterministic awaiting_test step, after the
    engineering agent's turn for that attempt has already ended. This is
    what resolves the "hidden-vs-visible test isolation" gap left open
    at Layer 4 (see cs3ip-sandbox-design memory) - the agent's own
    run_tests() tool call only ever sees whatever visible tests already
    live in the repo, never these.
    """

    test_files: dict[str, str]  # path relative to repo root -> source
    fail_to_pass: list[str]  # pytest node ids
    pass_to_pass: list[str]  # pytest node ids
