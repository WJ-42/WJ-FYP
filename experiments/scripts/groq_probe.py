"""Probe Groq before building anything against it.

Checks the three things that decide whether the WJ-FYP agent layer can
run on it at all:

  1. Which models the account can actually see.
  2. A forced single tool call with an enumerated schema - the shape
     every one of Intake/Backlog/Specd/Review/Escalated uses.
  3. A multi-turn tool loop with tool_choice "auto" - the In Progress
     shape.

Uses the official OpenAI SDK against Groq's OpenAI-compatible endpoint,
which is the same client the real backend will use. Reads GROQ_API_KEY
from the environment.

    .venv/bin/python groq_probe.py
"""

from __future__ import annotations

import json
import os
import sys

from openai import OpenAI

BASE = "https://api.groq.com/openai/v1"
LADDER = [
    ("large", "openai/gpt-oss-120b"),
    ("mid (preview)", "qwen/qwen3.8-27b"),
    ("small", "openai/gpt-oss-20b"),
    ("tiny", "allam-2-7b"),
]

# The real trigger-declaring tool, copied in shape from claude_agent.py:
# one required enum argument the model cannot free-text around.
DECLARE_TRIGGER = {
    "type": "function",
    "function": {
        "name": "declare_trigger",
        "description": "Declare the FSM trigger for this ticket.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "trigger": {
                    "type": "string",
                    "enum": ["approved", "changes_requested", "escalated"],
                },
                "rationale": {"type": "string"},
            },
            "required": ["trigger", "rationale"],
            "additionalProperties": False,
        },
    },
}

READ_FILE = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "Read a file from the sandbox.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    },
}


def check_models(client: OpenAI) -> set[str]:
    print("\n[1] models the account can see")
    try:
        ids = {m.id for m in client.models.list().data}
    except Exception as e:  # noqa: BLE001 - a probe wants the message
        print(f"    FAILED: {e!r}")
        return set()
    for label, model in LADDER:
        print(f"    {'ok  ' if model in ids else 'MISSING'} {label:22} {model}")
    extra = sorted(i for i in ids if i not in {m for _, m in LADDER})
    print(f"    ({len(ids)} total)")
    for i in extra:
        print(f"      - {i}")
    return ids


def check_forced_tool(client: OpenAI, model: str) -> bool:
    try:
        r = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are the CTO reviewing a ticket."},
                {
                    "role": "user",
                    "content": "The engineer submitted code but never committed it. Rule on it.",
                },
            ],
            tools=[DECLARE_TRIGGER],
            tool_choice={"type": "function", "function": {"name": "declare_trigger"}},
            max_tokens=400,
        )
    except Exception as e:  # noqa: BLE001
        print(f"    FAILED: {type(e).__name__}: {str(e)[:300]}")
        return False

    calls = r.choices[0].message.tool_calls or []
    if not calls:
        print(f"    FAILED: no tool call. content={str(r.choices[0].message.content)[:200]!r}")
        return False
    try:
        args = json.loads(calls[0].function.arguments)
    except json.JSONDecodeError as e:
        print(f"    FAILED: arguments not valid JSON ({e}): {calls[0].function.arguments[:200]!r}")
        return False

    legal = calls[0].function.name == "declare_trigger" and args.get("trigger") in {
        "approved",
        "changes_requested",
        "escalated",
    }
    u = r.usage
    print(
        f"    {'ok  ' if legal else 'BAD '} trigger={args.get('trigger')!r} "
        f"tokens={u.prompt_tokens}+{u.completion_tokens}"
    )
    return legal


def check_multi_turn(client: OpenAI, model: str) -> bool:
    messages: list = [
        {"role": "system", "content": "You are an engineer with sandbox tools."},
        {"role": "user", "content": "Read README.md, then declare your trigger."},
    ]
    for turn in range(4):
        try:
            r = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=[READ_FILE, DECLARE_TRIGGER],
                tool_choice="auto",
                max_tokens=500,
            )
        except Exception as e:  # noqa: BLE001
            print(f"    FAILED on turn {turn}: {type(e).__name__}: {str(e)[:300]}")
            return False

        msg = r.choices[0].message
        calls = msg.tool_calls or []
        if not calls:
            print(f"    turn {turn}: no tool call, model said {str(msg.content)[:140]!r}")
            return False
        names = [c.function.name for c in calls]
        print(f"    turn {turn}: {names}")
        if "declare_trigger" in names:
            return True
        messages.append(msg.model_dump(exclude_none=True))
        for c in calls:
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": c.id,
                    "content": "# WJ-FYP\n\nA multi-agent simulation.",
                }
            )
    print("    FAILED: never reached declare_trigger in 4 turns")
    return False


def main() -> int:
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        print("GROQ_API_KEY is not set. Export it, then re-run.", file=sys.stderr)
        return 2
    client = OpenAI(api_key=key, base_url=BASE)

    ids = check_models(client)

    print("\n[2] forced single tool call, strict enumerated schema")
    forced = {m: check_forced_tool(client, m) for _, m in LADDER if m in ids}

    print("\n[3] multi-turn tool loop (In Progress shape)")
    multi = {}
    for label, m in LADDER:
        if m in ids:
            print(f"  {label} ({m})")
            multi[m] = check_multi_turn(client, m)

    print("\n--- verdict ---")
    for label, m in LADDER:
        if m not in ids:
            print(f"  {label:22} UNAVAILABLE")
            continue
        print(
            f"  {label:22} forced={'pass' if forced.get(m) else 'FAIL'}  "
            f"multi-turn={'pass' if multi.get(m) else 'FAIL'}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
