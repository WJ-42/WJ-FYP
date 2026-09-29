"""Pure FSM-level tests (transition table shape, trigger filtering) -
separate from test_orchestrator_loop.py's end-to-end retry-budget
behaviour, since these don't need an event log, agents, or a sandbox at
all.
"""

from __future__ import annotations

import unittest

from wjfyp.models.ticket import TicketStatus
from wjfyp.orchestrator.fsm import (
    RETRY_COUNTED_TRIGGERS,
    RETRY_OVERFLOW_TRIGGER,
    agent_facing_triggers,
    next_state,
    valid_triggers,
)


class RetryOverflowFsmTest(unittest.TestCase):
    def test_both_loop_back_edges_escalate_on_overflow(self) -> None:
        """FYP-27: awaiting_test and review must both have a real
        transition to escalated on the shared overflow trigger - without
        this, next_state() raises KeyError the moment the budget is
        actually exceeded from review, which is exactly the situation
        the old code never reached (see loop.py's _apply_retry_budget).
        """
        self.assertEqual(
            next_state(TicketStatus.AWAITING_TEST, RETRY_OVERFLOW_TRIGGER),
            TicketStatus.ESCALATED,
        )
        self.assertEqual(
            next_state(TicketStatus.REVIEW, RETRY_OVERFLOW_TRIGGER),
            TicketStatus.ESCALATED,
        )

    def test_retry_counted_triggers_cover_exactly_the_two_loop_back_edges(self) -> None:
        self.assertEqual(
            RETRY_COUNTED_TRIGGERS,
            {
                (TicketStatus.AWAITING_TEST, "tests_failed"),
                (TicketStatus.REVIEW, "changes_requested"),
            },
        )

    def test_agent_facing_triggers_hides_the_overflow_trigger_from_review(self) -> None:
        """The CTO is invoked for real at review (unlike awaiting_test,
        which no agent ever acts at) - agent_facing_triggers() is what
        keeps the overflow trigger out of its tool schema, so the model
        can never just declare it wants to escalate for itself.
        """
        self.assertIn(RETRY_OVERFLOW_TRIGGER, valid_triggers(TicketStatus.REVIEW))
        self.assertNotIn(RETRY_OVERFLOW_TRIGGER, agent_facing_triggers(TicketStatus.REVIEW))
        self.assertEqual(
            set(agent_facing_triggers(TicketStatus.REVIEW)), {"approved", "changes_requested"}
        )


if __name__ == "__main__":
    unittest.main()
