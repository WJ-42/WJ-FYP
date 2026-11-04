#!/bin/bash
# Keeps an unattended 22-run batch honest overnight.
#
# batch_task2.sh has no per-run timeout, so one hung run blocks the loop
# indefinitely and the morning's result is a partial set with no
# explanation. Editing that script while bash is reading it risks
# corrupting its execution, so this supervises from outside: it kills a
# STALLED run, which makes the batch loop advance by itself, and aborts
# the batch if the inference server has gone away, because 20 runs
# against a dead endpoint is worse than 2 runs and a clear reason.
#
# Everything it does goes to supervisor.log with a timestamp, so
# "what happened overnight" has an answer that is read rather than
# reconstructed.
#
# TWO BUGS WERE FOUND IN THIS SCRIPT BEFORE IT WAS FIRST ARMED, both of
# the kind it exists to prevent:
#
#  1. `find -newermt "-30 min"` matched NOTHING even with a file three
#     minutes old, so every check would have declared a stall and killed
#     a healthy run on sight. `-mmin -30` is the correct idiom.
#  2. `pkill -f batch_task2.sh` matched four processes, only one of
#     which was the driver. The others were interactive shells whose
#     command line happened to contain the pattern. Matching on the
#     command line alone is how a watchdog kills the wrong thing, so
#     every lookup below is filtered by `comm` as well. This also stops
#     the `awk` in the pipeline matching itself.
SCRATCH=/tmp/claude-1000/-home-waleed42/bc950c4a-424b-48e0-99f6-589ce37e029d/scratchpad
SUM="$SCRATCH/task2_batch_summary.txt"
LOG="$SCRATCH/supervisor.log"
BASE=http://10.126.129.12:11434

# A run writes an event log entry on every tool call, so silence means
# stalled rather than busy. The loosest legitimate silence is a hung
# request: REQUEST_TIMEOUT is 90s with MAX_RETRIES 6, about 9 minutes
# plus backoff. 30 is comfortably past that and still under a run's
# length, so it cannot kill a run that is merely slow.
STALL_MIN=30
# Five consecutive failures, not one, because a single curl failure is a
# blip and not a dead server.
SERVER_STRIKES=5

say() { echo "$(date '+%F %H:%M:%S') $*" >> "$LOG"; }

# Filtered by comm so an interactive shell, or the awk in this very
# pipeline, cannot be mistaken for the target. See bug 2 above.
driver_pid() { ps -eo pid,comm,args --no-headers | awk '$2=="bash" && /batch_task2[.]sh/ {print $1; exit}'; }
run_pid()    { ps -eo pid,comm,args --no-headers | awk '$2=="python" && /wjfyp[.]eval[.]cli compare/ {print $1; exit}'; }

say "supervisor up, stall=${STALL_MIN}min server_strikes=$SERVER_STRIKES driver=$(driver_pid)"

strikes=0
stalls=0
while true; do
  if grep -q "batch finished" "$SUM" 2>/dev/null; then
    say "batch finished cleanly. stalls handled: $stalls"
    exit 0
  fi
  d=$(driver_pid)
  if [ -z "$d" ]; then
    say "WARN batch driver gone with no finish line in the summary. stalls handled: $stalls"
    exit 1
  fi

  # --- inference server ---
  if curl -s --max-time 10 "$BASE/api/tags" >/dev/null 2>&1; then
    [ "$strikes" -gt 0 ] && say "server recovered after $strikes strike(s)"
    strikes=0
  else
    strikes=$((strikes + 1))
    say "WARN server unreachable, strike $strikes/$SERVER_STRIKES"
    if [ "$strikes" -ge "$SERVER_STRIKES" ]; then
      say "ABORT server down for $strikes consecutive checks, stopping the batch"
      r=$(run_pid); [ -n "$r" ] && kill "$r" 2>/dev/null
      kill "$d" 2>/dev/null
      sleep 5
      for c in $(docker ps -q --filter ancestor=wjfyp-sandbox:latest 2>/dev/null); do
        docker stop "$c" >/dev/null 2>&1 && say "stopped container $c"
      done
      say "ABORT done. Runs already in the summary are unaffected and remain valid."
      exit 2
    fi
  fi

  # --- progress of the live run ---
  # -mmin, not -newermt. The -wal file updates on every write, so this
  # tracks real activity rather than directory metadata.
  recent=$(find "$SCRATCH"/task2-batch/run*/ -name "eventlog.db*" -mmin -$STALL_MIN 2>/dev/null | head -1)
  anylog=$(find "$SCRATCH"/task2-batch/run*/ -name "eventlog.db*" 2>/dev/null | head -1)
  r=$(run_pid)
  if [ -n "$r" ] && [ -n "$anylog" ] && [ -z "$recent" ]; then
    where=$(ls -dt "$SCRATCH"/task2-batch/run*/ 2>/dev/null | head -1)
    say "STALL no event log write for ${STALL_MIN}min in $where, killing pid $r so the batch advances"
    kill "$r" 2>/dev/null
    sleep 10
    still=$(run_pid)
    [ "$still" = "$r" ] && { kill -9 "$r" 2>/dev/null; say "STALL escalated to SIGKILL on $r"; }
    for c in $(docker ps -q --filter ancestor=wjfyp-sandbox:latest 2>/dev/null); do
      docker stop "$c" >/dev/null 2>&1 && say "stopped leaked container $c"
    done
    stalls=$((stalls + 1))
    say "STALL handled ($stalls so far), the batch should start the next run"
    sleep 120
  fi
  sleep 60
done
