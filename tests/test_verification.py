from __future__ import annotations

import unittest

from wjfyp.orchestrator.verification import (
    COMMIT_BACKED_TRIGGERS,
    UNVERIFIED_PREFIX,
    accept_under_protest,
    verify_declaration,
)


class VerifyDeclarationTest(unittest.TestCase):
    def test_a_committed_submission_passes(self):
        self.assertTrue(verify_declaration("code_submission", has_committed=True))

    def test_an_uncommitted_submission_is_rejected_with_an_actionable_reason(self):
        verdict = verify_declaration("code_submission", has_committed=False)
        self.assertFalse(verdict)
        # The wording goes to a model, so it must say what to do next,
        # not merely what was wrong.
        self.assertIn("git_commit", verdict.rejection)

    def test_triggers_that_claim_nothing_about_code_are_unaffected(self):
        """Only a trigger asserting that work was committed needs a
        commit. Gating the rest would block legitimate transitions, such
        as an engineer escalating precisely because they could not get
        the work done.
        """
        for trigger in ["approved", "changes_requested", "escalated", "spec_ready", "decomposed"]:
            with self.subTest(trigger=trigger):
                self.assertTrue(verify_declaration(trigger, has_committed=False))

    def test_an_unknown_trigger_is_not_gated(self):
        self.assertTrue(verify_declaration("some_future_trigger", has_committed=False))

    def test_the_gated_set_is_explicit(self):
        self.assertIn("code_submission", COMMIT_BACKED_TRIGGERS)

    def test_the_verdict_is_usable_as_a_plain_boolean(self):
        """Both agents branch on it directly, so truthiness has to mean
        what it looks like it means.
        """
        self.assertTrue(bool(verify_declaration("approved", has_committed=False)))
        self.assertFalse(bool(verify_declaration("code_submission", has_committed=False)))


class AcceptUnderProtestTest(unittest.TestCase):
    def test_it_flags_the_narrative_visibly(self):
        flagged = accept_under_protest("Added header validation and a test.")
        self.assertTrue(flagged.startswith(UNVERIFIED_PREFIX))
        self.assertIn("Added header validation", flagged)

    def test_flagging_twice_does_not_stack_prefixes(self):
        once = accept_under_protest("Did the thing.")
        self.assertEqual(accept_under_protest(once), once)


class RealFailuresTest(unittest.TestCase):
    """The two real runs this check exists because of, as regressions.

    Both agents produced confident, specific narratives describing work
    they had not done. Neither is hypothetical and neither was caught by
    prompt wording.
    """

    def test_the_haiku_case_a_submission_with_no_commit_at_all(self):
        self.assertFalse(verify_declaration("code_submission", has_committed=False))

    def test_the_gpt_oss_case_a_detailed_false_narrative_is_still_flagged(self):
        narrative = "Added header validation and test to reject malformed expiry values."
        verdict = verify_declaration("code_submission", has_committed=False)
        self.assertFalse(verdict)
        # Plausibility of the account is irrelevant; only the commit is.
        self.assertIn(UNVERIFIED_PREFIX, accept_under_protest(narrative))


if __name__ == "__main__":
    unittest.main()
