"""Exercises the orchestrator loop mechanics end to end, using a
scripted stub agent (no model wired up yet) and a fake sandbox (no
Docker) rather than real ones. This tests control flow (FSM stepping,
retry-cap bookkeeping, intervention pausing/resume, sandbox lifecycle
per attempt), not agent intelligence or real code execution.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from wjfyp.config import Settings
from wjfyp.eventlog import EventLog
from wjfyp.models.channel import Channel
from wjfyp.models.message import (
    AgentRef,
    HandoffNote,
    Message,
    MessageContent,
    MessageType,
)
from wjfyp.models.ticket import Ticket, TicketStatus
from wjfyp.orchestrator.agent import AgentContext, AgentResponse
from wjfyp.orchestrator.loop import resume, run
from wjfyp.sandbox.controller import TestResult
from wjfyp.sandbox.fake import FakeSandboxController
from wjfyp.sandbox.git_workspace import GitWorkspace
from wjfyp.sandbox.hidden_tests import HiddenTestSpec


class ScriptedAgent:
    """A stub Agent that returns pre-scripted responses in order,
    standing in for a real LLM-backed agent until model selection is
    resolved (see cs3ip-fyp-overview memory).
    """

    def __init__(self, role: str, instance_id: str, script: list[str]):
        self.role = role
        self.instance_id = instance_id
        self._script = list(script)
        # Every AgentContext this agent was actually invoked with, in
        # order - lets a test check what a specific turn received (e.g.
        # its handoff) rather than only what got appended to the log.
        self.received_contexts: list[AgentContext] = []

    def invoke(self, context: AgentContext) -> AgentResponse:
        self.received_contexts.append(context)
        trigger = self._script.pop(0)
        message = Message(
            sender=AgentRef(role=self.role, instance_id=self.instance_id),
            channel_id=context.channel_id,
            type=MessageType.STATUS_UPDATE,
            ticket_ref=context.ticket.id,
            content=MessageContent(
                text=f"{self.role} fires {trigger}",
                handoff=HandoffNote(
                    done=f"handled by {self.role}",
                    remaining="n/a",
                    notes_for_next="n/a",
                ),
            ),
        )
        return AgentResponse(message=message, trigger=trigger)


def _make_ticket() -> Ticket:
    return Ticket(id="TCK-1", title="Test ticket", description="...")


def _make_channel(ticket: Ticket) -> Channel:
    return Channel(id=f"channel-{ticket.id}", key=f"ticket:{ticket.id}", ticket_ref=ticket.id)


def _sandbox_factory_with_results(results: list[TestResult]):
    """Each ticket-attempt gets a fresh sandbox (see cs3ip-sandbox-design
    memory) - this hands out a new FakeSandboxController per call, each
    pre-loaded with the next scripted test result in order.
    """
    remaining = list(results)

    def factory(_ticket: Ticket) -> FakeSandboxController:
        return FakeSandboxController(test_results=[remaining.pop(0)])

    return factory


def _sandbox_factory_with_hidden_results(results: list[TestResult]):
    """Like _sandbox_factory_with_results, but scripts run_hidden_tests()
    instead - and records every sandbox handed out, so a test can assert
    on what the orchestrator actually called after the run finishes.
    """
    remaining = list(results)
    created: list[FakeSandboxController] = []

    def factory(_ticket: Ticket) -> FakeSandboxController:
        sandbox = FakeSandboxController(hidden_test_results=[remaining.pop(0)])
        created.append(sandbox)
        return sandbox

    factory.created = created  # type: ignore[attr-defined]
    return factory


def _test_result(passed: bool) -> TestResult:
    return TestResult(
        passed=passed,
        fail_to_pass_total=1,
        fail_to_pass_passed=1 if passed else 0,
        pass_to_pass_total=0,
        pass_to_pass_passed=0,
        output="",
    )


class OrchestratorLoopTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(self._tmpdir.name) / "eventlog.db"
        self.event_log = EventLog(db_path)
        self.addCleanup(self.event_log.close)
        self.addCleanup(self._tmpdir.cleanup)

    def _agents(self, **scripts: list[str]) -> dict[str, list]:
        return {
            role: [ScriptedAgent(role, f"{role}-1", script)]
            for role, script in scripts.items()
        }

    def test_happy_path_reaches_done_in_autonomous_mode(self) -> None:
        ticket = _make_ticket()
        channel = _make_channel(ticket)
        settings = Settings(autonomy_mode="autonomous")
        agents = self._agents(
            cto=["decomposed", "approved"],
            product=["spec_ready"],
            engineering=["assigned", "code_submission"],
        )
        sandbox_factory = _sandbox_factory_with_results([_test_result(passed=True)])

        result = run(ticket, channel, self.event_log, agents, sandbox_factory, settings)

        self.assertTrue(
            not result.advanced and result.ticket.status == TicketStatus.DONE
        )
        self.assertEqual(self.event_log.get_ticket(ticket.id).status, TicketStatus.DONE)
        # cto x2, product x1, engineering x2, sandbox tool_result x1.
        self.assertEqual(len(self.event_log.get_channel_history(channel.id, 100)), 6)

    def test_hidden_tests_lookup_is_used_at_awaiting_test_instead_of_run_tests(self) -> None:
        ticket = _make_ticket()
        channel = _make_channel(ticket)
        settings = Settings(autonomy_mode="autonomous")
        agents = self._agents(
            cto=["decomposed", "approved"],
            product=["spec_ready"],
            engineering=["assigned", "code_submission"],
        )
        spec = HiddenTestSpec(
            test_files={"tests/test_hidden.py": "def test_hidden():\n    assert True\n"},
            fail_to_pass=["tests/test_hidden.py::test_hidden"],
            pass_to_pass=["tests/test_existing.py::test_existing"],
        )
        hidden_result = TestResult(
            passed=True,
            fail_to_pass_total=1,
            fail_to_pass_passed=1,
            pass_to_pass_total=1,
            pass_to_pass_passed=1,
            output="1 passed",
        )
        sandbox_factory = _sandbox_factory_with_hidden_results([hidden_result])

        result = run(
            ticket,
            channel,
            self.event_log,
            agents,
            sandbox_factory,
            settings,
            hidden_tests=lambda _ticket: spec,
        )

        self.assertEqual(result.ticket.status, TicketStatus.DONE)
        # Exactly one sandbox was created for the single attempt, and it
        # was asked to grade the hidden spec, not the plain run_tests().
        [sandbox] = sandbox_factory.created  # type: ignore[attr-defined]
        self.assertEqual(sandbox.hidden_test_specs_seen, [spec])
        self.assertEqual(sandbox._test_results, [])  # run_tests() was never called

        history = self.event_log.get_channel_history(channel.id, 100)
        tool_result = next(m for m in history if m.type.value == "tool_result")
        self.assertIn("FAIL_TO_PASS 1/1", tool_result.content.text)
        self.assertIn("PASS_TO_PASS 1/1", tool_result.content.text)

    def test_retry_cap_exceeded_escalates_instead_of_looping_forever(self) -> None:
        ticket = _make_ticket()
        channel = _make_channel(ticket)
        settings = Settings(autonomy_mode="autonomous")
        # Fails four attempts in a row: 3 is within cap (loops back to
        # in_progress, a fresh sandbox each time), the 4th must force
        # retry_cap_exceeded. escalated is not terminal - it's a real
        # CTO-actionable state, so the loop keeps going and hands it to
        # the CTO agent, which here reports it can't be resolved either.
        agents = self._agents(
            cto=["decomposed", "cto_cannot_resolve"],
            product=["spec_ready"],
            engineering=[
                "assigned",
                "code_submission",
                "code_submission",
                "code_submission",
                "code_submission",
            ],
        )
        sandbox_factory = _sandbox_factory_with_results(
            [_test_result(passed=False) for _ in range(4)]
        )

        result = run(ticket, channel, self.event_log, agents, sandbox_factory, settings)

        self.assertTrue(not result.advanced)
        self.assertEqual(result.ticket.status, TicketStatus.HALTED)
        self.assertEqual(result.ticket.retry_count, 0)  # reset on entering escalated

    def test_intervention_mode_pauses_and_resume_applies_human_decision(self) -> None:
        ticket = _make_ticket()
        channel = _make_channel(ticket)
        settings = Settings(autonomy_mode="intervention")
        agents = self._agents(
            cto=["decomposed", "changes_requested", "approved"],
            product=["spec_ready"],
            engineering=["assigned", "code_submission", "code_submission"],
        )
        sandbox_factory = _sandbox_factory_with_results(
            [_test_result(passed=True), _test_result(passed=True)]
        )

        paused = run(ticket, channel, self.event_log, agents, sandbox_factory, settings)
        self.assertFalse(paused.advanced)
        self.assertEqual(paused.ticket.status, TicketStatus.REVIEW)
        self.assertEqual(paused.paused_trigger, "changes_requested")
        # Persisted, not just returned - a separate dashboard process
        # reading the event log needs to see this too (cs3ip-comm-
        # protocol memory: orchestrator and dashboard are separate
        # processes sharing only the datastore).
        self.assertEqual(paused.ticket.pending_trigger, "changes_requested")
        self.assertEqual(self.event_log.get_ticket(ticket.id).pending_trigger, "changes_requested")

        # A human confirms the proposed trigger, with notes attached
        # ("repeat with notes" - cs3ip-project-diary memory); the loop
        # continues, sends the ticket through another in_progress/
        # awaiting_test pass (a brand new sandbox for that attempt), and
        # pauses again at the next requires_approval gate.
        confirmed = resume(
            paused.ticket,
            "changes_requested",
            channel,
            self.event_log,
            agents,
            sandbox_factory,
            settings,
            notes="please also add a docstring",
        )
        self.assertFalse(confirmed.advanced)
        self.assertEqual(confirmed.ticket.status, TicketStatus.REVIEW)
        self.assertEqual(confirmed.paused_trigger, "approved")
        # pending_trigger from the first pause was cleared by the
        # transition resume() just applied, then set fresh for the
        # second pause - never both at once.
        self.assertEqual(confirmed.ticket.pending_trigger, "approved")
        self.assertIsNone(confirmed.ticket.human_decision)

        # The notes got logged as a real message the next agent turn
        # could see, not silently dropped.
        history = self.event_log.get_channel_history(channel.id, 100)
        notes_message = next(m for m in history if m.content.text == "please also add a docstring")
        self.assertEqual(notes_message.sender.role, "human")

        # And critically: it's what the very next agent turn's default
        # context actually receives (engineering, since resume() drove
        # the ticket straight through in_progress again before pausing
        # at review a second time) - not just present somewhere in
        # get_channel_history's fallback path. Checking received context
        # directly, not last_handoff() after the fact: by the time this
        # test reaches here, engineering's own turn has since logged its
        # own handoff (as does the CTO's next proposal), which would
        # overwrite what last_handoff() returns - the guarantee that
        # matters is what THIS turn received, not the log's current
        # tail. A message with no handoff attached is invisible to
        # last_handoff() entirely - a first cut of this feature logged
        # text-only and would have silently dropped the notes for any
        # agent that didn't explicitly fall back to channel history.
        engineering_agent = agents["engineering"][0]
        self.assertEqual(len(engineering_agent.received_contexts), 3)  # 2 from the initial run, 1 after resume
        self.assertEqual(
            engineering_agent.received_contexts[-1].handoff.notes_for_next, "please also add a docstring"
        )

        final = resume(
            confirmed.ticket,
            "approved",
            channel,
            self.event_log,
            agents,
            sandbox_factory,
            settings,
        )
        self.assertFalse(final.advanced)
        self.assertEqual(final.ticket.status, TicketStatus.DONE)
        # Fully resolved - nothing pending left on a Done ticket.
        self.assertIsNone(final.ticket.pending_trigger)
        self.assertIsNone(final.ticket.human_decision)
        self.assertIsNone(final.ticket.decision_notes)

    def _real_workspace_with_a_ticket_branch_commit(self, ticket: Ticket) -> GitWorkspace:
        """A real, throwaway git repo with ticket.branch_name already
        carrying a commit main doesn't have - simulating what a real
        git_commit() push-back from a container leaves behind, without
        needing a real Docker sandbox for these orchestrator-level tests
        (real Docker mechanics are covered separately, per
        cs3ip-sandbox-design memory)."""
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        repo_path = Path(tmpdir.name)
        subprocess.run(["git", "init", "-b", "main", str(repo_path)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo_path), "config", "user.email", "test@example.com"], check=True)
        subprocess.run(["git", "-C", str(repo_path), "config", "user.name", "Test"], check=True)
        (repo_path / "README.md").write_text("hello\n")
        subprocess.run(["git", "-C", str(repo_path), "add", "."], check=True)
        subprocess.run(["git", "-C", str(repo_path), "commit", "-m", "initial"], check=True, capture_output=True)

        workspace = GitWorkspace(repo_path, default_base="main")
        workspace.ensure_branch(ticket)
        subprocess.run(
            ["git", "-C", str(repo_path), "checkout", ticket.branch_name], check=True, capture_output=True
        )
        (repo_path / "feature.txt").write_text("real work\n")
        subprocess.run(["git", "-C", str(repo_path), "add", "."], check=True)
        subprocess.run(
            ["git", "-C", str(repo_path), "commit", "-m", "the ticket's work"], check=True, capture_output=True
        )
        subprocess.run(["git", "-C", str(repo_path), "checkout", "main"], check=True, capture_output=True)
        return workspace

    def test_approving_in_autonomous_mode_merges_into_the_workspace(self) -> None:
        ticket = Ticket(id="TCK-1", title="Test ticket", description="...", branch_name="ticket/TCK-1")
        channel = _make_channel(ticket)
        settings = Settings(autonomy_mode="autonomous")
        agents = self._agents(
            cto=["decomposed", "approved"],
            product=["spec_ready"],
            engineering=["assigned", "code_submission"],
        )
        sandbox_factory = _sandbox_factory_with_results([_test_result(passed=True)])
        workspace = self._real_workspace_with_a_ticket_branch_commit(ticket)

        result = run(ticket, channel, self.event_log, agents, sandbox_factory, settings, workspace=workspace)

        self.assertEqual(result.ticket.status, TicketStatus.DONE)
        self.assertTrue((workspace.repo_path / "feature.txt").exists())

    def test_a_failed_merge_prevents_the_ticket_from_becoming_done(self) -> None:
        """The same "don't let a failure look like success" check this
        project applies elsewhere (git_commit's _exec_checked, the
        code_submission commit gate) - a ticket must not end up Done in
        the persisted event log while its merge actually failed."""
        ticket = Ticket(id="TCK-1", title="Test ticket", description="...", branch_name="ticket/TCK-1")
        channel = _make_channel(ticket)
        settings = Settings(autonomy_mode="autonomous")
        agents = self._agents(
            cto=["decomposed", "approved"],
            product=["spec_ready"],
            engineering=["assigned", "code_submission"],
        )
        sandbox_factory = _sandbox_factory_with_results([_test_result(passed=True)])

        class _PoisonWorkspace:
            def merge(self, _ticket: Ticket) -> str:
                raise RuntimeError("simulated merge conflict")

        with self.assertRaises(RuntimeError):
            run(ticket, channel, self.event_log, agents, sandbox_factory, settings, workspace=_PoisonWorkspace())

        self.assertEqual(self.event_log.get_ticket(ticket.id).status, TicketStatus.REVIEW)

    def test_resume_merges_when_a_human_approves_in_intervention_mode(self) -> None:
        ticket = Ticket(id="TCK-1", title="Test ticket", description="...", branch_name="ticket/TCK-1")
        channel = _make_channel(ticket)
        settings = Settings(autonomy_mode="intervention")
        agents = self._agents(
            cto=["decomposed", "approved"],
            product=["spec_ready"],
            engineering=["assigned", "code_submission"],
        )
        sandbox_factory = _sandbox_factory_with_results([_test_result(passed=True)])
        workspace = self._real_workspace_with_a_ticket_branch_commit(ticket)

        paused = run(ticket, channel, self.event_log, agents, sandbox_factory, settings, workspace=workspace)
        self.assertEqual(paused.paused_trigger, "approved")
        self.assertFalse((workspace.repo_path / "feature.txt").exists())  # not merged yet

        final = resume(
            paused.ticket, "approved", channel, self.event_log, agents, sandbox_factory, settings,
            workspace=workspace,
        )

        self.assertEqual(final.ticket.status, TicketStatus.DONE)
        self.assertTrue((workspace.repo_path / "feature.txt").exists())


if __name__ == "__main__":
    unittest.main()
