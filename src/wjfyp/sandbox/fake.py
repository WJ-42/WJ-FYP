from __future__ import annotations

from wjfyp.sandbox.controller import CommandResult, TestResult
from wjfyp.sandbox.hidden_tests import HiddenTestSpec


class FakeSandboxController:
    """In-memory SandboxController for tests - no Docker, no git. Files
    live in a dict; run_tests()/run_hidden_tests() return pre-scripted
    results in order, the same pattern tests/test_orchestrator_loop.py's
    ScriptedAgent uses for agent responses, so control-flow tests don't
    depend on a real container or real pytest output.
    """

    def __init__(
        self,
        test_results: list[TestResult] | None = None,
        hidden_test_results: list[TestResult] | None = None,
        command_results: list[CommandResult] | None = None,
    ):
        self.files: dict[str, str] = {}
        self.commands: list[str] = []
        self.commits: list[str] = []
        self.closed = False
        self._test_results = list(test_results or [])
        self._hidden_test_results = list(hidden_test_results or [])
        # Unlike run_tests()/run_hidden_tests(), run_command() falls back
        # to a silent success once scripted results are exhausted (or if
        # none were given at all) rather than raising - most run_command
        # calls in a real session succeed and aren't worth scripting
        # individually, only specific ones (e.g. a network failure) are.
        self._command_results = list(command_results or [])
        self.hidden_test_specs_seen: list[HiddenTestSpec] = []

    def run_tests(self) -> TestResult:
        if not self._test_results:
            raise AssertionError("FakeSandboxController.run_tests() called with no scripted result left")
        return self._test_results.pop(0)

    def run_hidden_tests(self, spec: HiddenTestSpec) -> TestResult:
        self.hidden_test_specs_seen.append(spec)
        self.files.update(spec.test_files)
        if not self._hidden_test_results:
            raise AssertionError(
                "FakeSandboxController.run_hidden_tests() called with no scripted result left"
            )
        return self._hidden_test_results.pop(0)

    def run_command(self, cmd: str) -> CommandResult:
        self.commands.append(cmd)
        if self._command_results:
            return self._command_results.pop(0)
        return CommandResult(exit_code=0, stdout="", stderr="")

    def read_file(self, path: str) -> str:
        return self.files[path]

    def write_file(self, path: str, content: str) -> None:
        self.files[path] = content

    def git_diff(self) -> str:
        return "\n".join(f"+{path}" for path in self.files)

    def git_commit(self, message: str) -> str:
        sha = f"fake-sha-{len(self.commits) + 1}"
        self.commits.append(sha)
        return sha

    def close(self) -> None:
        self.closed = True
