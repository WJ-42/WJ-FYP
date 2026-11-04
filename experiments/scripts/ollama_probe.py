"""Probe a self-hosted Ollama server before building against it.

Same three checks the Groq probe ran, because the same three things
decide whether the WJ-FYP agent layer can use it at all:

  1. Which models the server exposes.
  2. A forced single tool call with a strict enumerated schema, the
     shape Intake/Backlog/Specd/Review/Escalated all use.
  3. A multi-turn tool loop with tool_choice "auto", the In Progress
     shape.

Ollama's OpenAI-compatible layer is a different implementation from
Groq's, so none of what held there is assumed here.

    .venv/bin/python ollama_probe.py [base_url] [model ...]
"""

from __future__ import annotations

import json
import sys
import time

import httpx
from openai import OpenAI

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://10.126.129.12:11434/v1"
MODELS = sys.argv[2:] or ["qwen3:8b"]

DECLARE_TRIGGER = {
    "type": "function",
    "function": {
        "name": "declare_outcome",
        "description": "Declare the FSM trigger for this ticket.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "trigger": {
                    "type": "string",
                    "enum": ["approved", "changes_requested", "escalated"],
                },
                "narrative": {"type": "string"},
            },
            "required": ["trigger", "narrative"],
            "additionalProperties": False,
        },
    },
}

READ_FILE = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "Read a file's current contents from the sandbox.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    },
}

client = OpenAI(api_key="not-needed", base_url=BASE, timeout=httpx.Timeout(300.0, connect=10.0))


def check_models() -> set[str]:
    print(f"\n[1] models exposed by {BASE}")
    try:
        ids = {m.id for m in client.models.list().data}
    except Exception as exc:  # noqa: BLE001
        print(f"    FAILED: {type(exc).__name__}: {str(exc)[:200]}")
        return set()
    for model in sorted(ids):
        print(f"    - {model}")
    return ids


def check_forced(model: str) -> bool:
    started = time.monotonic()
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are the CTO reviewing a ticket."},
                {
                    "role": "user",
                    "content": "The engineer submitted code but never committed it. Rule on it.",
                },
            ],
            tools=[DECLARE_TRIGGER],
            tool_choice={"type": "function", "function": {"name": "declare_outcome"}},
        )
    except Exception as exc:  # noqa: BLE001
        print(f"    FAILED: {type(exc).__name__}: {str(exc)[:250]}")
        return False

    elapsed = time.monotonic() - started
    calls = response.choices[0].message.tool_calls or []
    if not calls:
        content = str(response.choices[0].message.content)[:160]
        print(f"    FAILED: no tool call in {elapsed:.1f}s. content={content!r}")
        return False
    try:
        args = json.loads(calls[0].function.arguments)
    except json.JSONDecodeError:
        print(f"    FAILED: arguments not JSON: {calls[0].function.arguments[:160]!r}")
        return False
    legal = args.get("trigger") in {"approved", "changes_requested", "escalated"}
    usage = response.usage
    tokens = f"{usage.prompt_tokens}+{usage.completion_tokens}" if usage else "?"
    print(f"    {'ok  ' if legal else 'BAD '} trigger={args.get('trigger')!r} {elapsed:5.1f}s tokens={tokens}")
    return legal


def check_multi_turn(model: str) -> bool:
    messages: list = [
        {"role": "system", "content": "You are an engineer with sandbox tools."},
        {"role": "user", "content": "Read README.md, then declare your outcome."},
    ]
    for turn in range(4):
        started = time.monotonic()
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=[READ_FILE, DECLARE_TRIGGER],
                tool_choice="auto",
            )
        except Exception as exc:  # noqa: BLE001
            print(f"    FAILED on turn {turn}: {type(exc).__name__}: {str(exc)[:200]}")
            return False
        elapsed = time.monotonic() - started
        message = response.choices[0].message
        calls = message.tool_calls or []
        if not calls:
            print(f"    turn {turn}: no tool call ({elapsed:.1f}s), said {str(message.content)[:110]!r}")
            return False
        names = [c.function.name for c in calls]
        print(f"    turn {turn}: {names}  ({elapsed:.1f}s)")
        if "declare_outcome" in names:
            return True
        assistant: dict = {"role": "assistant"}
        if message.content:
            assistant["content"] = message.content
        assistant["tool_calls"] = [
            {
                "id": c.id,
                "type": "function",
                "function": {"name": c.function.name, "arguments": c.function.arguments},
            }
            for c in calls
        ]
        messages.append(assistant)
        for call in calls:
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": "# WJ-FYP\n\nA multi-agent simulation.",
                }
            )
    print("    FAILED: never reached declare_outcome in 4 turns")
    return False


def main() -> int:
    ids = check_models()
    results = {}
    for model in MODELS:
        if ids and model not in ids:
            print(f"\n  {model}: NOT on the server")
            continue
        print(f"\n[2] forced single tool call, strict enum  ({model})")
        forced = check_forced(model)
        print(f"[3] multi-turn tool loop  ({model})")
        multi = check_multi_turn(model)
        results[model] = (forced, multi)

    print("\n--- verdict ---")
    for model, (forced, multi) in results.items():
        print(f"  {model:16} forced={'pass' if forced else 'FAIL'}  multi-turn={'pass' if multi else 'FAIL'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
