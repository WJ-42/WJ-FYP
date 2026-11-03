"""Guards on the team config files themselves.

These arms only mean anything if the thing each one claims to vary is
the ONLY thing it varies. That property lives in YAML, not in code, so
nothing else in the suite would notice it breaking: a stray edit to one
persona string, or a model changed in one arm and not another, would
leave every test green while quietly voiding the comparison the whole
project rests on. Hence these.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from wjfyp.config import load_roles

CONFIG = Path("config")

CLAUDE_HIERARCHICAL = CONFIG / "roles.yaml"
CLAUDE_FLAT = CONFIG / "roles_flat.yaml"
GROQ_HIERARCHICAL = CONFIG / "roles_groq.yaml"
GROQ_FLAT = CONFIG / "roles_groq_flat.yaml"
GROQ_NEUTRAL = CONFIG / "roles_groq_neutral.yaml"
GROQ_CTO_ONLY = CONFIG / "roles_groq_cto_only.yaml"

# The arms that hold capability fixed and vary persona alone. A
# difference in models between any two of these would confound the
# persona effect they exist to isolate.
PERSONA_ONLY_ARMS = [GROQ_NEUTRAL, GROQ_CTO_ONLY, GROQ_HIERARCHICAL]


def by_id(path: Path) -> dict[str, object]:
    return {r.id: r for r in load_roles(path)}


class EveryArmLoadsTest(unittest.TestCase):
    def test_all_six_configs_parse(self):
        for path in [
            CLAUDE_HIERARCHICAL,
            CLAUDE_FLAT,
            GROQ_HIERARCHICAL,
            GROQ_FLAT,
            GROQ_NEUTRAL,
            GROQ_CTO_ONLY,
        ]:
            with self.subTest(config=path.name):
                roles = load_roles(path)
                self.assertEqual({r.id for r in roles}, {"cto", "product", "engineering"})

    def test_role_counts_match_across_every_arm(self):
        """A team of a different size is a different team. Counts must
        not drift between arms or the comparison is not like for like.
        """
        expected = {r.id: r.count for r in load_roles(CLAUDE_HIERARCHICAL)}
        for path in [CLAUDE_FLAT, GROQ_HIERARCHICAL, GROQ_FLAT, GROQ_NEUTRAL, GROQ_CTO_ONLY]:
            with self.subTest(config=path.name):
                self.assertEqual({r.id: r.count for r in load_roles(path)}, expected)


class PersonaOnlyArmsTest(unittest.TestCase):
    """The three arms that vary persona and nothing else."""

    def test_models_are_identical_across_them(self):
        baseline = {rid: r.model for rid, r in by_id(GROQ_NEUTRAL).items()}
        for path in PERSONA_ONLY_ARMS:
            with self.subTest(config=path.name):
                self.assertEqual({rid: r.model for rid, r in by_id(path).items()}, baseline)

    def test_tiers_are_identical_across_them(self):
        baseline = {rid: r.tier for rid, r in by_id(GROQ_NEUTRAL).items()}
        for path in PERSONA_ONLY_ARMS:
            with self.subTest(config=path.name):
                self.assertEqual({rid: r.tier for rid, r in by_id(path).items()}, baseline)

    def test_the_control_carries_no_persona_at_all(self):
        for rid, role in by_id(GROQ_NEUTRAL).items():
            with self.subTest(role=rid):
                self.assertIsNone(role.personality)

    def test_the_cto_only_arm_varies_exactly_one_role(self):
        roles = by_id(GROQ_CTO_ONLY)
        self.assertIsNotNone(roles["cto"].personality)
        self.assertIsNone(roles["product"].personality)
        self.assertIsNone(roles["engineering"].personality)

    def test_the_cto_persona_is_the_same_text_as_the_full_hierarchy_arm(self):
        """If this drifts, the CTO-only arm stops testing the same
        persona as the arm it is being compared against, and the
        decomposition is meaningless.
        """
        self.assertEqual(
            by_id(GROQ_CTO_ONLY)["cto"].personality,
            by_id(GROQ_HIERARCHICAL)["cto"].personality,
        )


class CrossBackendTest(unittest.TestCase):
    """Claude and Groq arms must carry byte-identical persona text.

    The same reason openai_agent imports its prompt builders from
    claude_agent instead of copying them: a backend comparison is only
    valid if the prompts are the same on both sides.
    """

    def test_hierarchical_personas_match_across_backends(self):
        claude = {rid: r.personality for rid, r in by_id(CLAUDE_HIERARCHICAL).items()}
        groq = {rid: r.personality for rid, r in by_id(GROQ_HIERARCHICAL).items()}
        self.assertEqual(claude, groq)

    def test_flat_personas_match_across_backends(self):
        claude = {rid: r.personality for rid, r in by_id(CLAUDE_FLAT).items()}
        groq = {rid: r.personality for rid, r in by_id(GROQ_FLAT).items()}
        self.assertEqual(claude, groq)

    def test_hierarchical_and_flat_really_do_differ_in_persona(self):
        """A sanity check in the opposite direction: if someone made the
        two arms identical, every test above would still pass while the
        experiment measured nothing.
        """
        self.assertNotEqual(
            by_id(GROQ_HIERARCHICAL)["cto"].personality,
            by_id(GROQ_FLAT)["cto"].personality,
        )


class FlatArmConfoundTest(unittest.TestCase):
    """Documents a known limitation rather than asserting it is fine.

    roles_flat.yaml drops the CTO's model as well as reframing every
    persona, so the flat arm differs from the hierarchical one in TWO
    ways. That is inherited from the original Claude pair and is not new,
    but it means flat-vs-hierarchical cannot attribute a difference to
    organisational structure alone. The persona-only arms above are what
    answer that question. This test exists so the confound is visible in
    the suite instead of only in a comment.
    """

    def test_the_flat_arm_changes_capability_as_well_as_persona(self):
        self.assertNotEqual(
            by_id(GROQ_FLAT)["cto"].model,
            by_id(GROQ_HIERARCHICAL)["cto"].model,
        )
        self.assertNotEqual(
            by_id(GROQ_FLAT)["cto"].tier,
            by_id(GROQ_HIERARCHICAL)["cto"].tier,
        )


if __name__ == "__main__":
    unittest.main()
