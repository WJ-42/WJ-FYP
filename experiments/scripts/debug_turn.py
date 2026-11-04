"""Dump exactly what gets sent and what comes back, to find which
tool call in history the endpoint is objecting to."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from wjfyp.models.ticket import Ticket, TicketStatus
from wjfyp.orchestrator.agent import AgentContext
from wjfyp.orchestrator import openai_agent as oa
from wjfyp.sandbox.controller import TestResult
from wjfyp.sandbox.fake import FakeSandboxController

real_create = oa.OpenAIAgent._create
CALL = {"n": 0}


def traced(self, **kwargs):
    CALL["n"] += 1
    msgs = kwargs.get("messages", [])
    print(f"\n=== request {CALL['n']}  ({len(msgs)} messages) ===")
    for i, m in enumerate(msgs):
        role = m["role"] if isinstance(m, dict) else m.role
        if isinstance(m, dict) and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                print(f"  [{i}] {role} TOOLCALL {tc['function']['name']} args={tc['function']['arguments'][:160]}")
        elif role == "tool":
            print(f"  [{i}] tool result id={m['tool_call_id']} -> {str(m['content'])[:80]!r}")
        else:
            content = m.get("content") if isinstance(m, dict) else None
            print(f"  [{i}] {role}: {str(content)[:110]!r}")
    try:
        r = real_create(self, **kwargs)
    except Exception as e:
        print(f"  !! {type(e).__name__}: {str(e)[:300]}")
        raise
    msg = r.choices[0].message
    for tc in msg.tool_calls or []:
        print(f"  <- returned {tc.function.name} args={tc.function.arguments[:160]}")
    if not msg.tool_calls:
        print(f"  <- returned text {str(msg.content)[:140]!r}")
    return r


oa.OpenAIAgent._create = traced

pool = oa.build_agent_pool(
    roles_path=Path("config/roles_groq.yaml"), api_key=os.environ["GROQ_API_KEY"]
)
from wjfyp.sandbox.controller import CommandResult
sandbox = FakeSandboxController(
    command_results=[
        CommandResult(exit_code=0, stdout="session.py\ntests/\n", stderr=""),
    ]
    * 20,
    test_results=[
        TestResult(
            passed=True,
            output="2 passed",
            fail_to_pass_total=1,
            fail_to_pass_passed=1,
            pass_to_pass_total=1,
            pass_to_pass_passed=1,
        )
        for _ in range(8)
    ]
)
sandbox.write_file("session.py", "def read_expiry(h):\n    return int(h)\n")

ticket = Ticket(
    id="AUTH-200",
    title="Validate the session expiry header",
    description="The session expiry header is read but never validated.",
    acceptance_criteria=["Reject a malformed expiry header.", "A test covers it."],
    status=TicketStatus.IN_PROGRESS,
)
eng = pool["engineering"][0]
try:
    r = eng.invoke(
        AgentContext(
            ticket=ticket,
            channel_id="c1",
            role="engineering",
            instance_id=eng.instance_id,
            sandbox=sandbox,
        )
    )
    print(f"\nRESULT trigger={r.trigger} commits={len(sandbox.commits)}")
except Exception as e:
    print(f"\nFAILED: {type(e).__name__}: {str(e)[:400]}")
    sys.exit(1)
