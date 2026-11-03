from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest import mock

from wjfyp.config import Settings
from wjfyp.eventlog import EventLog
from wjfyp.models.ticket import Ticket, TicketStatus
from wjfyp.orchestrator import scheduler as scheduler_module
from wjfyp.orchestrator.scheduler import DecisionScheduler, SchedulerTick


def settings() -> Settings:
    return Settings.model_validate(
        {"retry_cap": 3, "concurrency": "sequential", "autonomy_mode": "intervention",
         "event_log": {"backend": "sqlite", "path": "unused.db"}}
    )


class SchedulerTestCase(unittest.TestCase):
    def setUp(self):
        self._dir = TemporaryDirectory()
        self.log = EventLog(Path(self._dir.name) / "eventlog.db")
        self.addCleanup(self._dir.cleanup)
        self.addCleanup(self.log.close)

    def ticket(self, tid: str, *, pending=None, decision=None, age_seconds=0) -> Ticket:
        t = Ticket(
            id=tid,
            title=tid,
            description="x",
            status=TicketStatus.REVIEW,
            pending_trigger=pending,
            human_decision=decision,
            updated_at=datetime.now(timezone.utc) - timedelta(seconds=age_seconds),
        )
        self.log.save_ticket(t)
        return t

    def scheduler(self, **kwargs) -> DecisionScheduler:
        kwargs.setdefault("stale_after_seconds", 60.0)
        return DecisionScheduler(
            self.log, agents={}, sandbox_factory=lambda t: None, settings=settings(), **kwargs
        )


class AwaitingAndStaleTest(SchedulerTestCase):
    def test_a_ticket_with_no_pending_trigger_is_not_awaiting_anything(self):
        self.ticket("T-1")
        self.assertEqual(self.scheduler().tick().awaiting, [])

    def test_a_freshly_paused_ticket_is_awaiting_but_not_stale(self):
        self.ticket("T-2", pending="approved", age_seconds=0)
        tick = self.scheduler().tick()
        self.assertEqual([t.id for t in tick.awaiting], ["T-2"])
        self.assertEqual(tick.stale, [])

    def test_a_long_waiting_ticket_is_reported_stale(self):
        self.ticket("T-3", pending="approved", age_seconds=120)
        tick = self.scheduler().tick()
        self.assertEqual([t.id for t in tick.stale], ["T-3"])

    def test_a_stale_ticket_is_never_decided_for_the_human(self):
        """The design decision this module turns on. A ticket nobody
        answers keeps waiting, with its pending trigger untouched. Auto
        approving would hand the machine the one judgement intervention
        mode exists to reserve for a person.
        """
        self.ticket("T-4", pending="approved", age_seconds=9999)
        self.scheduler().tick()
        after = self.log.get_ticket("T-4")
        self.assertEqual(after.pending_trigger, "approved")
        self.assertIsNone(after.human_decision)
        self.assertEqual(after.status, TicketStatus.REVIEW)

    def test_a_ticket_that_already_has_a_decision_is_not_counted_as_awaiting(self):
        """It is the driver's job, not something to nag about."""
        self.ticket("T-5", pending="approved", decision="approved")
        tick = self.scheduler().tick()
        self.assertEqual(tick.awaiting, [])
        self.assertEqual(tick.stale, [])


class AppliesDecisionsTest(SchedulerTestCase):
    """The scheduler's actual job: a recorded decision gets applied.

    No agents are needed for this one. An "approved" at Review is a
    deterministic transition, so it exercises the real driver rather
    than a stand-in.
    """

    def test_a_decided_ticket_advances_and_its_pending_state_is_cleared(self):
        self.ticket("T-6", pending="approved", decision="approved")

        tick = self.scheduler().tick()

        self.assertEqual(len(tick.applied), 1)
        self.assertTrue(tick.did_something)
        after = self.log.get_ticket("T-6")
        self.assertEqual(after.status, TicketStatus.DONE)
        self.assertIsNone(after.pending_trigger)
        self.assertIsNone(after.human_decision)

    def test_an_undecided_ticket_alongside_it_is_left_alone(self):
        self.ticket("T-6", pending="approved", decision="approved")
        self.ticket("T-7", pending="approved", age_seconds=120)

        tick = self.scheduler().tick()

        self.assertEqual(len(tick.applied), 1)
        self.assertEqual([t.id for t in tick.stale], ["T-7"])
        self.assertEqual(self.log.get_ticket("T-7").status, TicketStatus.REVIEW)

    def test_a_quiet_pass_reports_doing_nothing(self):
        self.ticket("T-11")
        tick = self.scheduler().tick()
        self.assertFalse(tick.did_something)


class ResilienceTest(SchedulerTestCase):
    """A standing process that dies on one bad ticket takes every other
    ticket down with it, so the failure has to be caught and carried.

    The failure is injected rather than contrived from real inputs: the
    point under test is the except branch, not any particular way of
    reaching it.
    """

    def test_a_failure_does_not_kill_the_pass_and_reporting_still_runs(self):
        self.ticket("T-7", pending="approved", age_seconds=120)

        with mock.patch.object(
            scheduler_module, "apply_pending_decisions", side_effect=RuntimeError("docker is down")
        ):
            tick = self.scheduler().tick()

        self.assertEqual(len(tick.failed), 1)
        name, exc = tick.failed[0]
        self.assertEqual(name, "apply_pending_decisions")
        self.assertIsInstance(exc, RuntimeError)
        # The awaiting/stale half of the pass ran regardless.
        self.assertEqual([t.id for t in tick.stale], ["T-7"])

    def test_the_loop_keeps_ticking_through_repeated_failures(self):
        with mock.patch.object(
            scheduler_module, "apply_pending_decisions", side_effect=RuntimeError("still down")
        ):
            ticks = self.scheduler(interval_seconds=0).run(max_ticks=3)

        self.assertEqual(len(ticks), 3)
        self.assertTrue(all(t.failed for t in ticks))


class RunLoopTest(SchedulerTestCase):
    def test_max_ticks_bounds_the_loop(self):
        self.ticket("T-9", pending="approved", age_seconds=120)
        ticks = self.scheduler(interval_seconds=0).run(max_ticks=3)
        self.assertEqual(len(ticks), 3)

    def test_a_set_stop_event_ends_it_without_a_single_pass(self):
        stop = Event()
        stop.set()
        self.assertEqual(self.scheduler(interval_seconds=0).run(stop=stop), [])

    def test_on_tick_sees_every_pass(self):
        self.ticket("T-10", pending="approved", age_seconds=120)
        seen: list[SchedulerTick] = []
        self.scheduler(interval_seconds=0).run(max_ticks=2, on_tick=seen.append)
        self.assertEqual(len(seen), 2)
        self.assertTrue(all(t.stale for t in seen))

    def test_stopping_mid_wait_does_not_burn_the_rest_of_the_interval(self):
        """run() waits on the Event rather than sleeping, so a stop is
        acted on at once. A long interval with an already-set stop would
        hang here if that regressed.
        """
        stop = Event()
        stop.set()
        self.scheduler(interval_seconds=3600).run(stop=stop, max_ticks=1)


if __name__ == "__main__":
    unittest.main()
