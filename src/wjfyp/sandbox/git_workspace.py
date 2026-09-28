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

    def diff_against_base(self, branch_name: str, base: str | None = None) -> str:
        """The diff `branch_name` introduced relative to `base` - usable
        whether or not the branch has since been merged.

        A plain `git diff base branch` (or even a merge-base diff) reads
        empty once merge() has run: after a merge, `branch` is an
        ancestor of `base`, and merge-base(base, branch) then collapses
        to branch's own tip - `git diff <tip> <tip>` is trivially empty,
        even though the branch genuinely changed something. So this
        first looks for the actual merge commit merge() would have
        created (a commit reachable from `base` whose second parent is
        branch's tip - found structurally, not by matching merge()'s
        commit-message text) and diffs its first parent against it,
        which is exactly what that merge introduced. Falls back to a
        plain merge-base diff for the pre-merge case, where this works
        correctly since branch hasn't been absorbed into base yet.

        Returns "" if the branch was never created (e.g. a ticket that
        never reached in_progress).
        """
        if not self._git("branch", "--list", branch_name):
            return ""
        base = base or self.default_base
        branch_tip = self._git("rev-parse", branch_name)
        merge_commits = self._git("log", base, "--merges", "--format=%H %P").splitlines()
        merge_commit = next(
            (line.split()[0] for line in merge_commits if line.split()[-1] == branch_tip), None
        )
        if merge_commit:
            first_parent = self._git("rev-parse", f"{merge_commit}^1")
            return self._git("diff", first_parent, merge_commit)
        merge_base = self._git("merge-base", base, branch_name)
        return self._git("diff", merge_base, branch_name)

    def clone_url(self) -> str:
        """A file:// URL usable as a git remote from inside a container
        that has this path bind-mounted read-write.
        """
        return f"file://{self.repo_path}"

    def merge(self, ticket: Ticket, base: str | None = None) -> str:
        """Merge ticket.branch_name into `base` on this host workspace and
        return the resulting commit sha. This is what actually makes the
        Review -> Done "approval IS the merge" mechanic real (see
        fsm.py's TRANSITIONS comment and cs3ip-comm-protocol memory) -
        found missing entirely 2026-09-28 running a real ticket through
        intervention mode end to end: approving a ticket only ever
        flipped Ticket.status, no git operation ran at all, so a
        genuinely approved, tested, committed change never actually
        reached the branch anyone else would look at.

        Checks out `base` first (default_base if not given) rather than
        merging from wherever the workspace happens to be checked out,
        so the workspace ends this call on `base` - the same invariant
        git_commit()'s push-back already depends on (see
        DockerAttemptSandbox.git_commit's comment: pushes are rejected
        by git's denyCurrentBranch default if the target branch is the
        one currently checked out on the host, so the host must stay off
        ticket branches). Raises (via subprocess's check=True) on a real
        merge conflict rather than silently leaving a half-merged state -
        this project doesn't have a conflict-resolution story yet, so
        surfacing the failure is the honest behavior until it does.
        """
        if not ticket.branch_name:
            raise ValueError(f"ticket {ticket.id} has no branch_name assigned")
        base = base or self.default_base
        self._git("checkout", base)
        self._git(
            "merge", "--no-ff", ticket.branch_name, "-m", f"Merge {ticket.branch_name}: {ticket.title}"
        )
        return self._git("rev-parse", "HEAD")
