from __future__ import annotations

import io
import shlex
import tarfile
from pathlib import Path

import docker

from wjfyp.models.ticket import Ticket
from wjfyp.sandbox.controller import CommandResult, TestResult
from wjfyp.sandbox.git_workspace import GitWorkspace
from wjfyp.sandbox.hidden_tests import HiddenTestSpec
from wjfyp.sandbox.pytest_output import parse_pytest_verbose_output

# Placeholder pending the real target stack - see docker/sandbox.Dockerfile
# for what it needs to contain and why.
DEFAULT_IMAGE = "wjfyp-sandbox:latest"


class DockerAttemptSandbox:
    """Docker-backed SandboxController for a single ticket-attempt.

    Per cs3ip-sandbox-design memory: non-root user, no network egress,
    CPU/memory/pids limits, fresh checkout per attempt (git-cloned from
    the host GitWorkspace via a read-write bind mount, not the working
    directory itself mounted directly - a bad generation can't corrupt
    the canonical workspace). Torn down via close() regardless of
    outcome; the caller (orchestrator loop) owns that lifecycle.

    Known limitation, still not actually resolved: the bind-mounted host
    repo's file ownership (host UID) and the container's `sandbox` user
    (image UID) aren't reconciled here. Confirmed 2026-09-25 (first real
    Docker run, once FYP-22 fixed daemon access) that git_commit()'s
    push-back to the host workspace works end to end - but only because
    the dev host's user and the image's `sandbox` user both happen to be
    uid 1000 (each being the first non-system user on their respective
    systems), not because this gap was actually closed. A host whose
    user has a different uid would still hit permission errors on the
    bind mount. Revisit if that's ever observed, or before relying on
    this against an unknown host.
    """

    def __init__(
        self,
        ticket: Ticket,
        workspace: GitWorkspace,
        image: str = DEFAULT_IMAGE,
        client: "docker.DockerClient | None" = None,
    ):
        self.ticket = ticket
        self.workspace = workspace
        self._client = client or docker.from_env()
        self._branch = workspace.ensure_branch(ticket)
        self._container = self._client.containers.run(
            image,
            detach=True,
            user="sandbox",
            network_disabled=True,
            mem_limit="512m",
            pids_limit=256,
            nano_cpus=1_000_000_000,  # 1 CPU
            volumes={str(workspace.repo_path): {"bind": "/host-repo", "mode": "rw"}},
        )
        self._exec_checked("git clone /host-repo /workspace", "clone the host workspace")
        self._exec_checked(
            f"git -C /workspace checkout {shlex.quote(self._branch)}", f"check out {self._branch!r}"
        )

    def _exec(self, cmd: str) -> CommandResult:
        exit_code, output = self._container.exec_run(["sh", "-c", cmd], workdir="/workspace")
        stdout = output.decode() if isinstance(output, bytes) else ""
        return CommandResult(exit_code=exit_code, stdout=stdout, stderr="")

    def _exec_checked(self, cmd: str, doing: str) -> CommandResult:
        """Like _exec(), but raises on a non-zero exit code instead of
        returning it for the caller to notice or not.

        Only used for internal orchestration mechanics (clone, checkout,
        the git add/commit/push sequence in git_commit()) where a
        failure must never be silently treated as success - unlike
        run_command()/run_tests(), which are deliberately agent-facing
        tools that return a structured result (CommandResult.exit_code,
        TestResult.passed) for the caller to interpret, since a non-zero
        exit there can be a legitimate, expected outcome (a linter
        finding issues, a failing test). Added after confirming by
        direct reproduction (2026-09-25, the first session with working
        Docker access) that git_commit() calling plain _exec()
        throughout meant a `git commit` failure (e.g. no git identity
        configured - see docker/sandbox.Dockerfile) was completely
        invisible: no exception, no error, just a stale HEAD sha
        returned as if the commit had succeeded.
        """
        result = self._exec(cmd)
        if result.exit_code != 0:
            raise RuntimeError(f"failed to {doing} (exit {result.exit_code}): {result.stdout}")
        return result

    def run_command(self, cmd: str) -> CommandResult:
        return self._exec(cmd)

    def run_tests(self) -> TestResult:
        # Agent-visible check only - runs whatever tests already live in
        # the repo, no FAIL_TO_PASS/PASS_TO_PASS breakdown. The
        # authoritative hidden-test verification the orchestrator relies
        # on at awaiting_test is run_hidden_tests(), never this method
        # (see HiddenTestSpec's docstring for why that separation matters).
        result = self._exec("python -m pytest -q")
        return TestResult(
            passed=result.exit_code == 0,
            fail_to_pass_total=0,
            fail_to_pass_passed=0,
            pass_to_pass_total=0,
            pass_to_pass_passed=0,
            output=result.stdout,
        )

    def run_hidden_tests(self, spec: HiddenTestSpec) -> TestResult:
        for path, content in spec.test_files.items():
            self.write_file(path, content)

        node_ids = spec.fail_to_pass + spec.pass_to_pass
        if not node_ids:
            # Nothing to verify - an empty spec is vacuously satisfied,
            # but running pytest with no node ids would fall back to
            # full test discovery over the whole repo, which is not
            # "hidden tests passed", just a full suite run under a
            # misleading label.
            return TestResult(
                passed=True,
                fail_to_pass_total=0,
                fail_to_pass_passed=0,
                pass_to_pass_total=0,
                pass_to_pass_passed=0,
                output="",
            )

        # --color=no: pytest emits ANSI colour codes around the outcome
        # word even when stdout is piped, not just in a real terminal -
        # confirmed empirically, so this can't be left to auto-detection
        # (parse_pytest_verbose_output also strips any that slip through
        # as belt-and-suspenders).
        cmd = "python -m pytest -v --no-header --color=no -p no:cacheprovider " + " ".join(
            shlex.quote(node_id) for node_id in node_ids
        )
        result = self._exec(cmd)
        outcomes = parse_pytest_verbose_output(result.stdout)

        fail_to_pass_passed = sum(1 for node_id in spec.fail_to_pass if outcomes.get(node_id))
        pass_to_pass_passed = sum(1 for node_id in spec.pass_to_pass if outcomes.get(node_id))
        passed = fail_to_pass_passed == len(spec.fail_to_pass) and pass_to_pass_passed == len(
            spec.pass_to_pass
        )
        return TestResult(
            passed=passed,
            fail_to_pass_total=len(spec.fail_to_pass),
            fail_to_pass_passed=fail_to_pass_passed,
            pass_to_pass_total=len(spec.pass_to_pass),
            pass_to_pass_passed=pass_to_pass_passed,
            output=result.stdout,
        )

    def read_file(self, path: str) -> str:
        result = self._exec(f"cat {shlex.quote(path)}")
        if result.exit_code != 0:
            raise FileNotFoundError(path)
        return result.stdout

    def write_file(self, path: str, content: str) -> None:
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w") as tar:
            data = content.encode()
            info = tarfile.TarInfo(name=Path(path).name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        archive.seek(0)
        dest_dir = str(Path("/workspace") / Path(path).parent)
        self._exec(f"mkdir -p {shlex.quote(dest_dir)}")
        self._container.put_archive(dest_dir, archive.getvalue())

    def git_diff(self) -> str:
        return self._exec("git diff HEAD").stdout

    def git_commit(self, message: str) -> str:
        # Pushes to whatever branch is currently checked out on the host
        # workspace are rejected by git's denyCurrentBranch default -
        # fine as long as the host stays on main/master while tickets
        # use their own branch, per the branch-per-ticket design.
        #
        # Every step here is _exec_checked, not plain _exec: this is the
        # orchestrator's authoritative "the code is committed" signal
        # (its return value becomes the commit sha other machinery
        # trusts), so a failure at any step must raise rather than
        # silently return a stale sha that looks like success - see
        # _exec_checked's own docstring for the real incident this fixes.
        self._exec_checked("git add -A", "stage changes")
        self._exec_checked(f"git commit -m {shlex.quote(message)}", "commit")
        self._exec_checked(f"git push origin HEAD:{self._branch}", f"push to {self._branch!r}")
        return self._exec_checked("git rev-parse HEAD", "resolve the new commit sha").stdout.strip()

    def close(self) -> None:
        self._container.remove(force=True)
