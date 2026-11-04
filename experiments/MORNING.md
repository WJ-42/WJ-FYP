# Status at 2026-11-04 09:00

## The overnight batch FAILED and was restarted

The 07:12 batch produced ZERO scored runs. Both runs died on
`openai.APITimeoutError`, losing 68 and 19 minutes of completed work.
The alarm fired at 08:41 and it was correct.

**The server was never at fault.** `REQUEST_TIMEOUT` was 90 seconds, a
figure chosen for Groq, applied unchanged to a self-hosted endpoint
where qwen3:8b generates at 7.5 tokens/sec because ~36% of it runs on
the CPU at a 16384 context on a 6 GB card. A 4096-token reply needs
560 seconds. Anything over ~670 tokens could not finish, and all six
retries re-sent the same doomed request.

Confirmed from the Windows server's own logs: two runs of exactly 7
failures (1 attempt + 6 retries), no sleep, no GPU reset, no model
reload, and other requests served normally throughout.

Fixed in commit 4d18b19: 700 second timeout, 1 retry, and a timeout now
costs one ITERATION rather than the whole run. 342 tests passing.

## Open decisions for you

1. **qwen3:4b instead of 8b?** The Windows session measured the 4b at
   53 tok/s against the 8b's 7.5, because the 4b fits the GPU entirely.
   That is a 7x speed difference and much larger than the figure the
   earlier 4b-vs-8b comparison was based on. The old finding that the
   8b wins on time-per-ticket was measured before this was known AND
   against the broken grader, so it is not safe to rely on. Worth
   re-testing before committing another long batch to the 8b.
2. **The 05/11 supervisor agenda** still quotes "2/5 against 1/5",
   which is not safe to present. Decide with the batch results.
3. **FYP-64 needs the Anthropic top-up.** 22 matched Claude runs is
   about $4.50 at measured token rates, so $20 covers it with room.
4. **Commit 706d064's comments still describe a context truncation bug
   that was later DISPROVED.** `token_cost.prompt` is a per-turn SUM,
   not one request. The trim is a valid safety net built on a wrong
   diagnosis. Wording needs correcting when the tree is free.

## The alarm

Rings only on: all runs finished, the batch stopped early, two
consecutive runs scoring nothing, or the inference server gone. Silence
it with the STOP ALARM button or
`~/Projects/WJ-FYP/experiments/alarm/alarm.sh stop`.

## Read these in order

1. `task2_batch_summary.txt`
2. `supervisor.log`
3. `pacer.log` for how many runs it decided to keep and why
4. `.venv/bin/python experiments/scripts/analyse_batch.py`
