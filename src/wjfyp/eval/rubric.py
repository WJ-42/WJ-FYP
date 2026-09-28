from __future__ import annotations

from typing import Any, Protocol

import anthropic
from pydantic import BaseModel

from wjfyp.eval.task import EvalTask

_TOOL_NAME = "submit_rubric_score"


class RubricAxis(BaseModel):
    """One scored dimension of output quality. Plain data, not baked
    into a prompt string - both the LLM judge's tool schema/system
    prompt and a future human-facing form render FROM this, so the two
    audiences always score against the identical axis definitions (see
    cs3ip-fyp-overview memory's "Evaluation direction confirmed"
    entry: the human-eval study is meant to reuse the same fixed
    rubric as the LLM judge).
    """

    id: str
    name: str
    description: str
    min_score: int = 1
    max_score: int = 5


# Confirmed with Waleed 2026-09-28: standard SE quality axes, the same
# framing SWE-bench/TheAgentCompany research (already in this project's
# Related Work) uses. 1-5 scale: enough granularity to differentiate
# quality without forcing false precision from either an LLM or a human
# skimming a diff, and simple for a later human-facing form (5 radio
# buttons per axis, not 10).
RUBRIC: list[RubricAxis] = [
    RubricAxis(
        id="correctness",
        name="Correctness",
        description="Does the change actually do what the ticket asked, without introducing new bugs?",
    ),
    RubricAxis(
        id="code_quality",
        name="Code quality / readability",
        description=(
            "Is the change clear, idiomatic, and reasonably maintainable - naming, structure, "
            "comments where they earn their place?"
        ),
    ),
    RubricAxis(
        id="completeness",
        name="Completeness against acceptance criteria",
        description="Does the change cover everything the ticket's requirement asked for, not just a partial slice?",
    ),
]


class AxisScore(BaseModel):
    axis_id: str
    score: int
    justification: str


class RubricScore(BaseModel):
    """No condition_label field here - the caller (Option B's comparison
    runner) already tracks which team config a score belongs to; a
    second place to record that would just be one more thing that could
    drift out of sync with it.
    """

    task_id: str
    axis_scores: list[AxisScore]
    overall: float  # plain mean of axis_scores - computed here, never asked of the model,
    # so it's identical whether axis_scores came from an LLM or (later) a human form


class RubricJudge(Protocol):
    def score(self, task: EvalTask, diff_text: str) -> RubricScore: ...


def _rubric_tool_schema() -> dict[str, Any]:
    def axis_property(axis: RubricAxis) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "score": {"type": "integer", "enum": list(range(axis.min_score, axis.max_score + 1))},
                "justification": {"type": "string"},
            },
            "required": ["score", "justification"],
            "additionalProperties": False,
        }

    # One required sub-object per axis id, not a generic array of
    # {axis_id, score, justification} objects - a fixed-properties
    # object structurally guarantees every axis appears exactly once
    # under strict mode, which a JSON Schema array can't cheaply
    # guarantee (no "exactly one of each" constraint exists for arrays).
    return {
        "name": _TOOL_NAME,
        "description": "Score this diff against the task on every rubric axis.",
        "input_schema": {
            "type": "object",
            "properties": {axis.id: axis_property(axis) for axis in RUBRIC},
            "required": [axis.id for axis in RUBRIC],
            "additionalProperties": False,
        },
        "strict": True,
    }


def _judge_system_prompt() -> str:
    lines = [
        "You are an independent quality reviewer scoring a code change against a fixed "
        "rubric. You are not a member of the team that produced it - score strictly against "
        "the rubric, not on how difficult the task seemed.",
        "",
    ]
    for axis in RUBRIC:
        lines.append(f"{axis.name} ({axis.min_score}-{axis.max_score}): {axis.description}")
    return "\n".join(lines)


def _judge_user_turn(task: EvalTask, diff_text: str) -> str:
    return (
        f"Ticket: {task.title}\n\n"
        f"Requirement:\n{task.prompt}\n\n"
        f"Final diff:\n{diff_text or '(no diff - no changes were committed)'}"
    )


class LLMRubricJudge:
    """Reuses ClaudeAgent's established forced-tool-call pattern
    (strict tool schema, tool_choice forcing the one tool) rather than
    inventing a new structured-output mechanism - see
    orchestrator/claude_agent.py's own docstring for why that mechanism
    was chosen in the first place.

    Deliberately no prompt caching here (unlike claude_agent.py's
    multi-turn sandbox loop): this is a single one-shot call with
    nothing to amortize a cache write against, so caching would add
    overhead for zero reuse.

    Model defaults to Sonnet 5, not Opus 5 or Haiku: call volume is
    fixed and low regardless of model (run_structure_comparison calls
    this exactly once per condition, after that condition's run has
    already finished - never in a loop), so cost isn't the deciding
    factor here the way it is for the tiered per-turn ClaudeAgent
    roles. Sonnet is this project's own "capability-adequate, not
    premium" tier already used elsewhere, and a one-shot diff-quality
    judgment doesn't need Opus-level depth. `model` is a constructor
    parameter, not hardcoded, so this is a one-line change later if
    judgment quality turns out to matter more than expected.
    """

    def __init__(self, model: str = "claude-sonnet-5", client: anthropic.Anthropic | None = None):
        self._model = model
        self._client = client or anthropic.Anthropic()

    def score(self, task: EvalTask, diff_text: str) -> RubricScore:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=2000,
            system=_judge_system_prompt(),
            tools=[_rubric_tool_schema()],
            tool_choice={"type": "tool", "name": _TOOL_NAME},
            messages=[{"role": "user", "content": _judge_user_turn(task, diff_text)}],
        )
        args = next(block for block in response.content if block.type == "tool_use").input
        axis_scores = [
            AxisScore(
                axis_id=axis.id,
                score=args[axis.id]["score"],
                justification=args[axis.id]["justification"],
            )
            for axis in RUBRIC
        ]
        return RubricScore(
            task_id=task.task_id,
            axis_scores=axis_scores,
            overall=sum(a.score for a in axis_scores) / len(axis_scores),
        )
