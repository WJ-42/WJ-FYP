"""What actually fills an In Progress request?

Measures the fixed floor, the part resent on every iteration, against
the free tier's 8,000 token-per-minute ceiling, to see whether the wall
is the tier or the implementation.
"""

import json
from pathlib import Path

from wjfyp.config import load_roles
from wjfyp.models.ticket import Ticket, TicketStatus
from wjfyp.orchestrator.agent import AgentContext
from wjfyp.orchestrator.claude_agent import (
    _MAX_TOOL_RESULT_CHARS,
    _SANDBOX_TOOL_SCHEMAS,
    _base_system_prompt,
    _tool_schema,
    _user_turn,
)
from wjfyp.orchestrator.openai_agent import _to_openai_tool

roles = {r.id: r for r in load_roles(Path("config/roles_groq.yaml"))}
engineering = roles["engineering"]
triggers = ["code_submission", "escalated"]

system_prompt = _base_system_prompt(engineering, TicketStatus.IN_PROGRESS, triggers)
tools = [_to_openai_tool(s) for s in [*_SANDBOX_TOOL_SCHEMAS, _tool_schema(triggers)]]
ticket = Ticket(
    id="X-1",
    title="Validate the session expiry header",
    description="The expiry header is read but never validated.",
    acceptance_criteria=["Reject malformed values.", "A test covers it."],
    status=TicketStatus.IN_PROGRESS,
)
user_turn = _user_turn(
    AgentContext(ticket=ticket, channel_id="c", role="engineering", instance_id="e-1")
)


def tokens(text: str) -> float:
    return len(text) / 4  # rough, but consistent across the comparison


system_t = tokens(system_prompt)
tools_t = tokens(json.dumps(tools))
user_t = tokens(user_turn)
fixed = system_t + tools_t + user_t

print(f"  system prompt   {system_t:7,.0f} tok")
print(f"  tool schemas    {tools_t:7,.0f} tok")
print(f"  user turn       {user_t:7,.0f} tok")
print("  " + "-" * 32)
print(f"  FIXED FLOOR     {fixed:7,.0f} tok   resent on every iteration")
print(f"  ceiling          8,000 tok")
print(f"  headroom        {8000 - fixed:7,.0f} tok for the whole growing transcript")
print()
print(f"  current tool-result cap: {_MAX_TOOL_RESULT_CHARS:,} chars = {_MAX_TOOL_RESULT_CHARS/4:,.0f} tok")
print("  -> one large read_file exceeds the entire headroom by itself")
print()
for cap in [8000, 4000, 2000, 1000]:
    per_call = cap / 4
    affordable = int((8000 - fixed) / (per_call * 2))  # call + result each turn
    print(f"  cap {cap:5,} chars ({per_call:4,.0f} tok) -> about {affordable:2} tool calls before the ceiling")
