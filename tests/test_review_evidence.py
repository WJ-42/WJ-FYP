"""Option A, "make review real" (19/11/2026). Product writes acceptance
criteria at Backlog, the engineer sees them at In Progress, and the CTO
at Review sees them next to the stat and a truncated diff of the ticket
branch. Covers the pure renderer, the git side, the loop wiring, and
that both backends put the same evidence in front of the model.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

from wjfyp.config import Settings
from wjfyp.eventlog import EventLog
from wjfyp.models.agent import RoleConfig
from wjfyp.models.channel import Channel
from wjfyp.models.message import AgentRef, HandoffNote, Message, MessageContent, MessageType
from wjfyp.models.ticket import Ticket, TicketStatus
from wjfyp.orchestrator.agent import AgentContext, AgentResponse
from wjfyp.orchestrator.claude_agent import ClaudeAgent, _criteria_from_args, _user_turn
from wjfyp.orchestrator.loop import run
from wjfyp.orchestrator.openai_agent import OpenAIAgent
from wjfyp.orchestrator.review_evidence import (
    build_review_evidence,
    omission_reason,
    render_review_evidence,
    split_diff,
)
from wjfyp.sandbox.controller import TestResult
from wjfyp.sandbox.fake import FakeSandboxController
from wjfyp.sandbox.git_workspace import GitWorkspace


def _file_diff(path: str, body_lines: int, line: str = "+added line of code") -> str:
    body = "\n".join(f"{line} {i}" for i in range(body_lines))
    return (
        f"diff --git a/{path} b/{path}\n"
        "index 0000000..1111111 100644\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        f"@@ -0,0 +1,{body_lines} @@\n"
        f"{body}\n"
    )


class RenderReviewEvidenceTest(unittest.TestCase):
    def test_a_small_diff_is_shown_whole_with_its_stat(self) -> None:
        diff = _file_diff("src/app.py", 3)
        stat = " src/app.py | 3 +++\n 1 file changed, 3 insertions(+)"

        text = render_review_evidence(stat, diff, budget_chars=10_000)

        self.assertIn("src/app.py | 3 +++", text)
        self.assertIn("+added line of code 2", text)
        self.assertIn("End of diff", text)
        self.assertNotIn("TRUNCATED", text)

    def test_an_empty_diff_says_there_is_nothing_to_review(self) -> None:
        text = render_review_evidence("", "")
        self.assertIn("changes nothing", text)

    def test_a_diff_over_budget_is_cut_and_says_so(self) -> None:
        diff = _file_diff("a.py", 50) + _file_diff("b.py", 50) + _file_diff("c.py", 50)
        budget = len(_file_diff("a.py", 50)) + 600

        text = render_review_evidence("stat", diff, budget_chars=budget)

        self.assertIn("[DIFF TRUNCATED HERE, partway through b.py]", text)
        self.assertIn("THE DIFF ABOVE WAS TRUNCATED", text)
        self.assertIn("not shown at all, c.py", text)
        self.assertNotIn("diff --git a/c.py", text)
        self.assertNotIn("End of diff", text)
        diff_part = text.split("Unified diff:\n", 1)[1].split("THE DIFF ABOVE", 1)[0]
        # The budget bounds the diff body itself, plus the one cut marker.
        self.assertLessEqual(len(diff_part), budget + 100)

    def test_a_cut_at_a_file_boundary_is_marked_too(self) -> None:
        diff = _file_diff("a.py", 50) + _file_diff("b.py", 50)
        budget = len(_file_diff("a.py", 50)) + 50

        text = render_review_evidence("stat", diff, budget_chars=budget)

        self.assertIn("[DIFF TRUNCATED HERE, before b.py]", text)
        self.assertIn("not shown at all, b.py", text)
        self.assertNotIn("diff --git a/b.py", text)

    def test_lock_files_are_listed_but_never_shown(self) -> None:
        diff = _file_diff("pyproject.toml", 4) + _file_diff("uv.lock", 30, line="+lockline")
        stat = " pyproject.toml | 4 ++++\n uv.lock | 30 +++\n 2 files changed"

        text = render_review_evidence(stat, diff, budget_chars=100_000)

        self.assertIn("uv.lock | 30", text)  # still in the stat
        self.assertIn("- uv.lock (lock file)", text)
        self.assertNotIn("+lockline", text)
        self.assertIn("+added line of code 3", text)  # the real change is shown

    def test_a_single_file_bigger_than_the_whole_budget_is_left_out(self) -> None:
        diff = _file_diff("small.py", 2) + _file_diff("generated_schema.json", 400)

        text = render_review_evidence("stat", diff, budget_chars=2_000)

        self.assertIn("generated_schema.json (diff too large to show", text)
        self.assertIn("+added line of code 1", text)
        self.assertNotIn("TRUNCATED", text)

    def test_omission_reasons(self) -> None:
        self.assertEqual(omission_reason("uv.lock"), "lock file")
        self.assertEqual(omission_reason("frontend/package-lock.json"), "lock file")
        self.assertEqual(omission_reason("poetry.lock"), "lock file")
        self.assertEqual(omission_reason("static/app.min.js"), "generated file")
        self.assertIsNone(omission_reason("pyproject.toml"))
        self.assertIsNone(omission_reason(".github/workflows/mypy.yml"))

    def test_split_diff_keys_each_chunk_by_its_path(self) -> None:
        diff = _file_diff("a.py", 1) + _file_diff("docs/b.md", 1)
        self.assertEqual([f.path for f in split_diff(diff)], ["a.py", "docs/b.md"])


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _repo_with_branch(tmp: Path, branch: str) -> GitWorkspace:
    _git(tmp, "init", "-b", "main")
    _git(tmp, "config", "user.email", "test@example.com")
    _git(tmp, "config", "user.name", "Test")
    (tmp / "README.md").write_text("hello\n")
    _git(tmp, "add", ".")
    _git(tmp, "commit", "-m", "initial")
    _git(tmp, "checkout", "-b", branch)
    (tmp / "feature.txt").write_text("real work\n")
    (tmp / "uv.lock").write_text("".join(f"lock entry {i}\n" for i in range(200)))
    _git(tmp, "add", ".")
    _git(tmp, "commit", "-m", "the ticket's work")
    _git(tmp, "checkout", "main")
    return GitWorkspace(tmp, default_base="main")


class GitWorkspaceEvidenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name)
        self.workspace = _repo_with_branch(self.repo, "ticket/T-1")

    def test_stat_lists_every_file_before_merge(self) -> None:
        stat = self.workspace.diff_stat_against_base("ticket/T-1")
        self.assertIn("feature.txt", stat)
        self.assertIn("uv.lock", stat)

    def test_stat_still_reads_after_merge(self) -> None:
        self.workspace.merge(Ticket(id="T-1", title="t", description="d", branch_name="ticket/T-1"))
        self.assertIn("feature.txt", self.workspace.diff_stat_against_base("ticket/T-1"))

    def test_stat_is_empty_for_a_missing_branch(self) -> None:
        self.assertEqual(self.workspace.diff_stat_against_base("ticket/none"), "")

    def test_built_evidence_shows_the_change_and_hides_the_lock(self) -> None:
        text = build_review_evidence(self.workspace, "ticket/T-1")
        self.assertIn("+real work", text)
        self.assertIn("uv.lock", text)
        self.assertIn("- uv.lock (lock file)", text)
        self.assertNotIn("lock entry 5", text)


class CriteriaParsingTest(unittest.TestCase):
    def test_list_is_cleaned_and_capped(self) -> None:
        raw = [" - one ", "", "two", "two", *[f"c{i}" for i in range(10)]]
        cleaned = _criteria_from_args({"acceptance_criteria": raw})
        self.assertEqual(cleaned[:2], ["one", "two"])
        self.assertEqual(len(cleaned), 8)

    def test_a_single_string_is_split_into_lines(self) -> None:
        cleaned = _criteria_from_args({"acceptance_criteria": "- a\n- b\n"})
        self.assertEqual(cleaned, ["a", "b"])

    def test_missing_gives_an_empty_list(self) -> None:
        self.assertEqual(_criteria_from_args({}), [])


class _RecordingAgent:
    """Scripted like test_orchestrator_loop's ScriptedAgent, but each
    script entry may also carry acceptance criteria to return."""

    def __init__(self, role: str, script: list[tuple[str, list[str] | None]]):
        self.role = role
        self.instance_id = f"{role}-1"
        self._script = list(script)
        self.contexts: list[AgentContext] = []

    def invoke(self, context: AgentContext) -> AgentResponse:
        self.contexts.append(context)
        trigger, criteria = self._script.pop(0)
        message = Message(
            sender=AgentRef(role=self.role, instance_id=self.instance_id),
            channel_id=context.channel_id,
            type=MessageType.STATUS_UPDATE,
            ticket_ref=context.ticket.id,
            content=MessageContent(
                text=f"{self.role} fires {trigger}",
                handoff=HandoffNote(done="d", remaining="r", notes_for_next="n"),
            ),
        )
        return AgentResponse(message=message, trigger=trigger, acceptance_criteria=criteria)


def _passing_sandbox(_ticket: Ticket) -> FakeSandboxController:
    return FakeSandboxController(
        test_results=[
            TestResult(
                passed=True,
                fail_to_pass_total=0,
                fail_to_pass_passed=0,
                pass_to_pass_total=0,
                pass_to_pass_passed=0,
                output="",
            )
        ]
    )


class LoopWiringTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.event_log = EventLog(Path(self._tmp.name) / "eventlog.db")
        self.addCleanup(self.event_log.close)
        repo = Path(self._tmp.name) / "repo"
        repo.mkdir()
        self.workspace = _repo_with_branch(repo, "ticket/T-1")
        self.ticket = Ticket(
            id="T-1", title="Add the feature", description="Write feature.txt.",
            branch_name="ticket/T-1",
        )
        self.channel = Channel(id="ch", key="ticket:T-1", ticket_ref="T-1")

    def _run(self, product_criteria: list[str] | None):
        cto = _RecordingAgent("cto", [("decomposed", None), ("approved", None)])
        product = _RecordingAgent("product", [("spec_ready", product_criteria)])
        engineering = _RecordingAgent("engineering", [("assigned", None), ("code_submission", None)])
        agents = {"cto": [cto], "product": [product], "engineering": [engineering]}
        result = run(
            self.ticket, self.channel, self.event_log, agents, _passing_sandbox,
            Settings(autonomy_mode="autonomous"), workspace=self.workspace,
        )
        return result, cto, engineering

    def test_criteria_are_written_and_reach_engineer_and_reviewer(self) -> None:
        criteria = ["feature.txt exists", "README.md is unchanged"]
        result, cto, engineering = self._run(criteria)

        self.assertEqual(result.ticket.status, TicketStatus.DONE)
        self.assertEqual(self.event_log.get_ticket("T-1").acceptance_criteria, criteria)
        in_progress = engineering.contexts[1]
        self.assertEqual(in_progress.ticket.status, TicketStatus.IN_PROGRESS)
        self.assertIn("1. feature.txt exists", _user_turn(in_progress))
        self.assertIsNone(in_progress.review_evidence)

        review = cto.contexts[1]
        self.assertEqual(review.ticket.status, TicketStatus.REVIEW)
        turn = _user_turn(review)
        self.assertIn("2. README.md is unchanged", turn)
        self.assertIn("git diff --stat", turn)
        self.assertIn("feature.txt |", turn)
        self.assertIn("+real work", turn)
        self.assertNotIn("lock entry 5", turn)

    def test_no_criteria_falls_back_to_the_description_and_logs_it(self) -> None:
        _result, cto, _engineering = self._run(None)

        self.assertEqual(self.event_log.get_ticket("T-1").acceptance_criteria, ["Write feature.txt."])
        texts = [m.content.text for m in self.event_log.get_channel_history("ch", 100)]
        self.assertTrue(any("Product wrote no acceptance criteria" in t for t in texts))
        self.assertIn("none were written", _user_turn(cto.contexts[1]))

    def test_without_a_workspace_review_gets_no_evidence(self) -> None:
        cto = _RecordingAgent("cto", [("decomposed", None), ("approved", None)])
        product = _RecordingAgent("product", [("spec_ready", ["x"])])
        engineering = _RecordingAgent("engineering", [("assigned", None), ("code_submission", None)])
        agents = {"cto": [cto], "product": [product], "engineering": [engineering]}
        run(self.ticket, self.channel, self.event_log, agents, _passing_sandbox,
            Settings(autonomy_mode="autonomous"))
        self.assertIsNone(cto.contexts[1].review_evidence)


# Both backends, through their real request building, against fakes.

EVIDENCE = "Code change under review.\n feature.txt | 1 +\n+real work\nEnd of diff."


def _review_context() -> AgentContext:
    return AgentContext(
        ticket=Ticket(
            id="T-1", title="Add the feature", description="Write feature.txt.",
            acceptance_criteria=["feature.txt exists"], status=TicketStatus.REVIEW,
        ),
        channel_id="c", role="cto", instance_id="cto-1", review_evidence=EVIDENCE,
    )


def _backlog_context() -> AgentContext:
    return AgentContext(
        ticket=Ticket(id="T-1", title="t", description="d", status=TicketStatus.BACKLOG),
        channel_id="c", role="product", instance_id="product-1",
    )


_ARGS: dict[str, Any] = {
    "narrative": "n",
    "handoff_done": "d",
    "handoff_remaining": "r",
    "handoff_notes_for_next": "x",
}


class _AnthropicBlock:
    type = "tool_use"
    name = "declare_outcome"
    id = "tu_1"

    def __init__(self, args: dict):
        self.input = args


class _AnthropicUsage:
    input_tokens = 10
    output_tokens = 5
    cache_creation_input_tokens = 0
    cache_read_input_tokens = 0


class _AnthropicResponse:
    def __init__(self, args: dict):
        self.content = [_AnthropicBlock(args)]
        self.usage = _AnthropicUsage()


class _AnthropicClient:
    def __init__(self, args: dict):
        self.calls: list[dict] = []
        self._args = args
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _AnthropicResponse(self._args)


class _OpenAICall:
    def __init__(self, args: dict):
        self.id = "call_1"
        self.type = "function"
        self.function = type("F", (), {"name": "declare_outcome", "arguments": json.dumps(args)})()


class _OpenAIClient:
    def __init__(self, args: dict):
        self.calls: list[dict] = []
        self._args = args
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        message = type("M", (), {"content": "", "tool_calls": [_OpenAICall(self._args)]})()
        choice = type("C", (), {"message": message, "finish_reason": "tool_calls"})()
        usage = type("U", (), {"prompt_tokens": 10, "completion_tokens": 5})()
        return type("R", (), {"choices": [choice], "usage": usage})()


CTO = RoleConfig(id="cto", name="CTO", tier=3, model="m")
PRODUCT = RoleConfig(id="product", name="Product", tier=2, model="m")


class BothBackendsTest(unittest.TestCase):
    def test_both_backends_send_criteria_stat_and_diff_at_review(self) -> None:
        claude_client = _AnthropicClient({**_ARGS, "trigger": "approved"})
        ClaudeAgent(CTO, "cto-1", client=claude_client).invoke(_review_context())
        claude_turn = claude_client.calls[0]["messages"][0]["content"]

        openai_client = _OpenAIClient({**_ARGS, "trigger": "approved"})
        OpenAIAgent(CTO, "cto-1", client=openai_client).invoke(_review_context())
        openai_turn = openai_client.calls[0]["messages"][1]["content"]

        self.assertEqual(claude_turn, openai_turn)
        for needle in ("1. feature.txt exists", "feature.txt | 1 +", "+real work"):
            self.assertIn(needle, claude_turn)

    def test_review_instruction_demands_every_criterion(self) -> None:
        client = _AnthropicClient({**_ARGS, "trigger": "approved"})
        ClaudeAgent(CTO, "cto-1", client=client).invoke(_review_context())
        system = client.calls[0]["system"][0]["text"]
        self.assertIn("approved only if every criterion is visibly met by the diff", system)

    def test_both_backends_offer_and_return_criteria_at_backlog(self) -> None:
        args = {**_ARGS, "trigger": "spec_ready", "acceptance_criteria": ["a", "b"]}

        claude_client = _AnthropicClient(args)
        claude_resp = ClaudeAgent(PRODUCT, "product-1", client=claude_client).invoke(_backlog_context())
        claude_schema = claude_client.calls[0]["tools"][0]["input_schema"]

        openai_client = _OpenAIClient(args)
        openai_resp = OpenAIAgent(PRODUCT, "product-1", client=openai_client).invoke(_backlog_context())
        openai_schema = openai_client.calls[0]["tools"][0]["function"]["parameters"]

        self.assertEqual(claude_schema["properties"]["acceptance_criteria"]["type"], "array")
        self.assertEqual(claude_schema, openai_schema)
        self.assertEqual(claude_resp.acceptance_criteria, ["a", "b"])
        self.assertEqual(openai_resp.acceptance_criteria, ["a", "b"])
        system = claude_client.calls[0]["system"][0]["text"]
        self.assertIn("2 to 8", system)

    def test_product_is_told_to_ground_every_criterion_in_the_task(self) -> None:
        args = {**_ARGS, "trigger": "spec_ready", "acceptance_criteria": ["a", "b"]}
        claude_client = _AnthropicClient(args)
        ClaudeAgent(PRODUCT, "product-1", client=claude_client).invoke(_backlog_context())
        openai_client = _OpenAIClient(args)
        OpenAIAgent(PRODUCT, "product-1", client=openai_client).invoke(_backlog_context())

        system = claude_client.calls[0]["system"][0]["text"]
        self.assertEqual(system, openai_client.calls[0]["messages"][0]["content"])
        for needle in (
            "must trace back to something the ticket's task text actually asks for",
            "where the task names files or content, name them",
            "Never add a requirement the task does not state",
            "performance or speed claims",
            "extra configuration or settings the task never mentions",
            "all existing functionality must be maintained",
        ):
            self.assertIn(needle, system)
        schema = claude_client.calls[0]["tools"][0]["input_schema"]
        self.assertIn(
            "must come from something the task text asks for",
            schema["properties"]["acceptance_criteria"]["description"],
        )

    def test_criteria_are_only_offered_at_backlog(self) -> None:
        client = _AnthropicClient({**_ARGS, "trigger": "approved"})
        resp = ClaudeAgent(CTO, "cto-1", client=client).invoke(_review_context())
        schema = client.calls[0]["tools"][0]["input_schema"]
        self.assertNotIn("acceptance_criteria", schema["properties"])
        self.assertIsNone(resp.acceptance_criteria)

    def test_openai_accepts_criteria_sent_as_one_string(self) -> None:
        args = {**_ARGS, "trigger": "spec_ready", "acceptance_criteria": "- a\n- b"}
        client = _OpenAIClient(args)
        resp = OpenAIAgent(PRODUCT, "product-1", client=client).invoke(_backlog_context())
        self.assertEqual(resp.acceptance_criteria, ["a", "b"])
        self.assertEqual(len(client.calls), 1)  # converted, not resampled


if __name__ == "__main__":
    unittest.main()
