from __future__ import annotations

from enum import Enum
from typing import Protocol

from wjfyp.models.message import Message
from wjfyp.models.ticket import Ticket, TicketStatus


class MastFailureMode(str, Enum):
    """The 14 failure modes from MAST (Cemri et al., arXiv:2503.13657),
    confirmed against the paper's own Figure 1 taxonomy directly, not a
    summary (see cs3ip-evaluation-detail memory) - including one naming
    correction the paper's own text makes clear: FC1 is "System Design
    Issues", not "Specification Issues".
    """

    FM_1_1 = "FM-1.1"  # Disobey Task Specification
    FM_1_2 = "FM-1.2"  # Disobey Role Specification
    FM_1_3 = "FM-1.3"  # Step Repetition
    FM_1_4 = "FM-1.4"  # Loss of Conversation History
    FM_1_5 = "FM-1.5"  # Unaware of Termination Conditions

    FM_2_1 = "FM-2.1"  # Conversation Reset
    FM_2_2 = "FM-2.2"  # Fail to Ask for Clarification
    FM_2_3 = "FM-2.3"  # Task Derailment
    FM_2_4 = "FM-2.4"  # Information Withholding
    FM_2_5 = "FM-2.5"  # Ignored Other Agent's Input
    FM_2_6 = "FM-2.6"  # Reasoning-Action Mismatch

    FM_3_1 = "FM-3.1"  # Premature Termination
    FM_3_2 = "FM-3.2"  # No or Incomplete Verification
    FM_3_3 = "FM-3.3"  # Incorrect Verification


MAST_NAMES: dict[MastFailureMode, str] = {
    MastFailureMode.FM_1_1: "Disobey Task Specification",
    MastFailureMode.FM_1_2: "Disobey Role Specification",
    MastFailureMode.FM_1_3: "Step Repetition",
    MastFailureMode.FM_1_4: "Loss of Conversation History",
    MastFailureMode.FM_1_5: "Unaware of Termination Conditions",
    MastFailureMode.FM_2_1: "Conversation Reset",
    MastFailureMode.FM_2_2: "Fail to Ask for Clarification",
    MastFailureMode.FM_2_3: "Task Derailment",
    MastFailureMode.FM_2_4: "Information Withholding",
    MastFailureMode.FM_2_5: "Ignored Other Agent's Input",
    MastFailureMode.FM_2_6: "Reasoning-Action Mismatch",
    MastFailureMode.FM_3_1: "Premature Termination",
    MastFailureMode.FM_3_2: "No or Incomplete Verification",
    MastFailureMode.FM_3_3: "Incorrect Verification",
}

MAST_CATEGORIES: dict[MastFailureMode, str] = {
    **{mode: "FC1 System Design Issues" for mode in (
        MastFailureMode.FM_1_1,
        MastFailureMode.FM_1_2,
        MastFailureMode.FM_1_3,
        MastFailureMode.FM_1_4,
        MastFailureMode.FM_1_5,
    )},
    **{mode: "FC2 Inter-Agent Misalignment" for mode in (
        MastFailureMode.FM_2_1,
        MastFailureMode.FM_2_2,
        MastFailureMode.FM_2_3,
        MastFailureMode.FM_2_4,
        MastFailureMode.FM_2_5,
        MastFailureMode.FM_2_6,
    )},
    **{mode: "FC3 Task Verification" for mode in (
        MastFailureMode.FM_3_1,
        MastFailureMode.FM_3_2,
        MastFailureMode.FM_3_3,
    )},
}


def _rule_based_tags(status: TicketStatus) -> list[MastFailureMode]:
    """Tags derivable with certainty from the ticket's final FSM status
    alone, per the hybrid design (cs3ip-evaluation-detail memory): a
    rule only fires where the orchestrator's own signal makes the code
    unambiguous, everything subtler is left to the judge.

    Both rules here rest on fsm.py's transition table being exactly what
    it is, not on inference: ESCALATED has exactly one inbound
    transition, (AWAITING_TEST, "retry_cap_exceeded"), so reaching it at
    all means the retry loop was exhausted - Step Repetition (FM-1.3).
    HALTED has exactly one inbound transition, (ESCALATED,
    "cto_cannot_resolve"), meaning the run ended with the requirement
    unresolved - Premature Termination (FM-3.1), on top of the same
    Step Repetition tag since HALTED is only reachable via ESCALATED.

    Known limitation, not hidden: this only looks at the *final* status,
    so a ticket that escalated and later recovered via cto_override to
    reach DONE won't be tagged, even though step repetition genuinely
    occurred mid-run. Ticket carries no history of past statuses to
    detect that cheaply, and a recovered run reads more as "the retry
    mechanism worked" than a failure worth flagging - revisit only if
    evaluation shows this gap matters, same "implementation-level,
    revisit if evaluation shows otherwise" treatment as
    _pick_instance's scheduling policy.
    """
    if status == TicketStatus.ESCALATED:
        return [MastFailureMode.FM_1_3]
    if status == TicketStatus.HALTED:
        return [MastFailureMode.FM_1_3, MastFailureMode.FM_3_1]
    return []


class MastJudge(Protocol):
    """The LLM-judge half of the hybrid design, for failure modes a
    rule can't reliably distinguish (e.g. information withholding vs.
    ignored input, reasoning-action mismatch) - Cemri et al.'s own
    approach was an LLM-as-judge pipeline for exactly this reason. No
    concrete LLM-backed implementation yet: mirrors wjfyp.orchestrator
    .agent.Agent's own deferred status, blocked on the same model/API
    selection.
    """

    def judge(self, ticket: Ticket, messages: list[Message]) -> list[MastFailureMode]: ...


def tag_run(
    ticket: Ticket, messages: list[Message], judge: MastJudge | None = None
) -> list[MastFailureMode]:
    """Tag one completed ticket run with every MAST failure mode the
    hybrid tagger can identify: rule-based tags always applied, plus
    whatever a judge adds on top, deduplicated. `judge` is optional -
    omitting it runs the rule-based half alone, useful before a real
    judge exists and for tests.
    """
    tags = list(_rule_based_tags(ticket.status))
    if judge is not None:
        for tag in judge.judge(ticket, messages):
            if tag not in tags:
                tags.append(tag)
    return tags
