"""End-to-end smoke test of OpenAIAgent against the live Groq account.

Builds a real agent pool from config/roles_groq.yaml and invokes two
genuinely different stages:

  1. CTO at Review, a single forced tool call (gpt-oss-120b).
  2. Engineering at In Progress, the multi-turn sandbox loop
     (gpt-oss-20b), against FakeSandbox so no Docker is needed.

Also checks the invariant that matters most: a code_submission declared
without a git_commit must be rejected rather than accepted.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

from wjfyp.models.ticket import Ticket, TicketStatus
from wjfyp.orchestrator.agent import AgentContext
from wjfyp.orchestrator.budget import SpendGuard
from wjfyp.orchestrator.openai_agent import build_agent_pool
from wjfyp.sandbox.controller import TestResult
from wjfyp.sandbox.fake import FakeSandboxController

ROLES = Path("config/roles_groq.yaml")


def show(label: str, response, elapsed: float) -> None:
    m = response.message
    print(f"\n  trigger    : {response.trigger}")
    print(f"  type       : {m.type}")
    print(f"  narrative  : {m.content.text[:220]}")
    if m.content.handoff:
        print(f"  handoff    : done={m.content.handoff.done[:110]!r}")
    print(f"  tokens     : {m.token_cost.prompt} in / {m.token_cost.completion} out")
    print(f"  wall clock : {elapsed:.1f}s")


def main() -> int:
    import time

    if not os.environ.get("GROQ_API_KEY"):
        print("GROQ_API_KEY not set", file=sys.stderr)
        return 2

    guard = SpendGuard(cap_usd=1.00)
    pool = build_agent_pool(roles_path=ROLES, api_key=os.environ["GROQ_API_KEY"], spend_guard=guard)
    print(f"pool: { {k: len(v) for k, v in pool.items()} }")

    ticket = Ticket(
        id="AUTH-200",
        title="Validate the session expiry header",
        description=(
            "The session expiry header is read but never validated, so a malformed "
            "value is treated as a valid far-future expiry."
        ),
        acceptance_criteria=[
            "A malformed expiry header is rejected rather than treated as valid.",
            "A test covers the malformed case.",
        ],
        status=TicketStatus.REVIEW,
    )

    # --- 1. CTO at Review: single forced tool call -----------------------
    print("\n[1] CTO at Review (gpt-oss-120b, forced single tool call)")
    cto = pool["cto"][0]
    t0 = time.monotonic()
    r = cto.invoke(
        AgentContext(ticket=ticket, channel_id="c1", role="cto", instance_id=cto.instance_id)
    )
    show("cto", r, time.monotonic() - t0)
    assert r.trigger in {"approved", "changes_requested"}, r.trigger

    # --- 2. Engineering at In Progress: multi-turn sandbox loop ----------
    print("\n[2] Engineering at In Progress (gpt-oss-20b, multi-turn sandbox loop)")
    sandbox = FakeSandboxController(
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
    eng_ticket = ticket.model_copy(update={"status": TicketStatus.IN_PROGRESS})
    eng = pool["engineering"][0]
    t0 = time.monotonic()
    r2 = eng.invoke(
        AgentContext(
            ticket=eng_ticket,
            channel_id="c1",
            role="engineering",
            instance_id=eng.instance_id,
            sandbox=sandbox,
        )
    )
    show("engineering", r2, time.monotonic() - t0)

    committed = bool(getattr(sandbox, "commits", None))
    print(f"\n  sandbox commits : {len(getattr(sandbox, 'commits', []) or [])}")
    print(f"  files touched   : {sorted(getattr(sandbox, 'files', {}) or {})}")

    # The contract, same as ClaudeAgent's: an uncommitted code_submission
    # is REJECTED inside the loop, and only reachable at all through the
    # iteration-budget escape hatch, where it must be flagged in the
    # narrative rather than silently accepted. Both outcomes below are
    # correct; an unflagged uncommitted submission is the only failure.
    flagged = r2.message.content.text.startswith("[submitted without committing]")
    if r2.trigger != "code_submission":
        print(f"  ok: declared {r2.trigger!r} rather than an uncommitted submission")
    elif committed:
        print("  ok: code_submission backed by a real commit")
    elif flagged:
        print("  ok: uncommitted submission reached the escape hatch and was flagged")
    else:
        print("\n  FAIL: uncommitted code_submission accepted unflagged")
        return 1

    print(f"\nspend guard estimate: ${guard.spent_usd:.5f} (free tier bills $0)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
