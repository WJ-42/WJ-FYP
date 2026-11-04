"""Turn 22 comparisons into something that can be reported.

Reads the batch summary for scores and the kept artifacts for WHY each
arm scored what it did. Deliberately reports the sign test rather than
a mean difference: the outcome per run is which arm did better, which
is ordinal, and averaging 2/5 against 1/5 across runs would invent a
precision the scoring does not have.

Ties are not dropped silently either. A tie is evidence AGAINST a
structure effect, and the convention of discarding them inflates the
apparent split, so they are counted and reported separately.

    .venv/bin/python analyse_batch.py
"""

from __future__ import annotations

import ast
import re
import sqlite3
from collections import Counter
from math import comb
from pathlib import Path

SCRATCH = Path("/tmp/claude-1000/-home-waleed42/bc950c4a-424b-48e0-99f6-589ce37e029d/scratchpad")
SUMMARY = SCRATCH / "task2_batch_summary.txt"
KEPT = SCRATCH / "task2-kept"

LINE = re.compile(r"^run (\d+)\s+hierarchical=(\d+)/(\d+)\S*\s+flat=(\d+)/(\d+)\S*\s+(\d+)min")


def read_scores() -> list[tuple[int, int, int, int]]:
    rows = []
    for line in SUMMARY.read_text().splitlines():
        m = LINE.match(line)
        if m:
            rows.append((int(m[1]), int(m[2]), int(m[4]), int(m[6])))
    return rows


def sign_test(wins: int, losses: int) -> float:
    """Two-sided exact binomial on the non-tied runs, p=0.5 under the
    null that structure makes no difference."""
    n = wins + losses
    if n == 0:
        return 1.0
    k = max(wins, losses)
    tail = sum(comb(n, i) for i in range(k, n + 1)) / 2**n
    return min(1.0, 2 * tail)


def diagnose(arm_dir: Path) -> list[str]:
    """What went wrong with one arm's test file, from the file itself.

    Reads `test_agent_skill.written.py`, which the janitor extracts from
    the ticket BRANCH. This is not interchangeable with what the grader
    scored. The agent commits onto ticket/<task-id> and the working tree
    stays on main, receiving that work only if the ticket reached Done,
    so a team that wrote a perfectly good test and never got it merged
    leaves pristine main in the working tree.

    Until 04/11/2026 this read the working-tree copy, which meant every
    unmerged run was diagnosed as "no test_append_file at all" - a
    statement about the upstream repo's own file, reported as a finding
    about the agents. The merge state is now reported alongside, because
    failing to merge is a different failure from writing a bad test and
    the two should not be summed.

    These are the failure modes actually seen, each tied to the
    checkpoint it costs. Anything unrecognised is reported as such
    rather than forced into a bucket.
    """
    written = arm_dir / "test_agent_skill.written.py"
    if not written.exists():
        legacy = arm_dir / "test_agent_skill.py"
        if legacy.exists():
            return ["kept before the branch fix, not comparable"]
        return ["no artifacts kept"]

    found = []
    merged = (arm_dir / "merged").read_text().strip() if (arm_dir / "merged").exists() else "?"
    if merged == "unmerged":
        found.append("never merged, so the grader scored pristine main")

    src = written.read_text()
    if src.startswith("# no ticket/"):
        return [*found, "no commit on the ticket branch"]
    # An EMPTY file is extraction having failed, not an agent writing
    # nothing, and the two must not be confused. Seen on 04/11/2026
    # from `git show "$br:$TESTFILE"` under zsh, where `:t` is a
    # parameter modifier and silently ate the path, leaving a 0-byte
    # file that would otherwise have been diagnosed as "no
    # test_append_file at all" - a claim about the agent drawn from a
    # quoting bug in the harness.
    if not src.strip():
        return [*found, "written file is EMPTY, extraction failed, do not read as a result"]

    has_def = re.search(r"^\s*def\s+test_append_file\s*\(", src, re.M) is not None
    if not has_def:
        if re.search(r"^\s*test_append_file\s*=", src, re.M):
            found.append("test_append_file bound to a value, not defined as a function")
        elif "test_append_file" in src:
            found.append("test_append_file only as a prefix of other names")
        else:
            found.append("no test_append_file at all")

    if "append_file" in src and not re.search(r"import .*append_file|from .* import", src):
        found.append("append_file never imported")

    # Widened 04/11/2026 after a real run used
    # mock.patch("builtins.open", new_callable=mock.MagicMock), which the
    # earlier pattern missed because it required a dotted prefix before
    # `.open`. This is the vacuous case: it passes pytest and covers 0
    # of the 423 lines a real test covers.
    if re.search(r"patch\(\s*['\"][\w.]*\bopen['\"]", src) or "mock_open" in src:
        found.append("mocks open(), so the real function is not exercised")

    try:
        ast.parse(src)
    except SyntaxError as exc:
        found.append(f"does not parse: {exc.msg}")

    return found or ["no recognised problem"]


def trim_stats(db: Path) -> tuple[int, int, int]:
    """(trims, floor hits, max prompt tokens) for one arm of one run.

    Worth reporting per run rather than assumed uniform. An arm that was
    trimmed hard saw less of its own history than one that was not, so a
    score difference between two runs that trimmed differently is not
    cleanly a difference between team structures. If the two arms trim
    at different rates that is itself a finding, because the
    hierarchical arm passes more messages and so fills the window faster.
    """
    if not db.exists():
        return (-1, -1, -1)
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error:
        return (-1, -1, -1)
    try:
        trims = con.execute(
            "select count(*) from messages where data like '%trimmed transcript%'"
        ).fetchone()[0]
        floor = con.execute(
            "select count(*) from messages where data like '%STILL over budget%'"
        ).fetchone()[0]
        peak = con.execute(
            "select coalesce(max(json_extract(data,'$.token_cost.prompt')), 0) from messages"
        ).fetchone()[0]
    except sqlite3.Error:
        return (-1, -1, -1)
    finally:
        con.close()
    return (trims, floor, int(peak or 0))


def report_trims(rows: list[tuple[int, int, int, int]]) -> None:
    print("\n  context trimming")
    any_floor = False
    for arm in ("hierarchical", "flat"):
        trims = floors = 0
        peaks = []
        missing = 0
        for run, *_ in rows:
            t, f, p = trim_stats(KEPT / f"run{run}" / arm / "eventlog.db")
            if t < 0:
                missing += 1
                continue
            trims += t
            floors += f
            peaks.append(p)
        peak = max(peaks) if peaks else 0
        note = f", {missing} log(s) unreadable" if missing else ""
        print(f"    {arm:14} {trims:3} trims, peak prompt {peak:,} tokens{note}")
        if floors:
            any_floor = True
            print(f"      {floors} turn(s) still over budget at the one-exchange floor")
    if any_floor:
        print("      -> the budget cannot fit a single exchange. Raise the server window")
        print("         or lower MAX_OUTPUT_TOKENS before trusting those runs.")


def main() -> None:
    rows = read_scores()
    if not rows:
        print("  no completed runs yet")
        return

    wins = sum(1 for _, h, f, _ in rows if h > f)
    losses = sum(1 for _, h, f, _ in rows if f > h)
    ties = sum(1 for _, h, f, _ in rows if h == f)
    mins = [m for *_, m in rows]

    print(f"  runs analysed        {len(rows)}")
    print(f"  hierarchical higher  {wins}")
    print(f"  flat higher          {losses}")
    print(f"  tied                 {ties}")
    print(f"  run time             {min(mins)}-{max(mins)} min, mean {sum(mins)/len(mins):.0f}")
    print()
    p = sign_test(wins, losses)
    print(f"  sign test on the {wins + losses} non-tied runs: p = {p:.3f}")
    if ties > len(rows) / 2:
        print("  NOTE: most runs tied, so the task is barely discriminating even here")
    if p > 0.05:
        print("  -> not significant. No structure effect demonstrated.")
    else:
        print("  -> significant at 0.05.")

    print("\n  score distribution")
    for arm, idx in (("hierarchical", 1), ("flat", 2)):
        dist = Counter(r[idx] for r in rows)
        print(f"    {arm:14} " + "  ".join(f"{s}/5 x{n}" for s, n in sorted(dist.items())))

    report_trims(rows)

    print("\n  failure modes")
    for arm in ("hierarchical", "flat"):
        modes: Counter[str] = Counter()
        for run, *_ in rows:
            modes.update(diagnose(KEPT / f"run{run}" / arm))
        print(f"    {arm}")
        for mode, n in modes.most_common():
            print(f"      {n:3}  {mode}")


if __name__ == "__main__":
    main()
