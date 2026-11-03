"""An edit tool that does not ask the model to retype what it is not
changing.

From a real run on 03/11/2026: asked to correct two answers in a
40-line YAML file, both team structures produced the two CORRECT values
and re-indented the surrounding block while rewriting the file whole,
leaving YAML that no longer parsed. The checkpoints read that file to
verify the answers, so a correct solution scored 1 of 3.

That is a transcription limit rather than a reasoning limit. write_file
makes every edit a bet that the model can reproduce every untouched
line exactly; this tool removes the bet.
"""

from __future__ import annotations

import unittest

from wjfyp.orchestrator.claude_agent import _SANDBOX_TOOL_SCHEMAS, _execute_sandbox_tool
from wjfyp.sandbox.fake import FakeSandboxController

YAML = '''examples:
  - inputs:
      task: "What day of the week is 2099-01-01?"
    outputs:
      answer: "Saturday"
  - inputs:
      task: "What is the integral of sin(x^2) evaluated from -1 to 1?"
    outputs:
      answer: "0.603848"
'''


def sandbox_with(content: str = YAML) -> FakeSandboxController:
    sandbox = FakeSandboxController()
    sandbox.write_file("agent.yaml", content)
    return sandbox


def call(sandbox, **args) -> tuple[str, bool]:
    return _execute_sandbox_tool(sandbox, "replace_in_file", args)


class ReplaceInFileTest(unittest.TestCase):
    def test_it_changes_only_the_snippet(self):
        sandbox = sandbox_with()
        text, is_error = call(
            sandbox, path="agent.yaml", old_text='answer: "Saturday"', new_text='answer: "Thursday"'
        )
        self.assertFalse(is_error)
        after = sandbox.read_file("agent.yaml")
        self.assertIn('answer: "Thursday"', after)
        # The real failure being prevented: everything else byte-identical.
        self.assertEqual(
            after.replace('answer: "Thursday"', 'answer: "Saturday"'), YAML
        )

    def test_the_file_still_parses_after_both_real_fixes(self):
        """The exact edit the failed run was asked to make."""
        import yaml

        sandbox = sandbox_with()
        call(sandbox, path="agent.yaml", old_text='answer: "Saturday"', new_text='answer: "Thursday"')
        call(sandbox, path="agent.yaml", old_text='answer: "0.603848"', new_text='answer: "0.620537"')

        parsed = yaml.safe_load(sandbox.read_file("agent.yaml"))
        answers = [e["outputs"]["answer"] for e in parsed["examples"]]
        self.assertEqual(answers, ["Thursday", "0.620537"])

    def test_a_snippet_that_is_not_unique_is_refused(self):
        """Replacing the first of several would silently edit somewhere
        the model never looked, which is the corruption this exists to
        prevent."""
        sandbox = sandbox_with()
        text, is_error = call(
            sandbox, path="agent.yaml", old_text="    outputs:", new_text="    results:"
        )
        self.assertTrue(is_error)
        self.assertIn("2 matches", text)
        self.assertIn("not unique", text)
        self.assertEqual(sandbox.read_file("agent.yaml"), YAML)

    def test_a_snippet_that_does_not_match_is_refused_with_the_likely_reason(self):
        """Whitespace is the usual culprit and the model cannot see it,
        so the error has to name it."""
        sandbox = sandbox_with()
        text, is_error = call(
            sandbox, path="agent.yaml", old_text='answer:"Saturday"', new_text="x"
        )
        self.assertTrue(is_error)
        self.assertIn("no match", text)
        self.assertIn("indentation", text)
        self.assertIn("already been made", text)
        self.assertEqual(sandbox.read_file("agent.yaml"), YAML)

    def test_an_already_applied_change_is_named_as_the_likely_cause(self):
        """The loop this prevents. A review-cycle agent on 03/11/2026
        spent most of a turn alternating read_file and a replace of
        'answer: "Saturday"' that could not match, because an earlier
        agent had already changed it to "Thursday". It read the correct
        file every time. An error that only mentions whitespace invites
        exactly that, since re-reading never reveals a whitespace
        problem that is not there.
        """
        sandbox = sandbox_with()
        call(sandbox, path="agent.yaml", old_text='answer: "Saturday"', new_text='answer: "Thursday"')

        text, is_error = call(
            sandbox, path="agent.yaml", old_text='answer: "Saturday"', new_text='answer: "Thursday"'
        )
        self.assertTrue(is_error)
        self.assertIn("ALREADY present", text)
        self.assertIn("already been made", text)

    def test_a_genuine_whitespace_miss_does_not_claim_it_is_done(self):
        """The hint must not fire when the change really has not been
        made, or it would send the agent away from work it still owes."""
        sandbox = sandbox_with()
        text, is_error = call(
            sandbox, path="agent.yaml", old_text='answer:"Saturday"', new_text='answer: "Friday"'
        )
        self.assertTrue(is_error)
        self.assertNotIn("ALREADY present", text)
        self.assertIn("whitespace", text)

    def test_a_missing_file_is_an_error_not_a_crash(self):
        text, is_error = call(sandbox_with(), path="nope.yaml", old_text="a", new_text="b")
        self.assertTrue(is_error)

    def test_it_is_offered_to_agents(self):
        names = {schema["name"] for schema in _SANDBOX_TOOL_SCHEMAS}
        self.assertIn("replace_in_file", names)

    def test_write_file_now_steers_toward_it(self):
        """A tool the model is never told to prefer will not get used,
        and write_file is the habit it has to displace."""
        write = next(s for s in _SANDBOX_TOOL_SCHEMAS if s["name"] == "write_file")
        self.assertIn("replace_in_file", write["description"])


if __name__ == "__main__":
    unittest.main()
