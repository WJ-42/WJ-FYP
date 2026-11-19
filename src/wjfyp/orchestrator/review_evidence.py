"""What the reviewer is shown of the code it is asked to approve.

Added 19/11/2026 (option A, "make review real"). Until then the CTO's
Review turn received the ticket text and the engineer's handoff and
nothing else, so it judged a narrative it had no way to check. On the
migrate run1 batch it approved false claims, and once an empty diff that
only _apply_empty_diff_guard in loop.py stopped. This module builds the
evidence block that loop.step() puts on AgentContext.review_evidence at
REVIEW, which claude_agent._user_turn appends to the reviewer's turn.
Both backends build their user turn from that one function, so both see
the same evidence byte for byte.

The block has two parts.

1. `git diff --stat` of the ticket branch against its base. Every
   changed file appears here, including the ones left out of part 2.
2. The unified diff against the same base, cut to DIFF_BUDGET_CHARS.
   Lock files and generated files are never included, and neither is a
   single file whose diff alone would exceed the budget. Each omitted
   file is named with the reason. When the cut happens the text says so
   plainly, both where it happens and in the closing summary, so the
   reviewer cannot mistake a partial diff for the whole change.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Protocol

# Diff budget, sized for qwen3:8b on the local Ollama server. The server
# window is 16,384 tokens, and the In Progress transcript is trimmed to
# openai_agent.CONTEXT_BUDGET_TOKENS (11,000) to leave room for a
# 4,096 token completion. The Review turn is a single request with no
# trim of its own, so it is held under that same 11,000 here. The rest
# of the request measured about 2,000 to 3,000 tokens on the migrate
# task (system prompt, declare_outcome schema, ticket description, up to
# eight criteria, the handoff and a stat of a few dozen lines). 16,000
# characters is about 4,600 tokens at the 3.5 characters per token that
# openai_agent seeds its estimate with, and diffs tokenise worse than
# prose, so the real figure may run nearer 5,500. That still lands the
# whole request around 8,500 tokens, under 11,000 with room to spare for
# a long handoff. Override with WJFYP_REVIEW_DIFF_BUDGET_CHARS if the
# server window changes.
DIFF_BUDGET_CHARS = int(os.environ.get("WJFYP_REVIEW_DIFF_BUDGET_CHARS", "16000"))

# A stat line per file. Past this many files the rest are summarised in
# one line, so a sweeping change cannot push the diff itself out.
MAX_STAT_LINES = 60

# Below this many characters left, a partial file is worth less than the
# note saying it was cut, so the cut happens at the file boundary.
_MIN_PARTIAL_CHARS = 400

# Exact file names that are lock files. Their diffs are machine written,
# often hundreds of kilobytes, and say nothing a reviewer can check.
LOCK_FILE_NAMES = frozenset(
    {
        "uv.lock",
        "poetry.lock",
        "Pipfile.lock",
        "pdm.lock",
        "package-lock.json",
        "npm-shrinkwrap.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "bun.lockb",
        "Cargo.lock",
        "Gemfile.lock",
        "composer.lock",
        "go.sum",
        "flake.lock",
    }
)

# Name endings that mark a generated or minified file.
GENERATED_SUFFIXES = (".min.js", ".min.css", ".map", ".pyc")


class _DiffSource(Protocol):
    def diff_against_base(self, branch_name: str) -> str: ...

    def diff_stat_against_base(self, branch_name: str) -> str: ...


@dataclass(frozen=True)
class FileDiff:
    path: str
    text: str


def split_diff(diff: str) -> list[FileDiff]:
    """Splits a unified diff into one chunk per file, keyed by the file's
    path on the new side (the old side for a deletion).
    """
    chunks: list[list[str]] = []
    for line in diff.splitlines(keepends=True):
        if line.startswith("diff --git ") or not chunks:
            chunks.append([])
        chunks[-1].append(line)
    return [FileDiff(path=_path_of(chunk[0]), text="".join(chunk)) for chunk in chunks if chunk]


def _path_of(header: str) -> str:
    if not header.startswith("diff --git "):
        return "(unknown)"
    parts = header.rstrip("\n").split(" b/", 1)
    if len(parts) == 2:
        return parts[1]
    return header.rstrip("\n")[len("diff --git ") :]


def omission_reason(path: str) -> str | None:
    """Why a file's diff is never shown in full, or None if it may be."""
    name = PurePosixPath(path).name
    if name in LOCK_FILE_NAMES or name.endswith(".lock"):
        return "lock file"
    if name.endswith(GENERATED_SUFFIXES):
        return "generated file"
    return None


def _cap_stat(stat: str) -> str:
    lines = stat.splitlines()
    # The last line of a --stat is the "N files changed" summary. Keep it.
    if len(lines) <= MAX_STAT_LINES + 1:
        return stat
    hidden = len(lines) - 1 - MAX_STAT_LINES
    return "\n".join([*lines[:MAX_STAT_LINES], f" ... and {hidden} more files", lines[-1]])


def render_review_evidence(stat: str, diff: str, budget_chars: int = DIFF_BUDGET_CHARS) -> str:
    """The evidence block as text. Pure, so it is tested without git."""
    if not diff.strip():
        return (
            "Code change under review:\n"
            "The ticket branch changes nothing against its base. There is no code "
            "to review, so no acceptance criterion can be met."
        )

    shown: list[str] = []
    omitted: list[str] = []
    not_reached: list[str] = []
    used = 0
    cut_at: str | None = None

    for file_diff in split_diff(diff):
        reason = omission_reason(file_diff.path)
        if reason is None and len(file_diff.text) > budget_chars:
            reason = f"diff too large to show, {len(file_diff.text):,} characters"
        if reason is not None:
            omitted.append(f"- {file_diff.path} ({reason})")
            continue
        if cut_at is not None:
            not_reached.append(file_diff.path)
            continue
        remaining = budget_chars - used
        if len(file_diff.text) <= remaining:
            shown.append(file_diff.text)
            used += len(file_diff.text)
            continue
        cut_at = file_diff.path
        if remaining >= _MIN_PARTIAL_CHARS:
            partial = file_diff.text[:remaining]
            partial = partial[: partial.rfind("\n") + 1] or partial
            shown.append(partial)
            shown.append(f"[DIFF TRUNCATED HERE, partway through {file_diff.path}]\n")
            used += len(partial)
        else:
            shown.append(f"[DIFF TRUNCATED HERE, before {file_diff.path}]\n")
            not_reached.append(file_diff.path)

    lines = [
        "Code change under review (the ticket branch against its base).",
        "Files changed, from git diff --stat. Every changed file is listed here, "
        "including any left out of the diff below.",
        _cap_stat(stat.rstrip("\n")),
    ]
    if omitted:
        lines.append("Left out of the diff below, so judge these from the stat only.")
        lines.extend(omitted)
    lines.append("Unified diff:")
    lines.append("".join(shown).rstrip("\n") if shown else "(no file small enough to show)")

    truncated = cut_at is not None
    if truncated:
        summary = (
            f"THE DIFF ABOVE WAS TRUNCATED to {budget_chars:,} characters. You have not "
            "seen all of the change."
        )
        if not_reached:
            summary += " Files changed but not shown at all, " + ", ".join(not_reached) + "."
        summary += (
            " Do not assume a criterion is met by code you have not seen. If the visible "
            "part does not show it, request changes and say what you could not verify."
        )
        lines.append(summary)
    else:
        lines.append("End of diff. This is the whole change apart from any files listed as left out.")
    return "\n".join(lines)


def build_review_evidence(workspace: _DiffSource, branch_name: str) -> str:
    """Reads the stat and diff for `branch_name` from the host workspace
    and renders them. The branch has already been pushed back by the
    engineer's git_commit before REVIEW, so this is the submitted work.
    """
    return render_review_evidence(
        workspace.diff_stat_against_base(branch_name),
        workspace.diff_against_base(branch_name),
    )
