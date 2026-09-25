from __future__ import annotations

import unittest

from wjfyp.eval.mast import MAST_CATEGORIES, MAST_NAMES, MastFailureMode, tag_run
from wjfyp.models.message import AgentRef, Message, MessageContent, MessageType
from wjfyp.models.ticket import Ticket, TicketStatus


class ScriptedMastJudge:
    """Stub MastJudge for tests - same pattern as
    test_orchestrator_loop.py's ScriptedAgent: no real LLM call, just
    hands back whatever was scripted.
    """

    def __init__(self, tags: list[MastFailureMode]):
        self._tags = list(tags)

    def judge(self, ticket: Ticket, messages: list[Message]) -> list[MastFailureMode]:
        return list(self._tags)


def _ticket(status: TicketStatus) -> Ticket:
    return Ticket(id="TCK-1", title="t", description="d", status=status)


def _message() -> Message:
    return Message(
        sender=AgentRef(role="cto", instance_id="cto-1"),
        channel_id="channel-TCK-1",
        type=MessageType.STATUS_UPDATE,
        ticket_ref="TCK-1",
        content=MessageContent(text="..."),
    )


class MastTaxonomyTest(unittest.TestCase):
    def test_all_14_codes_have_a_name_and_category(self) -> None:
        self.assertEqual(len(list(MastFailureMode)), 14)
        for mode in MastFailureMode:
            self.assertIn(mode, MAST_NAMES)
            self.assertIn(mode, MAST_CATEGORIES)

    def test_category_counts_match_the_paper(self) -> None:
        # FC1 has 5 modes, FC2 has 6, FC3 has 3 - confirmed against the
        # paper's own Figure 1 (see cs3ip-evaluation-detail memory).
        counts: dict[str, int] = {}
        for category in MAST_CATEGORIES.values():
            counts[category] = counts.get(category, 0) + 1
        self.assertEqual(counts["FC1 System Design Issues"], 5)
        self.assertEqual(counts["FC2 Inter-Agent Misalignment"], 6)
        self.assertEqual(counts["FC3 Task Verification"], 3)


class TagRunRuleBasedTest(unittest.TestCase):
    def test_done_gets_no_rule_based_tags(self) -> None:
        self.assertEqual(tag_run(_ticket(TicketStatus.DONE), []), [])

    def test_in_progress_gets_no_rule_based_tags(self) -> None:
        self.assertEqual(tag_run(_ticket(TicketStatus.IN_PROGRESS), []), [])

    def test_escalated_is_tagged_step_repetition(self) -> None:
        # ESCALATED's only inbound FSM transition is retry_cap_exceeded
        # (see fsm.py's TRANSITIONS table), so reaching it at all means
        # the retry loop was exhausted.
        self.assertEqual(tag_run(_ticket(TicketStatus.ESCALATED), []), [MastFailureMode.FM_1_3])

    def test_halted_is_tagged_step_repetition_and_premature_termination(self) -> None:
        # HALTED's only inbound transition is from ESCALATED, so both
        # tags apply: the retry loop was exhausted (FM-1.3) and the run
        # ended without a resolved requirement (FM-3.1).
        tags = tag_run(_ticket(TicketStatus.HALTED), [])
        self.assertEqual(set(tags), {MastFailureMode.FM_1_3, MastFailureMode.FM_3_1})


class TagRunWithJudgeTest(unittest.TestCase):
    def test_judge_tags_are_added_on_top_of_rule_based_ones(self) -> None:
        judge = ScriptedMastJudge([MastFailureMode.FM_2_6])
        tags = tag_run(_ticket(TicketStatus.HALTED), [_message()], judge=judge)
        self.assertEqual(
            set(tags),
            {MastFailureMode.FM_1_3, MastFailureMode.FM_3_1, MastFailureMode.FM_2_6},
        )

    def test_duplicate_judge_tags_are_not_repeated(self) -> None:
        judge = ScriptedMastJudge([MastFailureMode.FM_1_3])
        tags = tag_run(_ticket(TicketStatus.ESCALATED), [_message()], judge=judge)
        self.assertEqual(tags, [MastFailureMode.FM_1_3])

    def test_judge_alone_can_tag_a_done_run(self) -> None:
        # The rule-based half has nothing to say about DONE, but the
        # judge might still catch a subtler issue in an otherwise
        # successful run.
        judge = ScriptedMastJudge([MastFailureMode.FM_2_2])
        tags = tag_run(_ticket(TicketStatus.DONE), [_message()], judge=judge)
        self.assertEqual(tags, [MastFailureMode.FM_2_2])

    def test_no_judge_runs_rule_based_half_only(self) -> None:
        tags = tag_run(_ticket(TicketStatus.HALTED), [_message()])
        self.assertEqual(set(tags), {MastFailureMode.FM_1_3, MastFailureMode.FM_3_1})


if __name__ == "__main__":
    unittest.main()
