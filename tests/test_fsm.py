"""Pure FSM-level tests (transition table shape, trigger filtering) -
separate from test_orchestrator_loop.py's end-to-end retry-budget
behaviour, since these don't need an event log, agents, or a sandbox at
all.
"""

from __future__ import annotations

import unittest

from wjfyp.models.ticket import TicketStatus
from wjfyp.orchestrator.fsm import (
    EMPTY_DIFF_TRIGGER,
    ESCALATION_COUNTED_TRIGGERS,
    ESCALATION_OVERFLOW_TRIGGER,
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

    def test_retry_counted_triggers_cover_exactly_the_known_loop_back_edges(self) -> None:
        self.assertEqual(
            RETRY_COUNTED_TRIGGERS,
            {
                (TicketStatus.AWAITING_TEST, "tests_failed"),
                (TicketStatus.REVIEW, "changes_requested"),
                (TicketStatus.REVIEW, "empty_diff"),
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


class EscalationOverflowFsmTest(unittest.TestCase):
    """Same shape as RetryOverflowFsmTest, one level up: cto_override
    granting a fresh retry budget also needs a cap on how many times that
    can happen in total, found auditing the rest of the graph for the
    same "reset with no bound on repeated resets" pattern after fixing
    the review-rejection loop.
    """

    def test_escalated_has_a_real_transition_to_halted_on_overflow(self) -> None:
        self.assertEqual(
            next_state(TicketStatus.ESCALATED, ESCALATION_OVERFLOW_TRIGGER),
            TicketStatus.HALTED,
        )

    def test_escalation_counted_triggers_cover_exactly_cto_override(self) -> None:
        self.assertEqual(
            ESCALATION_COUNTED_TRIGGERS, {(TicketStatus.ESCALATED, "cto_override")}
        )

    def test_agent_facing_triggers_hides_the_overflow_trigger_from_escalated(self) -> None:
        """The CTO is invoked for real at escalated too - same reasoning
        as review, this trigger must never appear in its tool schema.
        """
        self.assertIn(ESCALATION_OVERFLOW_TRIGGER, valid_triggers(TicketStatus.ESCALATED))
        self.assertNotIn(
            ESCALATION_OVERFLOW_TRIGGER, agent_facing_triggers(TicketStatus.ESCALATED)
        )
        self.assertEqual(
            set(agent_facing_triggers(TicketStatus.ESCALATED)),
            {"cto_override", "cto_cannot_resolve"},
        )


class EmptyDiffFsmTest(unittest.TestCase):
    """FYP-31: an approved review with an empty diff must be redirected
    back to engineering, not merged - see loop.py's
    _apply_empty_diff_guard for the actual diff check.
    """

    def test_review_has_a_real_transition_to_in_progress_on_empty_diff(self) -> None:
        self.assertEqual(
            next_state(TicketStatus.REVIEW, EMPTY_DIFF_TRIGGER),
            TicketStatus.IN_PROGRESS,
        )

    def test_empty_diff_shares_the_same_retry_budget_as_the_other_loop_back_edges(self) -> None:
        self.assertIn((TicketStatus.REVIEW, EMPTY_DIFF_TRIGGER), RETRY_COUNTED_TRIGGERS)

    def test_agent_facing_triggers_hides_empty_diff_from_review(self) -> None:
        """The CTO must never be offered this trigger directly - it's the
        orchestrator's own deterministic check on the merge, not a
        choice for the CTO to declare it wants to make.
        """
        self.assertIn(EMPTY_DIFF_TRIGGER, valid_triggers(TicketStatus.REVIEW))
        self.assertNotIn(EMPTY_DIFF_TRIGGER, agent_facing_triggers(TicketStatus.REVIEW))
        self.assertEqual(
            set(agent_facing_triggers(TicketStatus.REVIEW)), {"approved", "changes_requested"}
        )


if __name__ == "__main__":
    unittest.main()
