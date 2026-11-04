"""Does a single request simply exceed the per-minute token budget once
the In Progress transcript grows?

If it does, the loop can never complete on the free tier no matter how
long it retries, because every retry carries the same oversized payload.
That is a wall, not a delay, and it decides whether end-to-end runs are
possible here at all.
"""

import os
import time

import httpx
from openai import OpenAI, RateLimitError

client = OpenAI(
    api_key=os.environ["GROQ_API_KEY"],
    base_url="https://api.groq.com/openai/v1",
    max_retries=0,
    timeout=httpx.Timeout(90.0, connect=10.0),
)

FILLER = "The quick brown fox jumps over the lazy dog. " * 40

for approx in [1000, 4000, 8000, 12000, 20000]:
    content = (FILLER * max(1, approx // 400))[: approx * 4]
    started = time.monotonic()
    try:
        response = client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=[{"role": "user", "content": content + "\n\nReply with the single word OK."}],
            max_tokens=20,
        )
        elapsed = time.monotonic() - started
        print(f"  ~{approx:6,} -> OK   prompt={response.usage.prompt_tokens:6,}  {elapsed:5.1f}s")
    except RateLimitError as exc:
        elapsed = time.monotonic() - started
        print(f"  ~{approx:6,} -> 429  {elapsed:5.1f}s  {str(exc)[:200]}")
    except Exception as exc:  # noqa: BLE001 - a probe wants the message
        elapsed = time.monotonic() - started
        print(f"  ~{approx:6,} -> {type(exc).__name__}  {elapsed:5.1f}s  {str(exc)[:160]}")
    time.sleep(2)
