import io, sys
p = "src/wjfyp/orchestrator/openai_agent.py"
src = io.open(p, encoding="utf-8").read()
orig = src

# 1. import os
old = "import json\nfrom pathlib import Path\n"
new = "import json\nimport os\nfrom pathlib import Path\n"
assert src.count(old) == 1, "import anchor"
src = src.replace(old, new)

# 2. constants after MAX_CONSECUTIVE_REJECTIONS
anchor = "MAX_CONSECUTIVE_REJECTIONS = 4\n"
assert src.count(anchor) == 1, "rejections anchor"
consts = anchor + '''

# The prompt budget for ONE In Progress request, in tokens, covering the
# transcript and the tool schemas together.
#
# This exists because of a measured failure rather than a precaution. On
# 04/11/2026, with the grader finally giving real pass/fail feedback,
# run 1 of the task-2 batch spent 45 minutes on a single turn against a
# 16,384-token server window with a prompt of 28,076 tokens. Ollama
# front-truncated it, so the model lost its own earlier attempts and
# oscillated between two assertions it had already seen fail:
#
#     "original contentappended content"       -> fails, there is a \\n
#     "original content\\nappended content"     -> fails, trailing \\n too
#
# It never reached the correct third form, because by the time it came
# back round the record of the first was gone. Five earlier turns in the
# same run, at 4,000 to 5,700 prompt tokens, each finished in 2 to 6
# minutes. The difference was entirely the window.
#
# Trimming in this module rather than raising the server's window is the
# deliberate choice. 32,768 tokens of KV cache would cost roughly 1.2
# GiB more than the 16,384 now in use, on a 6 GB card already holding
# 4 GB of weights, so it would spill into host RAM and run slower still.
# A transcript that fits costs nothing.
#
# 11,000 leaves room under a 16,384 window for MAX_OUTPUT_TOKENS of
# completion plus headroom for the tokenizer estimate being wrong in the
# unsafe direction. Override it when the server's window differs - this
# number is a property of the SERVER, not of this code.
CONTEXT_BUDGET_TOKENS = int(os.environ.get("WJFYP_CONTEXT_BUDGET_TOKENS", "11000"))

# Nothing in the OpenAI-compatible API reports a tokenizer, so the
# budget is enforced against an estimate from character counts. 3.5 is
# the starting guess, below English's 4 to 5 because transcripts here
# are mostly code and JSON, which tokenize worse. It is only a seed:
# _calibrate replaces it with the ratio the server itself reports, so a
# model whose tokenizer differs corrects itself after one request.
#
# The bounds exist so a single odd reading cannot disable the trim. A
# ratio read as very high would make every transcript look small and
# every prompt fit, which is the exact failure this guards against, so
# an implausible measurement is discarded rather than trusted.
_CHARS_PER_TOKEN_SEED = 3.5
_CHARS_PER_TOKEN_BOUNDS = (2.0, 6.0)

# The most recent exchange is never dropped: it holds the action the
# model just took and the result it needs to react to, so a turn without
# it cannot proceed at all. One, not two, because tool results are
# already capped at 2,000 characters by claude_agent._truncate while an
# assistant message can carry a whole file, and a floor of two could
# therefore exceed the budget on its own.
_MIN_KEPT_GROUPS = 1

# How much of a dropped call's arguments survives into the digest.
# Enough to tell two attempts apart - which assertion, which path - and
# not enough to put the file back in the prompt.
_DIGEST_ARG_CHARS = 140

# A dropped-call digest is bounded too, or it would grow into the thing
# it replaces. Twelve iterations of a few calls each fits well inside
# this; past it the OLDEST lines are kept, because the point of the
# digest is remembering what already failed.
_MAX_DIGEST_LINES = 40
'''
src = src.replace(anchor, consts)

# 3. module-level helpers before `class OpenAIAgent:`
anchor = "class OpenAIAgent:"
assert src.count(anchor) == 1, "class anchor"
helpers = '''def _exchange_groups(body: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Splits the appended part of a transcript into units that can be
    dropped whole.

    A group is one assistant message plus every tool reply answering its
    tool_call ids, because that pairing is required rather than
    conventional: an assistant message holding a call whose reply is
    missing is rejected by the endpoint, and so is a reply whose call
    has gone. Whole groups are therefore the only safe granularity, and
    trimming by message count would produce a transcript no server
    accepts. A plain user message - the corrections and nudges this loop
    appends - stands alone.
    """
    groups: list[list[dict[str, Any]]] = []
    for msg in body:
        if msg.get("role") == "tool" and groups and groups[-1][0].get("role") == "assistant":
            groups[-1].append(msg)
        else:
            groups.append([msg])
    return groups


def _digest_group(group: list[dict[str, Any]]) -> list[str]:
    """One line per tool call in a group about to be dropped: what was
    called, enough of the arguments to identify the attempt, and whether
    it worked.

    The outcome is the part that matters. Dropping an exchange silently
    is what produced the oscillation described above CONTEXT_BUDGET_TOKENS,
    so a trim that only freed space would reproduce that bug more
    cheaply. Carrying the failures forward is the point.
    """
    head = group[0]
    if head.get("role") != "assistant":
        return []
    replies = {m.get("tool_call_id"): m for m in group[1:]}
    lines: list[str] = []
    for call in head.get("tool_calls") or []:
        function = call.get("function") or {}
        name = function.get("name") or "?"
        args = " ".join((function.get("arguments") or "").split())
        if len(args) > _DIGEST_ARG_CHARS:
            args = args[:_DIGEST_ARG_CHARS] + " ..."
        content = (replies.get(call.get("id")) or {}).get("content") or ""
        outcome = "FAILED" if content.startswith("ERROR:") else "ok"
        lines.append(f"- {name} {args} -> {outcome}")
    return lines


def _elision_note(lines: list[str]) -> str:
    """The text appended to the ticket turn in place of what was cut."""
    kept = lines[:_MAX_DIGEST_LINES]
    body = "\\n".join(kept)
    if len(lines) > _MAX_DIGEST_LINES:
        body += f"\\n- ... and {len(lines) - _MAX_DIGEST_LINES} further steps"
    return (
        "\\n\\n--- EARLIER STEPS THIS TURN ---\\n"
        "Your earlier steps have been summarised to fit the context window. "
        "You already did the following, oldest first. Anything marked FAILED "
        "did not work, so do not try it again unchanged.\\n" + body
    )


'''
src = src.replace(anchor, helpers + anchor)

io.open(p, "w", encoding="utf-8").write(src)
print("constants+helpers inserted, grew", len(src) - len(orig), "chars")
