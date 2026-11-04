"""Why did the forced call come back empty on qwen3:4b?

The probe only looked at .content and .tool_calls. An empty pair can
mean several different things, and they have different fixes:

  - the model put everything in a reasoning channel (fix: read it)
  - Ollama ignored the forced tool_choice (fix: a different mode)
  - the model genuinely emitted nothing (fix: a bigger model)

So dump the raw message, and try each tool_choice mode Ollama accepts.
"""

import json
import sys
import time

import httpx
from openai import OpenAI

BASE = sys.argv[1]
MODEL = sys.argv[2]

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

MODES = [
    ("forced by name", {"type": "function", "function": {"name": "declare_outcome"}}),
    ("required", "required"),
    ("auto", "auto"),
]

for label, choice in MODES:
    print(f"\n=== tool_choice = {label} ===")
    started = time.monotonic()
    try:
        r = client.chat.completions.create(
            model=MODEL, messages=MESSAGES, tools=[TOOL], tool_choice=choice
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  {type(exc).__name__}: {str(exc)[:300]}")
        continue
    elapsed = time.monotonic() - started
    msg = r.choices[0].message
    print(f"  {elapsed:.1f}s  finish_reason={r.choices[0].finish_reason}")
    print(f"  dump: {json.dumps(msg.model_dump(), default=str)[:900]}")
    if r.usage:
        print(f"  tokens: prompt={r.usage.prompt_tokens} completion={r.usage.completion_tokens}")
