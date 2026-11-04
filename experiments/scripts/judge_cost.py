"""What does keeping the rubric judge on Claude actually cost?

The judge is deliberately NOT moved to the open model (see cli.py's
--backend help): a fixed measuring instrument is worth more than a free
one, because a judge that changes with the thing being judged cannot
separate "this arm produced a better diff" from "this judge liked this
arm". But "deliberate" is not the same as "affordable", and the
no-spend constraint is a hard one, so the figure has to be real.
"""

import json

from wjfyp.eval.rubric import RUBRIC, _judge_system_prompt, _judge_user_turn, _rubric_tool_schema
from wjfyp.eval.task import EvalTask

PRICES = {"claude-sonnet-5": (2.00, 10.00)}  # from budget.py, USD per million

system = _judge_system_prompt()
schema = json.dumps(_rubric_tool_schema())

# A realistic diff: the judge sees the FINAL diff for the whole ticket.
# 120 changed lines at ~45 chars is a normal small-feature diff.
diff = "\n".join(f"+    some_changed_line_of_code_number_{i}()" for i in range(120))
task = EvalTask(
    task_id="t",
    title="Validate the expiry header",
    prompt="The header is read but never validated. Reject malformed values and test it.",
    checkpoints=[],
)
user = _judge_user_turn(task, diff)

tok = lambda s: len(s) / 4
input_tokens = tok(system) + tok(schema) + tok(user)
# Output is bounded by max_tokens=2000; a filled rubric is far smaller.
output_tokens = 120 * len(RUBRIC)

inp, outp = PRICES["claude-sonnet-5"]
per_call = (input_tokens / 1e6) * inp + (output_tokens / 1e6) * outp

print(f"  rubric axes          {len(RUBRIC)}")
print(f"  system prompt        {tok(system):7,.0f} tok")
print(f"  rubric tool schema   {tok(schema):7,.0f} tok")
print(f"  user turn (120-line diff) {tok(user):7,.0f} tok")
print(f"  input total          {input_tokens:7,.0f} tok")
print(f"  output (estimated)   {output_tokens:7,.0f} tok")
print(f"\n  per judge call       ${per_call:.4f}")
print(f"  per comparison (2)   ${per_call * 2:.4f}")
for runs in [22, 39, 93]:
    print(f"  {runs:3} runs/arm, 2 arms -> {runs*2:4} calls = ${per_call * runs * 2:7.2f}")
