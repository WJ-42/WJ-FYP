#!/bin/bash
# Keeps an unattended 22-run batch honest overnight.
#
# batch_task2.sh has no per-run timeout, so one hung run blocks the loop
# indefinitely. Editing that script while bash is reading it risks
# corrupting its execution, so this supervises from outside.
#
# Three failure modes, and the third is the one that nearly got missed.
#
# 1. STALL. A run hangs. Killed after STALL_MIN with no event log write,
#    which makes the batch loop advance to the next run by itself.
#
# 2. SERVER. The inference endpoint stops answering. Aborted after
#    SERVER_STRIKES consecutive failures, because 20 runs against a dead
#    endpoint is worse than 2 runs and a clear reason.
#
# 3. FAST FAILURE, added 04/11/2026 after noticing the hole while asked
#    how to stop Docker dying. If Docker, the grader venv, or the clone
#    is unavailable, every run fails in SECONDS. The stall detector
#    cannot fire, because a stall needs a live run that has gone quiet
#    and these die instantly. The batch would burn all 22 runs in a few
#    minutes, write "batch finished", and the completion waiter would
#    report success. The morning's result would be 22 empty scores under
#    a log saying everything completed - which is strictly worse than a
#    crash, because it looks like data.
#
#    So an empty-score summary line is counted, and two in a row aborts.
#    Two rather than one, because a single run can legitimately fail on
#    its own (a model that will not declare an outcome raises, by
#    design). Two consecutive is systemic.
#
# The Docker DAEMON itself needs no help from this script: docker.socket
# is enabled and docker.service is Restart=always, so systemd brings it
# back on its own. What needed handling was the batch's reaction to its
# brief absence, which is case 3.
#
# Two bugs were found in the first version of this script before it was
# ever armed, both of the kind it exists to prevent:
#  1. `find -newermt "-30 min"` matched NOTHING even with a file three
#     minutes old, so every check would have declared a stall and killed
#     every healthy run on sight. `-mmin -30` is the correct idiom.
#  2. `pkill -f batch_task2.sh` matched four processes, only one the
#     driver, the rest interactive shells whose command line contained
#     the pattern. Every lookup below is filtered by `comm` as well,
#     which also stops the awk in the pipeline matching itself.
SCRATCH=/tmp/claude-1000/-home-waleed42/bc950c4a-424b-48e0-99f6-589ce37e029d/scratchpad
SUM="$SCRATCH/task2_batch_summary.txt"
LOG="$SCRATCH/supervisor.log"
BASE=http://10.126.129.12:11434

# A run writes an event log entry on every tool call, so silence means
# stalled. The loosest legitimate silence is a hung request:
# REQUEST_TIMEOUT 90s with MAX_RETRIES 6, about 9 minutes plus backoff.
STALL_MIN=30
SERVER_STRIKES=5
EMPTY_RUNS_ABORT=2

say() { echo "$(date '+%F %H:%M:%S') $*" >> "$LOG"; }

# The audible alarm. Fired ONLY for states that are terminal and need a
# human: the batch is finished, or it has stopped for a reason this
# script cannot fix. Never for a stalled run, because that is handled
# here and the batch carries on - waking someone for something already
# dealt with is how an alarm stops being believed.
ALARM="$HOME/Projects/WJ-FYP/experiments/alarm/alarm.sh"
wake() {
  say "ALARM fired: $1"
  [ -x "$ALARM" ] || { say "ALARM MISSING at $ALARM, cannot wake anyone"; return; }
  setsid nohup "$ALARM" start "$1" >/dev/null 2>&1 &
}

driver_pid() { ps -eo pid,comm,args --no-headers | awk '$2=="bash" && /batch_task2[.]sh/ {print $1; exit}'; }
run_pid()    { ps -eo pid,comm,args --no-headers | awk '$2=="python" && /wjfyp[.]eval[.]cli compare/ {print $1; exit}'; }
scored()     { grep -cE '^run [0-9]+ ' "$SUM" 2>/dev/null || echo 0; }
empties()    { grep -cE '^run [0-9]+ +hierarchical= +flat=' "$SUM" 2>/dev/null || echo 0; }

cleanup_containers() {
  for c in $(docker ps -q --filter ancestor=wjfyp-sandbox:latest 2>/dev/null); do
    docker stop "$c" >/dev/null 2>&1 && say "stopped container $c"
  done
}

say "supervisor v3 up (audible alarm wired), stall=${STALL_MIN}min server_strikes=$SERVER_STRIKES empty_abort=$EMPTY_RUNS_ABORT driver=$(driver_pid)"

strikes=0
stalls=0
dockerbad=0
last_scored=$(scored)
consec_empty=0
while true; do
  if grep -q "batch finished" "$SUM" 2>/dev/null; then
    say "batch finished. runs scored: $(scored), empty: $(empties), stalls handled: $stalls"
    wake "All $(scored) runs are done. Nothing failed that needed you. Ready for the Claude runs."
    exit 0
  fi
  d=$(driver_pid)
  if [ -z "$d" ]; then
    say "WARN driver gone with no finish line. scored: $(scored), empty: $(empties), stalls: $stalls"
    wake "The batch stopped early after $(scored) of 22 runs. It did not finish and I cannot restart it for you."
    exit 1
  fi

  # --- fast-failure detection, case 3 ---
  now_scored=$(scored)
  if [ "$now_scored" -gt "$last_scored" ]; then
    newline=$(grep -E '^run [0-9]+ ' "$SUM" | tail -1)
    if echo "$newline" | grep -qE '^run [0-9]+ +hierarchical= +flat='; then
      consec_empty=$((consec_empty + 1))
      say "WARN empty-score run ($consec_empty consecutive): $newline"
      if [ "$consec_empty" -ge "$EMPTY_RUNS_ABORT" ]; then
        say "ABORT $consec_empty consecutive runs produced no scores, so this is systemic not per-run"
        say "ABORT docker says: $(timeout 10 docker info --format '{{.ServerVersion}}' 2>&1 | head -1)"
        say "ABORT grader python: $([ -x "$HOME/Projects/WJ-FYP/.grader-venv/bin/python" ] && echo present || echo MISSING)"
        r=$(run_pid); [ -n "$r" ] && kill "$r" 2>/dev/null
        kill "$d" 2>/dev/null
        sleep 5
        cleanup_containers
        say "ABORT done. The $(( now_scored - consec_empty )) scored runs before this remain valid."
        wake "Batch aborted: $consec_empty runs in a row produced no scores. Something systemic broke and needs you."
        exit 3
      fi
    else
      [ "$consec_empty" -gt 0 ] && say "a scored run followed $consec_empty empty one(s), counter reset"
      consec_empty=0
    fi
    last_scored=$now_scored
  fi

  # --- docker reachability, logged for diagnosis ---
  if timeout 10 docker info >/dev/null 2>&1; then
    [ "$dockerbad" -gt 0 ] && say "docker responding again after $dockerbad check(s)"
    dockerbad=0
  else
    dockerbad=$((dockerbad + 1))
    say "WARN docker not responding, check $dockerbad (systemd should restart it; the empty-run abort is the real guard)"
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
      cleanup_containers
      say "ABORT done. Runs already in the summary are unaffected and remain valid."
      wake "Batch aborted: the Windows inference server stopped answering. Needs you to bring it back."
      exit 2
    fi
  fi

  # --- stall ---
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
    cleanup_containers
    stalls=$((stalls + 1))
    say "STALL handled ($stalls so far)"
    sleep 120
  fi
  sleep 60
done
