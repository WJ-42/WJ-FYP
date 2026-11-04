# Overnight batch, started 2026-11-04 07:12:18

## The alarm will have woken you if anything needed you

It rings on four things only, and all four are terminal states that
need a person:

1. All 22 runs finished.
2. The batch stopped early without finishing.
3. Two runs in a row produced no scores, so something systemic broke.
4. The Windows inference server stopped answering.

It does NOT ring for a single stalled run, because the supervisor kills
that run and the batch carries on by itself.

To silence it: click **STOP ALARM** on the dialog, or run
`~/Projects/WJ-FYP/experiments/alarm/alarm.sh stop`.
It restores your volume and mute state either way, and gives up on its
own after 45 minutes.

## Read these in order

1. `task2_batch_summary.txt` - one line per completed run, plus a
   "batch finished" line if it got all the way through.
2. `supervisor.log` - everything the watchdog did and why the alarm
   rang. Only the "supervisor up" lines means a clean night.
3. `janitor.log` - which runs had their 89 MB checkouts reclaimed.
4. Then run the analysis:
   `cd ~/Projects/WJ-FYP && .venv/bin/python experiments/scripts/analyse_batch.py`
   for the sign test, score distribution, trim counts and failure modes.

## What each log line means

- A summary line with EMPTY scores (`hierarchical=  flat=`) is a run
  that crashed or was killed by the watchdog. `supervisor.log` says
  which.
- `STALL` means a run made no event log write for 30 minutes and was
  killed so the batch could advance. The runs around it are unaffected.
- `ABORT` means the batch was stopped deliberately. Runs already in the
  summary remain valid.

## Known open items

- The source comments and commit 706d064 still describe a context
  truncation bug that was later DISPROVED. `token_cost.prompt` is a
  per-turn SUM, not one request, so no prompt ever exceeded the window.
  The trim is a valid safety net built on a wrong diagnosis. Fix the
  wording once the batch is done and the tree is safe to edit.
- The 05/11 supervisor agenda still quotes "2/5 against 1/5", which is
  not safe to present. Decide with the batch results.
- FYP-64 needs an Anthropic top-up. 22 matched Claude runs is about
  $4.50 at measured token rates, so $20 covers it with room.
