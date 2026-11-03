from __future__ import annotations

import unittest
from pathlib import Path

from wjfyp.config import load_roles
from wjfyp.orchestrator.openai_agent import build_agent_pool

CONFIG = Path("config")
HIERARCHICAL = CONFIG / "roles_groq.yaml"
NEUTRAL = CONFIG / "roles_groq_neutral.yaml"


def models_in(pool) -> dict[str, set[str]]:
    return {rid: {a._role_config.model for a in agents} for rid, agents in pool.items()}


def personas_in(pool) -> dict[str, str | None]:
    return {rid: agents[0]._role_config.personality for rid, agents in pool.items()}


class ModelOverrideTest(unittest.TestCase):
    """The override is what lets one set of arm configs run on any
    backend. Six arms times two backends would otherwise be twelve
    near-identical files whose persona text has to stay byte-identical
    in pairs, and the only reliable way to keep two copies identical is
    to have one copy.
    """

    def test_without_an_override_the_file_decides(self):
        pool = build_agent_pool(HIERARCHICAL, api_key="x", base_url="http://localhost:1/v1")
        self.assertEqual(models_in(pool)["cto"], {"openai/gpt-oss-120b"})
        self.assertEqual(models_in(pool)["engineering"], {"openai/gpt-oss-20b"})

    def test_an_override_replaces_every_role_s_model(self):
        pool = build_agent_pool(
            HIERARCHICAL, api_key="x", base_url="http://localhost:1/v1", model="qwen3:8b"
        )
        for rid, models in models_in(pool).items():
            with self.subTest(role=rid):
                self.assertEqual(models, {"qwen3:8b"})

    def test_it_changes_nothing_else_about_the_arm(self):
        """The whole point. If an override touched personas, tiers or
        counts, running an arm on a second backend would silently be
        running a different arm, and the comparison would be void.
        """
        plain = build_agent_pool(HIERARCHICAL, api_key="x", base_url="http://localhost:1/v1")
        overridden = build_agent_pool(
            HIERARCHICAL, api_key="x", base_url="http://localhost:1/v1", model="qwen3:8b"
        )

        self.assertEqual(personas_in(plain), personas_in(overridden))
        self.assertEqual(
            {rid: len(a) for rid, a in plain.items()},
            {rid: len(a) for rid, a in overridden.items()},
        )
        self.assertEqual(
            {rid: a[0]._role_config.tier for rid, a in plain.items()},
            {rid: a[0]._role_config.tier for rid, a in overridden.items()},
        )

    def test_the_control_arm_stays_persona_free_under_an_override(self):
        pool = build_agent_pool(
            NEUTRAL, api_key="x", base_url="http://localhost:1/v1", model="qwen3:8b"
        )
        for rid, persona in personas_in(pool).items():
            with self.subTest(role=rid):
                self.assertIsNone(persona)

    def test_the_file_on_disk_is_not_mutated(self):
        """model_copy rather than assignment. A roles file that changed
        under a run would corrupt every later arm in the same session.
        """
        before = {r.id: r.model for r in load_roles(HIERARCHICAL)}
        build_agent_pool(HIERARCHICAL, api_key="x", base_url="http://localhost:1/v1", model="qwen3:8b")
        after = {r.id: r.model for r in load_roles(HIERARCHICAL)}
        self.assertEqual(before, after)
        self.assertEqual(after["cto"], "openai/gpt-oss-120b")


if __name__ == "__main__":
    unittest.main()
