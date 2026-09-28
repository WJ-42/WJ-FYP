"""Unit tests use a fake Anthropic client (no network, no cost) to check
the request-building and response-parsing logic, same fixture pattern
as test_claude_agent.py. test_real_api_call is the exception - it makes
one real, tiny, billed call to prove the tool schema is actually valid
against the live API, not just a hand-written fixture. It self-skips
when ANTHROPIC_API_KEY isn't set.
"""

from __future__ import annotations

import os
import unittest

from wjfyp.eval.rubric import RUBRIC, LLMRubricJudge, RubricScore, _rubric_tool_schema
from wjfyp.eval.task import EvalTask


class _FakeToolUseBlock:
    type = "tool_use"

    def __init__(self, input: dict, name: str = "submit_rubric_score", id: str = "toolu_1"):
        self.input = input
        self.name = name
        self.id = id


class _FakeResponse:
    def __init__(self, content: list):
        self.content = content


class _FakeMessagesApi:
    def __init__(self, response: _FakeResponse, captured_calls: list[dict]):
        self._response = response
        self._captured_calls = captured_calls

    def create(self, **kwargs):
        self._captured_calls.append(kwargs)
        return self._response


class _FakeClient:
    def __init__(self, axis_args: dict):
        self.captured_calls: list[dict] = []
        response = _FakeResponse(content=[_FakeToolUseBlock(axis_args)])
        self.messages = _FakeMessagesApi(response, self.captured_calls)


def _scripted_axis_args(scores: dict[str, int]) -> dict:
    return {
        axis_id: {"score": score, "justification": f"justification for {axis_id}"}
        for axis_id, score in scores.items()
    }


def _task() -> EvalTask:
    return EvalTask(
        task_id="TCK-1",
        title="Add a health check endpoint",
        prompt="Add a GET /health endpoint that returns 200 OK.",
        checkpoints=[],
    )


class RubricTest(unittest.TestCase):
    def test_rubric_tool_schema_has_one_required_property_per_axis(self) -> None:
        schema = _rubric_tool_schema()

        self.assertEqual(set(schema["input_schema"]["properties"].keys()), {a.id for a in RUBRIC})
        self.assertEqual(set(schema["input_schema"]["required"]), {a.id for a in RUBRIC})
        self.assertTrue(schema["strict"])

    def test_tool_schema_tracks_the_rubric_data_without_code_changes(self) -> None:
        """Proves the data/prompt separation actually holds: adding an
        axis to RUBRIC changes the schema without touching
        _rubric_tool_schema()'s own code.
        """
        from wjfyp.eval.rubric import RubricAxis

        extra = RubricAxis(id="extra_axis", name="Extra", description="a test-only axis")
        RUBRIC.append(extra)
        try:
            schema = _rubric_tool_schema()
            self.assertIn("extra_axis", schema["input_schema"]["properties"])
            self.assertIn("extra_axis", schema["input_schema"]["required"])
        finally:
            RUBRIC.pop()


class LLMRubricJudgeTest(unittest.TestCase):
    def test_score_returns_all_axes_with_the_correct_mean(self) -> None:
        client = _FakeClient(_scripted_axis_args({"correctness": 5, "code_quality": 3, "completeness": 4}))
        judge = LLMRubricJudge(client=client)

        result = judge.score(_task(), "diff --git a/app.py b/app.py\n+def health(): ...")

        self.assertIsInstance(result, RubricScore)
        self.assertEqual(result.task_id, "TCK-1")
        self.assertEqual({a.axis_id for a in result.axis_scores}, {a.id for a in RUBRIC})
        self.assertAlmostEqual(result.overall, (5 + 3 + 4) / 3)

    def test_request_includes_the_task_and_diff(self) -> None:
        client = _FakeClient(_scripted_axis_args({"correctness": 1, "code_quality": 1, "completeness": 1}))
        judge = LLMRubricJudge(client=client)

        judge.score(_task(), "diff --git a/app.py b/app.py\n+def health(): ...")

        call = client.captured_calls[0]
        self.assertEqual(call["tool_choice"], {"type": "tool", "name": "submit_rubric_score"})
        user_turn = call["messages"][0]["content"]
        self.assertIn("Add a health check endpoint", user_turn)
        self.assertIn("Add a GET /health endpoint", user_turn)
        self.assertIn("def health()", user_turn)

    def test_empty_diff_is_still_handled(self) -> None:
        client = _FakeClient(_scripted_axis_args({"correctness": 1, "code_quality": 1, "completeness": 1}))
        judge = LLMRubricJudge(client=client)

        judge.score(_task(), "")

        user_turn = client.captured_calls[0]["messages"][0]["content"]
        self.assertIn("no changes were committed", user_turn)

    def test_default_model_is_sonnet(self) -> None:
        judge = LLMRubricJudge(client=_FakeClient(_scripted_axis_args({"correctness": 1, "code_quality": 1, "completeness": 1})))
        self.assertEqual(judge._model, "claude-sonnet-5")


class ScriptedRubricJudge:
    """Test double for callers (e.g. Option B's comparison runner tests)
    that need a RubricJudge without any Anthropic client at all - mirrors
    test_mast.py's ScriptedMastJudge.
    """

    def __init__(self, scores: dict[str, RubricScore] | None = None, default: RubricScore | None = None):
        self._scores = scores or {}
        self._default = default

    def score(self, task: EvalTask, diff_text: str) -> RubricScore:
        if task.task_id in self._scores:
            return self._scores[task.task_id]
        if self._default is not None:
            return self._default
        raise AssertionError(f"ScriptedRubricJudge has no scripted score for {task.task_id!r}")


@unittest.skipUnless(
    os.environ.get("ANTHROPIC_API_KEY"), "ANTHROPIC_API_KEY not set - skipping real API call"
)
class RealApiIntegrationTest(unittest.TestCase):
    def test_real_api_call(self) -> None:
        judge = LLMRubricJudge()

        result = judge.score(
            _task(),
            "diff --git a/app.py b/app.py\n"
            "new file mode 100644\n"
            "+++ b/app.py\n"
            "+from flask import Flask\n"
            "+app = Flask(__name__)\n"
            "+\n"
            "+@app.route('/health')\n"
            "+def health():\n"
            "+    return 'OK', 200\n",
        )

        self.assertEqual(len(result.axis_scores), len(RUBRIC))
        for axis_score in result.axis_scores:
            self.assertTrue(axis_score.justification)


if __name__ == "__main__":
    unittest.main()
