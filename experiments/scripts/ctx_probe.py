"""What does this server do when the transcript exceeds num_ctx?

/api/ps reports context_length 4096 for the loaded model. The In
Progress fixed floor is already 1,163 tokens and each qwen3 turn adds
500-1,500 of reasoning, so a real loop crosses 4096 within a couple of
turns. Everything depends on WHICH of these it does:

  - errors, like Groq's 413. Loud, survivable, fixable.
  - slides the window and drops the oldest tokens. Silent, and the
    oldest tokens are the system prompt and the role persona - so a
    persona-comparison run would discard the independent variable and
    still look like it worked.

The test: plant a fact at the very START of a long prompt and ask for it
back at the end. If the answer comes back, the window held. If the model
cannot see it but still answers happily, the front was dropped silently.
A control run well under the limit proves the model can do the task at
all, so a failure at length means truncation rather than incapacity.
"""

import sys
import time

import httpx
from openai import OpenAI

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://10.126.129.12:11434/v1"
MODEL = sys.argv[2] if len(sys.argv) > 2 else "qwen3:8b"

SECRET = "PASSPHRASE-IS-WJFYP-ORANGE-7731"
FILLER = "The audit log records routine access events for later review. "

client = OpenAI(api_key="not-needed", base_url=BASE, timeout=httpx.Timeout(600.0, connect=10.0))


def run(approx_tokens: int) -> None:
    body = FILLER * max(1, approx_tokens // 12)
    prompt = (
        f"FACT_AT_FRONT: {SECRET}-FRONT\n\n"
        f"{body}\n\n"
        f"FACT_AT_BACK: {SECRET}-BACK\n\n"
        "List which of FACT_AT_FRONT and FACT_AT_BACK you can actually see, "
        "and quote each one you can see. Say MISSING for either you cannot."
    )
    started = time.monotonic()
    try:
        r = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2500,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  ~{approx_tokens:6,} tok -> {type(exc).__name__}: {str(exc)[:200]}")
        return
    elapsed = time.monotonic() - started
    text = (r.choices[0].message.content or "").strip()
    prompt_tokens = r.usage.prompt_tokens if r.usage else -1
    front = f"{SECRET}-FRONT" in text
    back = f"{SECRET}-BACK" in text
    verdict = {
        (True, True): "both visible",
        (False, True): "FRONT DROPPED",
        (True, False): "back dropped",
        (False, False): "neither quoted",
    }[(front, back)]
    sent_vs_seen = (
        "" if prompt_tokens >= approx_tokens * 0.8 else f"  <- only {prompt_tokens:,} of ~{approx_tokens:,} accepted"
    )
    print(
        f"  ~{approx_tokens:6,} tok -> prompt_tokens={prompt_tokens:6,}  {elapsed:5.1f}s  "
        f"{verdict:15}{sent_vs_seen}"
    )


print(f"  model {MODEL}, server reports a 4096-token window\n")
for size in [500, 2000, 3500, 6000, 12000]:
    run(size)
