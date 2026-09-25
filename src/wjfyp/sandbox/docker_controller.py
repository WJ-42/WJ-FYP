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

# Placeholder, same status as config/roles.yaml's TBD-* model fields -
# see docker/sandbox.Dockerfile for what it needs to contain and why.
DEFAULT_IMAGE = "wjfyp-sandbox:latest"


class DockerAttemptSandbox:
    """Docker-backed SandboxController for a single ticket-attempt.

    Per cs3ip-sandbox-design memory: non-root user, no network egress,
    CPU/memory/pids limits, fresh checkout per attempt (git-cloned from
    the host GitWorkspace via a read-write bind mount, not the working
    directory itself mounted directly - a bad generation can't corrupt
    the canonical workspace). Torn down via close() regardless of
    outcome; the caller (orchestrator loop) owns that lifecycle.

    Known limitation, not yet resolved: the bind-mounted host repo's
    file ownership (host UID) and the container's `sandbox` user (image
    UID) aren't reconciled here, which can produce permission errors on
    the bind mount depending on the host filesystem - needs revisiting
    once this actually runs against a real Docker daemon.
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
        self._exec("git clone /host-repo /workspace")
        self._exec(f"git -C /workspace checkout {shlex.quote(self._branch)}")

    def _exec(self, cmd: str) -> CommandResult:
        exit_code, output = self._container.exec_run(["sh", "-c", cmd], workdir="/workspace")
        stdout = output.decode() if isinstance(output, bytes) else ""
        return CommandResult(exit_code=exit_code, stdout=stdout, stderr="")

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
        self._exec("git add -A")
        self._exec(f"git commit -m {shlex.quote(message)}")
        self._exec(f"git push origin HEAD:{self._branch}")
        return self._exec("git rev-parse HEAD").stdout.strip()

    def close(self) -> None:
        self._container.remove(force=True)
