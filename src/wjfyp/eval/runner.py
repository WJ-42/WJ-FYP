from __future__ import annotations

from pydantic import BaseModel

from wjfyp.config import Settings
from wjfyp.eval.mast import MastFailureMode, MastJudge, tag_run
from wjfyp.eval.scoring import TaskScore, score_task
from wjfyp.eval.task import EvalTask, GradingContext
from wjfyp.eventlog import EventLog
from wjfyp.models.channel import Channel
from wjfyp.models.ticket import Ticket
from wjfyp.orchestrator.agent import Agent
from wjfyp.orchestrator.loop import HiddenTestLookup, SandboxFactory, run
from wjfyp.sandbox.git_workspace import GitWorkspace


class EvalRunResult(BaseModel):
    """Everything one EvalTask run through the orchestrator produces:
    where the ticket ended up, how it scored against the task's own
    checkpoints, and what MAST failure modes were observed - the three
    things a report/dashboard needs to say anything meaningful about a
    run, tied together in one place rather than left for a caller to
    reassemble from separate calls.
    """

    task_id: str
    ticket_id: str
    ticket_status: str
    score: TaskScore
    mast_tags: list[MastFailureMode]


def run_eval_task(
    task: EvalTask,
    agents: dict[str, list[Agent]],
    sandbox_factory: SandboxFactory,
    workspace: GitWorkspace,
    event_log: EventLog,
    settings: Settings | None = None,
    judge: MastJudge | None = None,
    hidden_tests: HiddenTestLookup | None = None,
) -> EvalRunResult:
    """Run one EvalTask end to end: feed its prompt through the
    orchestrator as a single ticket (Intake through Done/Halted/stuck-in
    -Escalated), score the resulting repo state against the task's own
    checkpoints, and tag whatever MAST failure modes the run exhibits.
    This is the actual "tie the harness together" piece of FYP-19 -
    Layers A-D each work, but nothing before this called them as one
    pipeline against a single task.

    `agents` decides which configuration is under test: the real
    multi-agent team, or single_agent_pool(...)'s baseline - this
    function doesn't care which, matching cs3ip-comm-protocol memory's
    "not a second system to maintain".

    Unit-tested against the same Scripted/Fake stand-ins used throughout
    the rest of the suite, since it only depends on the
    Agent/SandboxController/EventLog protocols, not any concrete
    implementation - but a real ClaudeAgent + real Docker sandbox had
    never actually been driven through here until Option B's comparison
    runner (see cs3ip-fyp-overview memory), which is what surfaced two
    real bugs no Fake-based test could catch: the ticket built here had
    no `branch_name` (GitWorkspace.ensure_branch() raises on that), and
    `run()` was never given `workspace=`, so the Review->Done auto-merge
    never fired. Both fixed alongside Option B's runner landing.
    """
    ticket = Ticket(
        id=task.task_id,
        title=task.title,
        description=task.prompt,
        branch_name=f"ticket/{task.task_id}",
    )
    channel = Channel(id=f"channel-{ticket.id}", key=f"ticket:{ticket.id}", ticket_ref=ticket.id)

    result = run(
        ticket,
        channel,
        event_log,
        agents,
        sandbox_factory,
        settings or Settings(),
        hidden_tests=hidden_tests,
        workspace=workspace,
    )

    grading_context = GradingContext(ticket_id=ticket.id, repo_path=str(workspace.repo_path))
    score = score_task(task, grading_context)

    messages = event_log.get_messages_for_ticket(ticket.id)
    tags = tag_run(result.ticket, messages, judge=judge)

    return EvalRunResult(
        task_id=task.task_id,
        ticket_id=ticket.id,
        ticket_status=result.ticket.status.value,
        score=score,
        mast_tags=tags,
    )
