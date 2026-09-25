from __future__ import annotations

from wjfyp.sandbox.controller import CommandResult, TestResult


class FakeSandboxController:
    """In-memory SandboxController for tests - no Docker, no git. Files
    live in a dict; run_tests() returns pre-scripted results in order,
    the same pattern tests/test_orchestrator_loop.py's ScriptedAgent
    uses for agent responses, so control-flow tests don't depend on a
    real container.
    """

    def __init__(self, test_results: list[TestResult] | None = None):
        self.files: dict[str, str] = {}
        self.commands: list[str] = []
        self.commits: list[str] = []
        self.closed = False
        self._test_results = list(test_results or [])

    def run_tests(self) -> TestResult:
        if not self._test_results:
            raise AssertionError("FakeSandboxController.run_tests() called with no scripted result left")
        return self._test_results.pop(0)

    def run_command(self, cmd: str) -> CommandResult:
        self.commands.append(cmd)
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
