from __future__ import annotations

import unittest

from wjfyp.eval.usage import UNATTRIBUTED, summarise_usage
from wjfyp.models.message import AgentRef, Message, MessageContent, MessageType, TokenCost


def msg(role: str, model: str | None, prompt: int, completion: int) -> Message:
    return Message(
        sender=AgentRef(role=role, instance_id=f"{role}-1"),
        channel_id="c1",
        type=MessageType.STATUS_UPDATE,
        content=MessageContent(text="x"),
        token_cost=TokenCost(prompt=prompt, completion=completion, model=model),
    )


def untracked(role: str) -> Message:
    """A tool_call/tool_result style message, which carries no cost."""
    return Message(
        sender=AgentRef(role=role, instance_id=f"{role}-1"),
        channel_id="c1",
        type=MessageType.TOOL_CALL,
        content=MessageContent(text="read_file(...)"),
    )


class BreakdownTest(unittest.TestCase):
    MESSAGES = [
        msg("cto", "claude-opus-5", 1_000, 500),
        msg("engineering", "claude-haiku-4-5", 10_000, 2_000),
        msg("engineering", "claude-haiku-4-5", 5_000, 1_000),
    ]

    def test_totals_add_up(self):
        report = summarise_usage(self.MESSAGES)
        self.assertEqual(report.total.prompt, 16_000)
        self.assertEqual(report.total.completion, 3_500)
        self.assertEqual(report.total.total, 19_500)
        self.assertEqual(report.total.messages, 3)

    def test_sliced_by_role(self):
        report = summarise_usage(self.MESSAGES)
        self.assertEqual(report.by_role["cto"].total, 1_500)
        self.assertEqual(report.by_role["engineering"].total, 18_000)
        self.assertEqual(report.by_role["engineering"].messages, 2)

    def test_cost_uses_each_model_s_own_rate(self):
        report = summarise_usage(self.MESSAGES)
        # Opus 5 at $5/$25 per Mtok: 1000*5 + 500*25 = 17,500 / 1e6
        self.assertAlmostEqual(report.by_role["cto"].cost_usd, 0.0175, places=6)
        # Haiku 4.5 at $1/$5: 15000*1 + 3000*5 = 30,000 / 1e6
        self.assertAlmostEqual(report.by_role["engineering"].cost_usd, 0.0300, places=6)
        self.assertAlmostEqual(report.total.cost_usd, 0.0475, places=6)

    def test_engineering_uses_more_tokens_but_costs_less_than_the_cto(self):
        """The reason a single total is the wrong unit: the cheap tier
        dominates token count while the expensive tier can dominate
        cost. Summing them answers neither question.
        """
        report = summarise_usage(self.MESSAGES)
        self.assertGreater(report.by_role["engineering"].total, report.by_role["cto"].total)
        self.assertGreater(report.by_role["engineering"].cost_usd, report.by_role["cto"].cost_usd)
        # 12x the tokens for under 2x the cost.
        ratio_tokens = report.by_role["engineering"].total / report.by_role["cto"].total
        ratio_cost = report.by_role["engineering"].cost_usd / report.by_role["cto"].cost_usd
        self.assertGreater(ratio_tokens, 10)
        self.assertLess(ratio_cost, 2)

    def test_role_and_model_join_is_kept(self):
        report = summarise_usage(self.MESSAGES)
        self.assertEqual(report.by_role_and_model["cto / claude-opus-5"].total, 1_500)
        self.assertEqual(report.by_role_and_model["engineering / claude-haiku-4-5"].total, 18_000)


class OpenModelTest(unittest.TestCase):
    def test_groq_models_are_priced_not_treated_as_unknown(self):
        report = summarise_usage(
            [
                msg("cto", "openai/gpt-oss-120b", 630, 287),
                msg("engineering", "openai/gpt-oss-20b", 13_533, 716),
            ]
        )
        self.assertEqual(report.unpriced_models, [])
        self.assertIsNotNone(report.total.cost_usd)
        # Measured from the real smoke run; the whole team turn is well
        # under a cent even at list price, and free on the free tier.
        self.assertLess(report.total.cost_usd, 0.01)

    def test_a_model_with_no_price_is_named_rather_than_silently_free(self):
        report = summarise_usage([msg("cto", "some-future-model", 1_000, 1_000)])
        self.assertEqual(report.unpriced_models, ["some-future-model"])
        self.assertIsNone(report.total.cost_usd)
        # The tokens are still counted - only the pricing is unknown.
        self.assertEqual(report.total.total, 2_000)


class LegacyLogTest(unittest.TestCase):
    def test_messages_predating_the_model_field_are_labelled_not_dropped(self):
        report = summarise_usage([msg("cto", None, 1_000, 500)])
        self.assertEqual(report.by_model[UNATTRIBUTED].total, 1_500)
        self.assertEqual(report.unpriced_models, [UNATTRIBUTED])
        self.assertIsNone(report.total.cost_usd)

    def test_mixed_old_and_new_rows_keep_the_priced_part_priced(self):
        report = summarise_usage(
            [msg("cto", "claude-opus-5", 1_000, 500), msg("cto", None, 1_000, 500)]
        )
        self.assertEqual(report.total.total, 3_000)
        self.assertAlmostEqual(report.total.cost_usd, 0.0175, places=6)
        self.assertEqual(report.unpriced_models, [UNATTRIBUTED])


class CostlessMessageTest(unittest.TestCase):
    def test_tool_messages_are_skipped_entirely(self):
        report = summarise_usage(
            [msg("engineering", "claude-haiku-4-5", 100, 10), untracked("engineering"), untracked("engineering")]
        )
        self.assertEqual(report.total.messages, 1)
        self.assertEqual(report.total.total, 110)

    def test_an_empty_log_reports_nothing_rather_than_raising(self):
        report = summarise_usage([])
        self.assertEqual(report.total.total, 0)
        self.assertEqual(report.by_role, {})
        self.assertIsNone(report.total.cost_usd)


class SummaryTest(unittest.TestCase):
    def test_summary_marks_cost_as_an_upper_bound(self):
        summary = summarise_usage(BreakdownTest.MESSAGES).summary()
        self.assertIn("at most", summary)
        self.assertIn("engineering", summary)

    def test_summary_says_unknown_when_nothing_is_priced(self):
        self.assertIn("cost unknown", summarise_usage([msg("cto", None, 1, 1)]).summary())


if __name__ == "__main__":
    unittest.main()
