from __future__ import annotations

import subprocess
from pathlib import Path

from wjfyp.models.ticket import Ticket


class GitWorkspace:
    """The persistent, host-side git workspace beneath the ephemeral
    per-attempt sandbox containers (confirmed 2026-09-23, see
    cs3ip-comm-protocol memory's "Git mechanics" entry). Holds the
    canonical repo and branch-per-ticket state that survives across
    attempts; each DockerAttemptSandbox clones from here fresh rather
    than mounting it directly, so a bad attempt can't corrupt it.

    Scope note: this operates on a local repo path already present on
    disk. Wiring it up to a real "attach to existing repo" (clone from
    a user-supplied remote) vs. "create new repo" flow is deferred -
    see the Layer 4 diary entry.
    """

    def __init__(self, repo_path: Path, default_base: str = "main"):
        self.repo_path = Path(repo_path)
        self.default_base = default_base

    def _git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(self.repo_path), *args],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()

    def ensure_branch(self, ticket: Ticket, base: str | None = None) -> str:
        """Create ticket.branch_name off `base` if it doesn't already
        exist. Returns the branch name. Raises if the ticket has no
        branch_name set - assigning one is the caller's job (e.g. when a
        ticket first enters in_progress).
        """
        if not ticket.branch_name:
            raise ValueError(f"ticket {ticket.id} has no branch_name assigned")
        base = base or self.default_base
        existing = self._git("branch", "--list", ticket.branch_name)
        if not existing:
            self._git("branch", ticket.branch_name, base)
        return ticket.branch_name

    def clone_url(self) -> str:
        """A file:// URL usable as a git remote from inside a container
        that has this path bind-mounted read-write.
        """
        return f"file://{self.repo_path}"
