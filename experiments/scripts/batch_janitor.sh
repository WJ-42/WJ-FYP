#!/bin/bash
# Keeps the batch from filling tmpfs.
#
# /tmp is RAM-backed here, and each comparison writes two 88 MB repo
# checkouts that would otherwise accumulate to ~3.9 GB across 22 runs
# and starve the machine. What is actually worth keeping per run is
# about 100 KB: the event logs, the run log, and the test file each
# team wrote, which is the artifact the whole experiment is scoring.
#
# Only touches a run directory once that run has been SCORED, which is
# the line appearing in the summary. A run in flight is never touched.
SCRATCH=/tmp/claude-1000/-home-waleed42/bc950c4a-424b-48e0-99f6-589ce37e029d/scratchpad
SUM="$SCRATCH/task2_batch_summary.txt"
KEEP="$SCRATCH/task2-kept"
TASK=sde-write-a-unit-test-for-append_file-function
TESTFILE=tests/unit/test_agent_skill.py
mkdir -p "$KEEP"

while true; do
  grep -q "batch finished" "$SUM" 2>/dev/null && last=999 || last=0
  for d in "$SCRATCH"/task2-batch/run*; do
    [ -d "$d" ] || continue
    n=$(basename "$d" | sed 's/run//')
    # Scored means the summary already has this run's line.
    grep -q "^run $n " "$SUM" 2>/dev/null || continue
    [ -d "$d/hierarchical" ] || [ -d "$d/flat" ] || continue
    mkdir -p "$KEEP/run$n"
    cp -f "$d/run.log" "$KEEP/run$n/" 2>/dev/null
    for arm in hierarchical flat; do
      mkdir -p "$KEEP/run$n/$arm"
      # .backup rather than cp, because the event log is in WAL mode.
      # A clean process exit checkpoints and removes the -wal, but a run
      # that was killed leaves recent messages ONLY in it, and copying
      # the db alone would silently lose the tail of exactly the runs
      # worth inspecting. Falls back to copying all three files if
      # sqlite3 is unavailable.
      if command -v sqlite3 >/dev/null 2>&1; then
        sqlite3 "$d/$arm/eventlog.db" ".backup '$KEEP/run$n/$arm/eventlog.db'" 2>/dev/null \
          || cp -f "$d/$arm/eventlog.db"* "$KEEP/run$n/$arm/" 2>/dev/null
      else
        cp -f "$d/$arm/eventlog.db"* "$KEEP/run$n/$arm/" 2>/dev/null
      fi
      # Two DIFFERENT versions of the test file, and the distinction
      # is a result rather than bookkeeping.
      #
      # The agent commits onto ticket/<task-id>. The working tree stays
      # on main and only receives that work if the ticket reached Done,
      # because the merge is what Review->Done fires. So the working
      # tree holds what the GRADER scored, while the branch holds what
      # the agent actually WROTE, and they differ exactly when a team
      # produced a test but never got it merged.
      #
      # Copying only the working tree, which is what this did until
      # 04/11/2026, saves pristine main for every unmerged run. The
      # failure-mode analysis would then have reported "no
      # test_append_file at all" for all 22 runs in both arms and been
      # presented as a finding about the agents.
      br="ticket/$TASK"
      git -C "$d/$arm" show "$br:$TESTFILE" > "$KEEP/run$n/$arm/test_agent_skill.written.py" 2>/dev/null \
        || echo "# no $br, or no $TESTFILE on it" > "$KEEP/run$n/$arm/test_agent_skill.written.py"
      cp -f "$d/$arm/$TESTFILE" "$KEEP/run$n/$arm/test_agent_skill.graded.py" 2>/dev/null
      git -C "$d/$arm" log --oneline main.."$br" > "$KEEP/run$n/$arm/branch.commits" 2>/dev/null
      git -C "$d/$arm" diff main.."$br" > "$KEEP/run$n/$arm/branch.diff" 2>/dev/null
      git -C "$d/$arm" branch --contains "$br" 2>/dev/null | grep -qx '[* ] main' \
        && echo merged > "$KEEP/run$n/$arm/merged" \
        || echo unmerged > "$KEEP/run$n/$arm/merged"
      rm -rf "$d/$arm"
    done
    echo "$(date '+%H:%M:%S') reclaimed run$n" >> "$SCRATCH/janitor.log"
  done
  [ "$last" = "999" ] && break
  sleep 60
done
