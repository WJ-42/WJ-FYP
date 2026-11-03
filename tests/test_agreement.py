from __future__ import annotations

import unittest

from wjfyp.eval.agreement import measure_agreement


def _from_counts(rows: list[list[int]], labels: str = "abcde") -> dict[str, list[str]]:
    """Expands a subjects-by-categories count matrix into the per-item
    outcome lists measure_agreement takes, so published worked examples
    can be used as-is.
    """
    return {
        f"item-{i}": [labels[j] for j, n in enumerate(row) for _ in range(n)]
        for i, row in enumerate(rows)
    }


class FleissKappaAgainstPublishedExampleTest(unittest.TestCase):
    """The standard Fleiss worked example: 10 subjects, 14 raters, 5
    categories, with published intermediate values. Checking against
    someone else's arithmetic rather than re-deriving it here is the
    point - this is the one test that would catch a plausible-looking
    but wrong kappa.
    """

    MATRIX = [
        [0, 0, 0, 0, 14],
        [0, 2, 6, 4, 2],
        [0, 0, 3, 5, 6],
        [0, 3, 9, 2, 0],
        [2, 2, 8, 1, 1],
        [7, 7, 0, 0, 0],
        [3, 2, 6, 3, 0],
        [2, 5, 3, 2, 2],
        [6, 5, 2, 1, 0],
        [0, 2, 2, 3, 7],
    ]

    def test_kappa_matches_published_value(self):
        report = measure_agreement(_from_counts(self.MATRIX))
        self.assertAlmostEqual(report.kappa, 0.2099, places=3)

    def test_marginals_match_published_values(self):
        report = measure_agreement(_from_counts(self.MATRIX))
        # Column totals 20, 28, 39, 21, 32 over 140 ratings.
        self.assertAlmostEqual(report.outcome_distribution["a"], 20 / 140, places=6)
        self.assertAlmostEqual(report.outcome_distribution["b"], 28 / 140, places=6)
        self.assertAlmostEqual(report.outcome_distribution["c"], 39 / 140, places=6)
        self.assertAlmostEqual(report.outcome_distribution["d"], 21 / 140, places=6)
        self.assertAlmostEqual(report.outcome_distribution["e"], 32 / 140, places=6)
        self.assertAlmostEqual(sum(report.outcome_distribution.values()), 1.0, places=9)


class ChanceCorrectionTest(unittest.TestCase):
    """The failure mode the module exists for: agreement that is high
    because the outcome distribution is lopsided, not because the system
    is reliable. This is the shape of the MDT paper's autocratic and
    avoidant arms.
    """

    # 20 items, 3 runs each. 17 unanimous "approved", 3 split 2-1. This
    # reproduces the MDT paper's reported shape: high full agreement,
    # a chance level that already accounts for it, and a kappa at zero.
    LOPSIDED = {
        **{f"t{i}": ["approved"] * 3 for i in range(17)},
        **{f"s{i}": ["approved", "approved", "changes_requested"] for i in range(3)},
    }
    # Same number of items and runs, but outcomes split evenly, so the
    # same observed agreement means far more.
    BALANCED = {
        **{f"t{i}": ["approved"] * 3 for i in range(10)},
        **{f"u{i}": ["changes_requested"] * 3 for i in range(10)},
    }

    def test_lopsided_condition_agrees_often_but_not_beyond_chance(self):
        report = measure_agreement(self.LOPSIDED)

        self.assertAlmostEqual(report.full_agreement, 0.85)
        # Chance alone already explains essentially all of it.
        self.assertAlmostEqual(report.chance_full_agreement, 0.857, places=3)
        self.assertLess(report.excess_agreement, 0.0)
        # And the chance-corrected figure says so plainly.
        self.assertLess(report.kappa, 0.05)

    def test_balanced_condition_scores_far_higher_on_similar_agreement(self):
        report = measure_agreement(self.BALANCED)

        self.assertEqual(report.full_agreement, 1.0)
        self.assertAlmostEqual(report.kappa, 1.0, places=6)
        self.assertAlmostEqual(report.chance_full_agreement, 0.25, places=6)

    def test_raw_agreement_alone_badly_misranks_the_two(self):
        """The whole reason the report refuses to expose raw agreement
        on its own: on that number the two conditions look close, while
        chance correction shows one is informative and the other is not.
        """
        lopsided = measure_agreement(self.LOPSIDED)
        balanced = measure_agreement(self.BALANCED)

        # Raw agreement puts them within 15 points of each other.
        self.assertLess(balanced.full_agreement - lopsided.full_agreement, 0.16)
        # Chance correction separates them completely.
        self.assertGreater(balanced.kappa - lopsided.kappa, 0.9)
        self.assertGreater(balanced.excess_agreement - lopsided.excess_agreement, 0.7)

    def test_kappa_saturates_when_every_item_is_unanimous(self):
        """A property worth pinning down rather than discovering later.

        Fleiss kappa is 1.0 whenever every run of every item agrees, no
        matter how lopsided the outcomes are, because observed pairwise
        agreement is then 1.0 and the ratio collapses. In that regime it
        is excess_agreement, not kappa, that exposes a condition which
        simply always says the same thing. Neither statistic is
        sufficient alone, which is why the report carries both.
        """
        nearly_always_approved = {
            **{f"t{i}": ["approved"] * 3 for i in range(19)},
            "t19": ["changes_requested"] * 3,
        }
        report = measure_agreement(nearly_always_approved)

        self.assertAlmostEqual(report.kappa, 1.0, places=6)
        self.assertGreater(report.chance_full_agreement, 0.85)
        self.assertLess(report.excess_agreement, 0.15)


class DegenerateInputTest(unittest.TestCase):
    def test_single_outcome_everywhere_leaves_kappa_undefined_not_perfect(self):
        report = measure_agreement({f"t{i}": ["approved"] * 3 for i in range(5)})

        self.assertIsNone(report.kappa)
        self.assertIsNotNone(report.kappa_undefined_reason)
        self.assertIn("says nothing about reliability", report.kappa_undefined_reason)
        # Still honest about what it did observe.
        self.assertEqual(report.full_agreement, 1.0)
        self.assertAlmostEqual(report.chance_full_agreement, 1.0)

    def test_total_disagreement_gives_negative_kappa(self):
        report = measure_agreement({f"t{i}": ["a", "b", "c"] for i in range(6)})
        self.assertLess(report.kappa, 0.0)
        self.assertEqual(report.full_agreement, 0.0)

    def test_unequal_run_counts_are_accepted(self):
        report = measure_agreement({"t0": ["a", "a"], "t1": ["a", "b", "b", "b"]})
        self.assertEqual(report.runs_per_item, {2: 1, 4: 1})
        self.assertIsNotNone(report.kappa)

    def test_an_item_with_one_run_is_rejected(self):
        with self.assertRaises(ValueError) as cm:
            measure_agreement({"t0": ["a", "a"], "t1": ["a"]})
        self.assertIn("fewer than 2 runs", str(cm.exception))

    def test_no_items_is_rejected(self):
        with self.assertRaises(ValueError):
            measure_agreement({})


class SummaryTest(unittest.TestCase):
    def test_summary_names_chance_alongside_the_raw_figure(self):
        runs = {**{f"t{i}": ["approved"] * 3 for i in range(19)}, "t19": ["changes_requested"] * 3}
        summary = measure_agreement(runs).summary()
        self.assertIn("full agreement", summary)
        self.assertIn("chance", summary)
        self.assertIn("kappa", summary)

    def test_summary_says_undefined_rather_than_printing_a_number(self):
        summary = measure_agreement({f"t{i}": ["approved"] * 3 for i in range(5)}).summary()
        self.assertIn("kappa undefined", summary)


if __name__ == "__main__":
    unittest.main()
