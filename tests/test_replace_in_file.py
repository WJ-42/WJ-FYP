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
        self.assertEqual(sandbox.read_file("agent.yaml"), YAML)

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
