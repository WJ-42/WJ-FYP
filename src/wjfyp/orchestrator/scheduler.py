"""The periodic process that actually moves intervention-mode work along.

FYP-59. Recording a human decision does not apply it:
driver.apply_pending_decisions() is the building block, but nothing ever
called it on a schedule, so a ticket paused for approval stayed paused
until something resumed it by hand. The README said as much.

All three of the agent-team write-ups Ziyang sent independently name a
scheduler as one of five parts of a working agent team, alongside the
router, the specialists, the shared board and the approval gate. This
project had the other four.

**What happens to a decision nobody answers.** This scheduler reports
them and never decides them. Auto-approving on a timeout would hand the
machine the one judgement intervention mode exists to reserve for a
human, and auto-rejecting would silently discard work for the crime of
the operator being asleep. One of those guides describes background
approvals in their own system expiring after about ten minutes, after
which the action quietly does not run; that is the failure mode this
deliberately does not reproduce. A ticket waiting too long is surfaced
as `stale` on every tick, loudly and repeatedly, and keeps waiting.

The split between tick() and run() is for testing. tick() is one pass
with no sleeping and no loop, so its behaviour can be asserted directly;
run() is the thin scheduling wrapper around it, with clock and sleep
injectable so a test of the loop never actually waits.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import Event
from typing import Callable

from wjfyp.config import Settings
from wjfyp.eventlog import EventLog
from wjfyp.models.ticket import Ticket
from wjfyp.orchestrator.agent import Agent
from wjfyp.orchestrator.driver import apply_pending_decisions, find_pending_decisions
from wjfyp.orchestrator.loop import HiddenTestLookup, SandboxFactory, StepResult
from wjfyp.sandbox.git_workspace import GitWorkspace

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 10.0
# How long a ticket may sit awaiting a human before it is called out.
# Generous on purpose: this is a prompt to a person, not a timeout with
# teeth, and crying stale after a couple of minutes would train whoever
# is watching to ignore it.
DEFAULT_STALE_AFTER_SECONDS = 900.0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class SchedulerTick:
    """What one pass found and did.

    `applied` is work that moved. `awaiting` is every ticket paused for
    a human, including ones that have only just paused. `stale` is the
    subset of `awaiting` that has been waiting longer than the
    threshold, and is the only part meant to prompt anyone.
    """

    applied: list[StepResult] = field(default_factory=list)
    awaiting: list[Ticket] = field(default_factory=list)
    stale: list[Ticket] = field(default_factory=list)
    failed: list[tuple[str, Exception]] = field(default_factory=list)

    @property
    def did_something(self) -> bool:
        return bool(self.applied)


class DecisionScheduler:
    """Calls apply_pending_decisions() on an interval and reports what
    is stuck waiting on a human.

    One scheduler drives one event log and one agent pool, matching how
    the orchestrator is run elsewhere. It owns no state beyond its
    configuration, so stopping and restarting it loses nothing: every
    decision it acts on is already persisted on the ticket.
    """

    def __init__(
        self,
        event_log: EventLog,
        agents: dict[str, list[Agent]],
        sandbox_factory: SandboxFactory,
        settings: Settings,
        hidden_tests: HiddenTestLookup | None = None,
        workspace: GitWorkspace | None = None,
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
        stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS,
        now: Callable[[], datetime] = _utcnow,
    ):
        self._event_log = event_log
        self._agents = agents
        self._sandbox_factory = sandbox_factory
        self._settings = settings
        self._hidden_tests = hidden_tests
        self._workspace = workspace
        self.interval_seconds = interval_seconds
        self.stale_after = timedelta(seconds=stale_after_seconds)
        self._now = now

    def tick(self) -> SchedulerTick:
        """One pass: apply what is decided, report what is waiting.

        A failure applying one ticket is caught and recorded rather than
        propagated. A standing process that dies because a single ticket
        hit a sandbox error would take every other ticket down with it,
        which is worse than the error it is reacting to. The exception
        is kept on the tick so a caller can surface it instead of it
        vanishing.
        """
        result = SchedulerTick()
        try:
            result.applied = apply_pending_decisions(
                self._event_log,
                self._agents,
                self._sandbox_factory,
                self._settings,
                hidden_tests=self._hidden_tests,
                workspace=self._workspace,
            )
        except Exception as exc:  # noqa: BLE001 - see docstring
            logger.exception("applying pending decisions failed")
            result.failed.append(("apply_pending_decisions", exc))

        decided = {t.id for t in find_pending_decisions(self._event_log)}
        cutoff = self._now() - self.stale_after
        for ticket in self._event_log.list_tickets():
            if ticket.pending_trigger is None or ticket.id in decided:
                continue
            result.awaiting.append(ticket)
            if ticket.updated_at <= cutoff:
                result.stale.append(ticket)

        if result.stale:
            logger.warning(
                "%d ticket(s) have been waiting on a human decision for over %s: %s",
                len(result.stale),
                self.stale_after,
                ", ".join(t.id for t in result.stale),
            )
        return result

    def run(
        self,
        stop: Event | None = None,
        max_ticks: int | None = None,
        on_tick: Callable[[SchedulerTick], None] | None = None,
    ) -> list[SchedulerTick]:
        """Tick until `stop` is set, or `max_ticks` passes have run.

        Waits on the Event rather than sleeping, so a stop is acted on
        immediately instead of after the rest of an interval. `max_ticks`
        exists for tests and for a one-shot pass from a cron-style
        caller that would rather not hold a process open.
        """
        stop = stop or Event()
        ticks: list[SchedulerTick] = []
        while not stop.is_set():
            tick = self.tick()
            ticks.append(tick)
            if on_tick is not None:
                on_tick(tick)
            if max_ticks is not None and len(ticks) >= max_ticks:
                break
            if stop.wait(self.interval_seconds):
                break
        return ticks
