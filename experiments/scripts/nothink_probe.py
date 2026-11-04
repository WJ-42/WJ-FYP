"""Can qwen3's visible reasoning be turned off, and does the agent layer
still work without it?

qwen3 spent 3.8x more completion tokens than Claude reaching the same
answer, almost all of it reasoning emitted before each action. If that
can be switched off without losing the behaviour the FSM depends on,
every future run gets several times cheaper in wall-clock.

Two mechanisms exist and it is not documented which this server
honours, so both are measured rather than assumed:

  A. chat_template_kwargs {"enable_thinking": false}, passed through
     extra_body - the documented Qwen switch.
  B. "/no_think" appended to the prompt - the soft switch qwen3 reads
     from the text itself.

Measuring tokens alone would not be enough. A mode that is fast and
cannot make a forced tool call is useless here, because every FSM
transition in this project is a forced tool call. So each mode is
checked for BOTH.
"""

from __future__ import annotations

import json
import sys
import time

import httpx
from openai import OpenAI

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://10.126.129.12:11434/v1"
MODEL = sys.argv[2] if len(sys.argv) > 2 else "qwen3:8b"

TOOL = {
    "type": "function",
    "function": {
        "name": "declare_outcome",
        "description": "Declare the FSM trigger for this ticket.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "trigger": {"type": "string", "enum": ["approved", "changes_requested", "escalated"]},
                "narrative": {"type": "string"},
            },
            "required": ["trigger", "narrative"],
            "additionalProperties": False,
        },
    },
}

SYSTEM = "You are the CTO reviewing a ticket."
USER = "The engineer submitted code but never committed it. Rule on it."

client = OpenAI(api_key="not-needed", base_url=BASE, timeout=httpx.Timeout(300.0, connect=10.0))


def attempt(label: str, messages: list, extra: dict | None) -> tuple[int, float, bool, str]:
    started = time.monotonic()
    try:
        r = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=[TOOL],
            tool_choice={"type": "function", "function": {"name": "declare_outcome"}},
            extra_body=extra or {},
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  {label:34} FAILED {type(exc).__name__}: {str(exc)[:110]}")
        return 0, 0.0, False, ""
    elapsed = time.monotonic() - started
    msg = r.choices[0].message
    calls = msg.tool_calls or []
    reasoning = getattr(msg, "reasoning", None) or ""
    comp = r.usage.completion_tokens if r.usage else 0
    ok = False
    trig = ""
    if calls:
        try:
            args = json.loads(calls[0].function.arguments)
            trig = args.get("trigger", "")
            ok = trig in {"approved", "changes_requested", "escalated"}
        except json.JSONDecodeError:
            pass
    print(
        f"  {label:34} completion={comp:5}  reasoning_chars={len(reasoning):5}  "
        f"{elapsed:5.1f}s  tool_call={'ok' if ok else 'NO'}  {trig}"
    )
    return comp, elapsed, ok, reasoning


MODES = [
    ("baseline (thinking on)", [{"role": "system", "content": SYSTEM}, {"role": "user", "content": USER}], None),
    (
        "A: enable_thinking=false",
        [{"role": "system", "content": SYSTEM}, {"role": "user", "content": USER}],
        {"chat_template_kwargs": {"enable_thinking": False}},
    ),
    (
        "B: /no_think in user turn",
        [{"role": "system", "content": SYSTEM}, {"role": "user", "content": USER + " /no_think"}],
        None,
    ),
]

print(f"  model {MODEL}\n")
results = {}
for label, messages, extra in MODES:
    # Fifteen samples each. Three could not separate a real reduction
    # from one long outlier: the baseline's mean was 641 tokens and its
    # median 419, because a single 1,100-token sample carried it. The
    # refusal rate needs the same treatment - 1 of 3 looks alarming
    # against the 9 of 10 measured earlier, but 3 samples cannot tell a
    # regression from a bad night.
    comps, oks = [], []
    for _ in range(15):
        c, _e, ok, _r = attempt(label, messages, extra)
        comps.append(c)
        oks.append(ok)
    ordered = sorted(comps)
    median = ordered[len(ordered) // 2]
    results[label] = (sum(comps) / len(comps), sum(oks), median)
    print()

print("  --- mean completion tokens over 15 samples, and tool calls that worked ---")
base = results[MODES[0][0]][2] or 1
for label, (mean, ok_count, median) in results.items():
    print(f"  {label:34} mean={mean:6.0f}  median={median:6.0f}  "
          f"({median/base:4.2f}x baseline median)  tool_calls_ok={ok_count}/15")
