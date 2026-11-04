"""How often does the forced call come back empty, and what is different
about the runs that fail?

One pass proves nothing about a sampling model. The arms need hundreds
of forced calls per run, so a 1-in-10 silent failure is the difference
between a working harness and a run that dies halfway with no error.

Hypothesis being tested: qwen3 emits reasoning before the tool call, and
Ollama's default context window is small. A long think would exhaust the
window and the call would be cut off, which looks exactly like "no tool
call" with empty content.
"""

import sys
import time

import httpx
from openai import OpenAI

BASE, MODEL = sys.argv[1], sys.argv[2]
RUNS = int(sys.argv[3]) if len(sys.argv) > 3 else 10

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

MESSAGES = [
    {"role": "system", "content": "You are the CTO reviewing a ticket."},
    {"role": "user", "content": "The engineer submitted code but never committed it. Rule on it."},
]

client = OpenAI(api_key="not-needed", base_url=BASE, timeout=httpx.Timeout(300.0, connect=10.0))

passes = 0
for i in range(RUNS):
    started = time.monotonic()
    try:
        r = client.chat.completions.create(
            model=MODEL,
            messages=MESSAGES,
            tools=[TOOL],
            tool_choice={"type": "function", "function": {"name": "declare_outcome"}},
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  {i:2}  EXC {type(exc).__name__}: {str(exc)[:120]}")
        continue
    elapsed = time.monotonic() - started
    msg = r.choices[0].message
    calls = msg.tool_calls or []
    reasoning = getattr(msg, "reasoning", None) or ""
    comp = r.usage.completion_tokens if r.usage else -1
    if calls:
        passes += 1
        print(f"  {i:2}  ok    {elapsed:5.1f}s  completion={comp:5}  reasoning_chars={len(reasoning):5}  finish={r.choices[0].finish_reason}")
    else:
        print(f"  {i:2}  EMPTY {elapsed:5.1f}s  completion={comp:5}  reasoning_chars={len(reasoning):5}  finish={r.choices[0].finish_reason}")

print(f"\n  {passes}/{RUNS} produced a tool call")
