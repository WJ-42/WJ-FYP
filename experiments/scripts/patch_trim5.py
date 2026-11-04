import io
p = "src/wjfyp/orchestrator/openai_agent.py"
src = io.open(p, encoding="utf-8").read()

old = """        """ + '"""' + """Detects a slid context window from the usage numbers alone.

        Checking the reported prompt size against a ceiling would mean
        knowing the ceiling, and nothing in the OpenAI-compatible API
        reports it - /api/ps is Ollama-specific and vLLM has its own.
        So this uses the one invariant that holds on every backend: this
        loop only ever APPENDS to `messages`, so the prompt can only
        grow. If a few thousand characters go in and the reported prompt
        barely moves, the front went out as the back went in."""
new = """        """ + '"""' + """Detects a slid context window from the usage numbers alone.

        Checking the reported prompt size against a ceiling would mean
        knowing the ceiling, and nothing in the OpenAI-compatible API
        reports it - /api/ps is Ollama-specific and vLLM has its own.
        So this uses the invariant that the transcript grows across an
        untrimmed stretch of the loop: if a few thousand characters go
        in and the reported prompt barely moves, the front went out as
        the back went in.

        This is now the BACKSTOP rather than the mechanism, and it is
        kept because it is not sufficient on its own. It did not fire
        during the 04/11/2026 run described above CONTEXT_BUDGET_TOKENS,
        where a 28,076-token prompt was truncated to 16,384: Ollama
        reports the PRE-truncation count, so the number kept climbing
        with the transcript while the model attended to only the last
        16,384 tokens of it. Nothing in the usage numbers distinguished
        that from a healthy turn. _trim_transcript keeps the prompt
        under a declared budget instead, and this still catches the case
        where the declared budget is wrong - a trim that fires on an
        iteration shrinks the transcript, which makes `appended_chars`
        negative and takes the early return below, so the two mechanisms
        do not fight."""
assert src.count(old) == 1, "docstring anchor"
src = src.replace(old, new)
io.open(p, "w", encoding="utf-8").write(src)
print("docstring updated")
