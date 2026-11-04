import io
p = "src/wjfyp/orchestrator/openai_agent.py"
src = io.open(p, encoding="utf-8").read()
orig = src

# 1. capture pristine head + tool schema size
old = """        messages = self._messages(context, triggers)
        total_prompt = 0
"""
assert src.count(old) == 1, "messages init anchor"
new = """        messages = self._messages(context, triggers)
        # Kept aside so _trim_transcript can rebuild the head from the
        # original each time rather than editing a message in place, which
        # would make the elision note accumulate on top of itself.
        system_message = messages[0]
        ticket_turn = str(messages[1]["content"])
        # The tool schemas go out with every request and count toward the
        # server's window exactly as the transcript does.
        tools_chars = len(json.dumps(tools, default=str))
        elided: list[str] = []
        total_prompt = 0
"""
src = src.replace(old, new)

# 2. trim at the top of each iteration
old = """        for _ in range(MAX_SANDBOX_ITERATIONS):
            sent_chars = len(json.dumps(messages, default=str))
"""
assert src.count(old) == 1, "loop anchor"
new = """        for _ in range(MAX_SANDBOX_ITERATIONS):
            messages, elided = self._trim_transcript(
                messages, system_message, ticket_turn, tools_chars, elided
            )
            sent_chars = len(json.dumps(messages, default=str))
"""
src = src.replace(old, new)

# 3. calibrate from the reported usage
old = """            total_completion += getattr(usage, "completion_tokens", 0) or 0
            self._check_context_held(
                prompt_tokens, last_prompt_tokens, sent_chars - last_sent_chars
            )
"""
assert src.count(old) == 1, "calibrate anchor"
new = """            total_completion += getattr(usage, "completion_tokens", 0) or 0
            self._calibrate(sent_chars + tools_chars, prompt_tokens)
            self._check_context_held(
                prompt_tokens, last_prompt_tokens, sent_chars - last_sent_chars
            )
"""
src = src.replace(old, new)

io.open(p, "w", encoding="utf-8").write(src)
print("loop wired, grew", len(src) - len(orig))
