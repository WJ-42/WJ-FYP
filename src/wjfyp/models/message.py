from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class MessageType(str, Enum):
    STATUS_UPDATE = "status_update"
    QUESTION = "question"
    HANDOFF = "handoff"
    CODE_SUBMISSION = "code_submission"
    REVIEW_FEEDBACK = "review_feedback"
    ESCALATION = "escalation"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    SYSTEM = "system"


class AgentRef(BaseModel):
    role: str
    instance_id: str


class ArtifactRef(BaseModel):
    kind: str
    ref: str


class TokenCost(BaseModel):
    prompt: int
    completion: int


class HandoffNote(BaseModel):
    """Structured summary an agent must produce on any transition-triggering
    message. This is what the next agent actually reads at invocation time,
    not raw channel history. See cs3ip-comm-protocol memory for the reasoning.
    """

    done: str
    remaining: str
    notes_for_next: str


class MessageContent(BaseModel):
    text: str
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    handoff: HandoffNote | None = None


class Message(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    sender: AgentRef
    channel_id: str
    recipient: AgentRef | None = None
    type: MessageType
    ticket_ref: str | None = None
    content: MessageContent
    parent_id: UUID | None = None
    token_cost: TokenCost | None = None
