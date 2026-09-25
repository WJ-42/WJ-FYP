from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel


class CommandResult(BaseModel):
    exit_code: int
    stdout: str
    stderr: str


class TestResult(BaseModel):
    """FAIL_TO_PASS / PASS_TO_PASS structure per SWE-bench's mechanism
    (Jimenez et al.) - see cs3ip-evaluation-detail memory. `passed` is
    the single verdict the orchestrator acts on: True only if every
    FAIL_TO_PASS test now passes AND every PASS_TO_PASS test still
    passes (no regression).
    """

    passed: bool
    fail_to_pass_total: int
    fail_to_pass_passed: int
    pass_to_pass_total: int
    pass_to_pass_passed: int
    output: str


@runtime_checkable
class SandboxController(Protocol):
    """The fixed tool interface agents use in place of raw shell access
    (see cs3ip-sandbox-design memory), and that the orchestrator also
    calls directly for deterministic test verification at the
    AWAITING_TEST state (confirmed 2026-09-25: test results are a
    system-verified fact, not something the engineering agent
    self-reports - see cs3ip-comm-protocol memory).

    One instance is bound to a single ticket-attempt: created fresh when
    a ticket (re-)enters IN_PROGRESS, used through that attempt's
    AWAITING_TEST check, then closed - win or lose - before the next
    attempt (if any) gets its own fresh instance.
    """

    def run_tests(self) -> TestResult: ...
    def run_command(self, cmd: str) -> CommandResult: ...
    def read_file(self, path: str) -> str: ...
    def write_file(self, path: str, content: str) -> None: ...
    def git_diff(self) -> str: ...
    def git_commit(self, message: str) -> str: ...  # returns the commit sha

    def close(self) -> None: ...
