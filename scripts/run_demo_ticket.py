"""Manual/demo driver: runs one real ticket through the full orchestrator
FSM against real Claude models and a real Docker sandbox, in intervention
mode so a human can watch it live in the dashboard (see run_dashboard.py)
and click real Approve/Request Changes decisions.

This is not part of the shipped system and has no automated test
coverage of its own - it's a manual testing/demo tool, the same role
FYP-25's live pipeline verification session used it for on 2026-09-28
(see cs3ip-fyp-overview memory). Requires:
  - ANTHROPIC_API_KEY set (e.g. in a local .env file, see README)
  - Docker access working and wjfyp-sandbox:latest built
  - DEMO_REPO_PATH pointing at a real git repo to use as the ticket's
    target codebase - keep it OUTSIDE this repo (a sibling directory,
    not nested inside WJ-FYP's own git tree) and dependency-free,
    matching what wjfyp-sandbox:latest actually ships (git + pytest,
    stdlib only, no network egress) - the first live run escalated
    for real because its demo repo needed Flask, which isn't there.

Usage: PYTHONPATH=src python scripts/run_demo_ticket.py
"""

import time
from pathlib import Path

from wjfyp.config import Settings, load_roles
from wjfyp.eventlog import EventLog
from wjfyp.models.channel import Channel
from wjfyp.models.ticket import Ticket, TicketStatus
from wjfyp.orchestrator.claude_agent import ClaudeAgent
from wjfyp.orchestrator.driver import apply_pending_decisions, find_pending_decisions
from wjfyp.orchestrator.loop import run
from wjfyp.sandbox.docker_controller import DockerAttemptSandbox
from wjfyp.sandbox.git_workspace import GitWorkspace

# A sibling directory, not nested inside WJ-FYP - see the module
# docstring for why. Adjust freely; recreate with a plain `git init`
# plus an initial commit if it doesn't exist yet.
DEMO_REPO_PATH = Path(__file__).resolve().parent.parent.parent / "wjfyp-demo-target"
EVENT_LOG_PATH = "data/live-run.db"
# Bump this for a fresh run against the same event log/demo repo - reusing
# an id re-saves over whatever ticket already has it, resetting it back
# to Intake even if it previously reached Done.
TICKET_ID = "DEMO-1"

TERMINAL = {TicketStatus.DONE, TicketStatus.HALTED}


def build_agents(event_log: EventLog) -> dict:
    roles = load_roles()  # config/roles.yaml - the hierarchical team
    return {
        role.id: [ClaudeAgent(role, f"{role.id}-{i + 1}", event_log=event_log) for i in range(role.count)]
        for role in roles
    }


def main() -> None:
    if not DEMO_REPO_PATH.is_dir():
        raise SystemExit(
            f"{DEMO_REPO_PATH} doesn't exist - create it first: a plain `git init -b main`, "
            "one commit, no external dependencies."
        )

    event_log = EventLog(EVENT_LOG_PATH)
    agents = build_agents(event_log)
    workspace = GitWorkspace(DEMO_REPO_PATH)
    sandbox_factory = lambda ticket: DockerAttemptSandbox(ticket, workspace)  # noqa: E731
    settings = Settings(autonomy_mode="intervention")

    ticket = Ticket(
        id=TICKET_ID,
        title="Add a word-count utility",
        description=(
            "Add a function count_words(text) in wordcount.py that returns the number "
            "of whitespace-separated words in a string, plus a pytest test covering an "
            "empty string, a single word, and a multi-word sentence. This repo has no "
            "network access and no dependencies installed beyond Python's standard "
            "library and pytest, so implement this using only the standard library."
        ),
        branch_name=f"ticket/{TICKET_ID}",
    )
    channel = Channel(id=f"channel-{ticket.id}", key=f"ticket:{ticket.id}", ticket_ref=ticket.id)

    print(f"Starting {ticket.id} through the real pipeline (intervention mode)...", flush=True)
    result = run(ticket, channel, event_log, agents, sandbox_factory, settings, workspace=workspace)

    while result.ticket.status not in TERMINAL:
        if result.advanced is False and result.paused_trigger is not None:
            print(
                f"Paused at {result.ticket.status.value} awaiting a human decision "
                f"(proposed: {result.paused_trigger}). Waiting for it in the dashboard...",
                flush=True,
            )
        waited = 0
        while not find_pending_decisions(event_log):
            time.sleep(3)
            waited += 3
            if waited >= 1800:
                print("Gave up waiting for a decision after 30 minutes.", flush=True)
                return
        print("Decision recorded - resuming...", flush=True)
        results = apply_pending_decisions(event_log, agents, sandbox_factory, settings, workspace=workspace)
        result = next(r for r in results if r.ticket.id == ticket.id)

    print(f"DONE - {ticket.id} reached terminal status: {result.ticket.status.value}", flush=True)
    event_log.close()


if __name__ == "__main__":
    main()
