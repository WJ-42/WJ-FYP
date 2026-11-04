#!/bin/bash
# Decides how many of the 22 runs are worth waiting for, once there is
# real evidence of how long a run takes.
#
# Waleed asked for "a mix of both": let a couple more runs land, and if
# the full batch still projects at ~20 hours, cut it back. He then went
# to sleep, so this makes that call rather than leaving it until he
# wakes.
#
# Two bounds decide the target, and the floor wins ties:
#
#  FLOOR 12. A two-sided exact binomial sign test on fewer than about
#  six non-tied runs cannot reach p < 0.05 even if every run agrees, so
#  a batch cut below this is not merely weaker, it is incapable of
#  producing a significant result. Finishing sooner is worthless if the
#  answer cannot be significant.
#
#  DEADLINE 22:00 today. That leaves tomorrow morning free for FYP-64
#  and the supervisor agenda before the 15:30 meeting.
#
# It never kills a run in flight. It waits for the current run to score,
# so no run is half-finished and wasted.
SCRATCH=/tmp/claude-1000/-home-waleed42/bc950c4a-424b-48e0-99f6-589ce37e029d/scratchpad
SUM="$SCRATCH/task2_batch_summary.txt"
LOG="$SCRATCH/pacer.log"
MEASURE_AFTER=3
FLOOR=12
CEILING=22
DEADLINE_HHMM=22:00

say() { echo "$(date '+%F %H:%M:%S') $*" >> "$LOG"; }
count_matching() {
  # NOT `grep -c ... || echo 0`. grep -c prints 0 and exits 1 when there
  # are no matches, so the || fired as well and the caller received the
  # two-line string "0\n0", which fails every numeric comparison. That
  # silently skipped the pacer's measurement wait and would have stopped
  # the supervisor's two-empty-runs abort from ever firing.
  local n
  n=$(grep -cE "$1" "$2" 2>/dev/null)
  echo "${n:-0}"
}
scored() { count_matching '^run [0-9]+ ' "$SUM"; }
driver_pid() { ps -eo pid,comm,args --no-headers | awk '$2=="bash" && /batch_task2[.]sh/ {print $1; exit}'; }
run_pid()    { ps -eo pid,comm,args --no-headers | awk '$2=="python" && /wjfyp[.]eval[.]cli compare/ {print $1; exit}'; }

say "pacer up. measuring after $MEASURE_AFTER runs, floor $FLOOR, ceiling $CEILING, deadline $DEADLINE_HHMM"

# --- phase 1: wait for enough runs to have a real number ---
while [ "$(scored)" -lt "$MEASURE_AFTER" ]; do
  grep -q "batch finished" "$SUM" 2>/dev/null && { say "batch finished before the pacer had to decide"; exit 0; }
  [ -z "$(driver_pid)" ] && { say "driver gone before the pacer could measure"; exit 1; }
  sleep 120
done

# Median, not mean: one pathological run should not drag the estimate.
median=$(grep -oE '[0-9]+min' "$SUM" | tr -d 'min' | sort -n | awk '{a[NR]=$1} END{print (NR%2)? a[(NR+1)/2] : int((a[NR/2]+a[NR/2+1])/2)}')
[ -z "$median" ] || [ "$median" -lt 1 ] && median=45
now=$(date +%s)
deadline=$(date -d "today $DEADLINE_HHMM" +%s)
[ "$deadline" -le "$now" ] && deadline=$(date -d "tomorrow $DEADLINE_HHMM" +%s)
avail=$(( (deadline - now) / 60 ))
done_so_far=$(scored)
fits=$(( avail / median + done_so_far ))

target=$fits
[ "$target" -gt "$CEILING" ] && target=$CEILING
[ "$target" -lt "$FLOOR" ] && target=$FLOOR

say "measured median run time: ${median}min over $done_so_far run(s)"
say "minutes available before $DEADLINE_HHMM: $avail, which fits about $fits runs in total"
say "TARGET SET TO $target runs (floor $FLOOR, ceiling $CEILING)"
if [ "$target" -ge "$CEILING" ]; then
  say "that is the full batch, so the pacer will not cut anything short"
else
  projected=$(( (target - done_so_far) * median ))
  say "projected finish about $(date -d "+$projected minutes" '+%H:%M'), versus $(date -d "+$(( (CEILING - done_so_far) * median )) minutes" '+%H:%M') for all $CEILING"
fi

# --- phase 2: stop the batch cleanly once the target is reached ---
while true; do
  grep -q "batch finished" "$SUM" 2>/dev/null && { say "batch finished on its own"; exit 0; }
  d=$(driver_pid)
  [ -z "$d" ] && { say "driver gone, nothing left to pace"; exit 1; }
  n=$(scored)
  if [ "$n" -ge "$target" ]; then
    # The finish line goes in FIRST. The supervisor polls for it, and if
    # it saw the driver vanish without one it would raise the "stopped
    # early" alarm, which would be the wrong thing to wake someone with.
    echo "batch finished $(date '+%F %H:%M:%S') (paced: stopped at $n of $CEILING, median ${median}min/run)" >> "$SUM"
    say "target reached at $n runs, wrote the finish line"
    kill "$d" 2>/dev/null
    sleep 1
    r=$(run_pid); [ -n "$r" ] && { kill "$r" 2>/dev/null; say "stopped the run that had just started"; }
    sleep 4
    for c in $(docker ps -q --filter ancestor=wjfyp-sandbox:latest 2>/dev/null); do
      docker stop "$c" >/dev/null 2>&1 && say "stopped container $c"
    done
    say "done. $n runs are in the summary and all of them are valid."
    exit 0
  fi
  sleep 60
done
