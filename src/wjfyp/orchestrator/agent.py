from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

from wjfyp.models.message import HandoffNote, Message
from wjfyp.models.ticket import Ticket


class AgentContext(BaseModel):
    """What an agent receives on invocation: ticket metadata plus the
    previous agent's handoff note - NOT raw channel history by default,
    per cs3ip-comm-protocol memory. An agent that judges this
    insufficient calls EventLog.get_channel_history(n) itself rather
    than the orchestrator pre-fetching it.
    """

    ticket: Ticket
    channel_id: str
    role: str
    instance_id: str
    handoff: HandoffNote | None = None


class AgentResponse(BaseModel):
    """An agent's output: the Message to append to the log, plus the FSM
    trigger it's declaring (e.g. "tests_passed", "changes_requested").
    `trigger` lives outside Message on purpose - Message's schema is
    fixed by the frozen Project Brief, and the trigger is a derived
    orchestrator concern, not part of the log itself. See fsm.py's
    module docstring for the trigger-derivation note.
    """

    message: Message
    trigger: str


class Agent(Protocol):
    """A single agent instance. Concrete LLM-backed implementations are a
    later layer - model selection is still a config placeholder (see
    config/roles.yaml) and no API access is wired up yet. This protocol
    is what the orchestrator loop depends on, so loop mechanics can be
    built and tested now without waiting on that.
    """

    role: str
    instance_id: str

    def invoke(self, context: AgentContext) -> AgentResponse: ...
