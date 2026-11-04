"""A REAL In Progress turn on a self-hosted model, against real Docker.

The earlier probe fed the model a canned three-line tool result, which
proves the protocol round-trips and nothing else. In Progress is the
only turn shape with a growing transcript, real file contents, real
pytest output and a commit gate, and it is the shape that failed on
Groq (413 Request too large). So it is the one that decides whether
local hosting actually works.

What this checks, in order of what would hurt most to get wrong:

  1. The loop terminates with a real FSM trigger rather than running
     out of iterations.
  2. A code_submission is backed by an actual git commit, or is
     rejected - the FYP-31 guarantee, which is structural and must not
     depend on the model being honest.
  3. The transcript does not blow a context limit as it grows, which is
     what killed this on the free hosted tier.

    .venv/bin/python ollama_in_progress.py <base_url> <model>
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from openai import OpenAI

from wjfyp.eventlog import EventLog
from wjfyp.models.agent import RoleConfig
from wjfyp.models.message import MessageType
from wjfyp.models.ticket import Ticket, TicketStatus
from wjfyp.orchestrator.agent import AgentContext
from wjfyp.orchestrator.openai_agent import MAX_RETRIES, OpenAIAgent
from wjfyp.orchestrator.verification import UNVERIFIED_PREFIX
from wjfyp.sandbox.docker_controller import DockerAttemptSandbox
from wjfyp.sandbox.git_workspace import GitWorkspace

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://10.126.129.12:11434/v1"
MODEL = sys.argv[2] if len(sys.argv) > 2 else "qwen3:8b"

SOURCE = '''def parse_expiry(header):
    """Return the integer seconds from a Cache-Control style header."""
    return int(header.split("=")[1])
'''

TEST = '''from expiry import parse_expiry


def test_parses_a_normal_value():
    assert parse_expiry("max-age=60") == 60
'''


def build_repo(path: Path) -> GitWorkspace:
    run = lambda *a: subprocess.run(["git", "-C", str(path), *a], check=True, capture_output=True)
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True, capture_output=True)
    run("config", "user.email", "test@example.com")
    run("config", "user.name", "Test")
    (path / "expiry.py").write_text(SOURCE)
    (path / "test_expiry.py").write_text(TEST)
    run("add", ".")
    run("commit", "-m", "initial")
    return GitWorkspace(path, default_base="main")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        repo.mkdir()
        workspace = build_repo(repo)
        log = EventLog(Path(tmp) / "events.db")

        ticket = Ticket(
            id="LOC-1",
            title="Validate the expiry header before using it",
            description=(
                "parse_expiry in expiry.py assumes the header is well formed. A header "
                "with no '=' or a non-numeric value raises an unhelpful error. Make it "
                "reject malformed values clearly, and cover it with a test."
            ),
            acceptance_criteria=[
                "A malformed header raises ValueError with a clear message.",
                "A test covers the malformed case.",
            ],
            status=TicketStatus.IN_PROGRESS,
            branch_name="ticket/LOC-1",
        )

        role = RoleConfig(id="engineering", name="Software Engineer", tier=3, model=MODEL, count=1)
        client = OpenAI(
            api_key="not-needed",
            base_url=BASE,
            max_retries=MAX_RETRIES,
            timeout=httpx.Timeout(300.0, connect=10.0),
        )
        agent = OpenAIAgent(role, "engineering-1", client=client, event_log=log)

        print(f"  model   {MODEL}\n  base    {BASE}\n  ticket  {ticket.id}\n")
        sandbox = DockerAttemptSandbox(ticket, workspace)
        started = time.monotonic()
        try:
            response = agent.invoke(
                AgentContext(
                    ticket=ticket,
                    channel_id="eng",
                    role="engineering",
                    instance_id="engineering-1",
                    sandbox=sandbox,
                )
            )
        finally:
            elapsed = time.monotonic() - started
            sandbox.close()

        print(f"  --- finished in {elapsed:.0f}s ---\n")
        print(f"  trigger       {response.trigger!r}")
        cost = response.message.token_cost
        print(f"  tokens        prompt={cost.prompt:,} completion={cost.completion:,}")
        print(f"  narrative     {response.message.content.text[:300]!r}\n")

        calls = [m for m in log.get_all_messages() if m.type == MessageType.TOOL_CALL]
        print(f"  tool calls    {len(calls)}")
        for m in calls:
            print(f"    - {m.content.text[:100]}")

        resamples = [
            m
            for m in log.get_all_messages()
            if m.type == MessageType.SYSTEM and "discarded attempt" in m.content.text
        ]
        print(f"\n  resamples     {len(resamples)}")
        for m in resamples:
            print(f"    - {m.content.text[:140]}")

        committed = any("git_commit" in m.content.text for m in calls)
        flagged = response.message.content.text.startswith(UNVERIFIED_PREFIX)
        print(f"\n  git_commit attempted  {committed}")
        print(f"  flagged unverified    {flagged}")

        rejects = [
            m
            for m in log.get_all_messages()
            if m.type == MessageType.SYSTEM and "rejected tool call" in m.content.text
        ]
        print(f"  rejected tool calls   {len(rejects)}")
        for m in rejects:
            print(f"    - {m.content.text[:150]}")

        # The gate is satisfied EITHER by a real commit OR by the
        # narrative being flagged. Only an unflagged code_submission
        # with no commit is a failure, because that is the one case
        # where a false claim would be recorded as true.
        if response.trigger == "code_submission" and not committed and not flagged:
            print("\n  !! unflagged code_submission with no commit - FYP-31 gate FAILED")
            return 1
        if response.trigger == "code_submission" and flagged:
            print("\n  gate worked: declaration accepted under protest and marked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
