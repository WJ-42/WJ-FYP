"""Is there a HARD guarantee available instead of resampling?

Ollama can constrain generation to a JSON schema (grammar-constrained
decoding), which the tool_choice path does not do. If the
OpenAI-compatible endpoint exposes it as response_format, a forced
declaration could be made impossible to refuse rather than retried.

The cost of using it: Anthropic and Groq both elicit the declaration as
a forced TOOL CALL, so a third request shape would mean the backends no
longer send the same request, only the same prompt text. Worth knowing
whether the option exists before deciding against it.
"""

import json
import sys
import time

import httpx
from openai import OpenAI

BASE, MODEL = sys.argv[1], sys.argv[2]
RUNS = int(sys.argv[3]) if len(sys.argv) > 3 else 10

SCHEMA = {
    "type": "object",
    "properties": {
        "trigger": {"type": "string", "enum": ["approved", "changes_requested", "escalated"]},
        "narrative": {"type": "string"},
        "handoff_done": {"type": "string"},
        "handoff_remaining": {"type": "string"},
        "handoff_notes_for_next": {"type": "string"},
    },
    "required": ["trigger", "narrative", "handoff_done", "handoff_remaining", "handoff_notes_for_next"],
    "additionalProperties": False,
}

MESSAGES = [
    {"role": "system", "content": "You are the CTO reviewing a ticket."},
    {"role": "user", "content": "The engineer submitted code but never committed it. Rule on it."},
]

client = OpenAI(api_key="not-needed", base_url=BASE, timeout=httpx.Timeout(300.0, connect=10.0))

good = 0
for i in range(RUNS):
    started = time.monotonic()
    try:
        r = client.chat.completions.create(
            model=MODEL,
            messages=MESSAGES,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "declare_outcome", "strict": True, "schema": SCHEMA},
            },
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  {i:2}  EXC {type(exc).__name__}: {str(exc)[:200]}")
        continue
    elapsed = time.monotonic() - started
    text = r.choices[0].message.content or ""
    try:
        args = json.loads(text)
    except json.JSONDecodeError:
        print(f"  {i:2}  BAD JSON {elapsed:5.1f}s  {text[:120]!r}")
        continue
    missing = [k for k in SCHEMA["required"] if k not in args]
    legal = args.get("trigger") in SCHEMA["properties"]["trigger"]["enum"]
    if not missing and legal:
        good += 1
        print(f"  {i:2}  ok   {elapsed:5.1f}s  trigger={args['trigger']!r}  completion={r.usage.completion_tokens}")
    else:
        print(f"  {i:2}  BAD  {elapsed:5.1f}s  missing={missing} trigger={args.get('trigger')!r}")

print(f"\n  {good}/{RUNS} conformed")
