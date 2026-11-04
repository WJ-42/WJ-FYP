import io
p = "src/wjfyp/orchestrator/openai_agent.py"
src = io.open(p, encoding="utf-8").read()
orig = src

# 1. calibration state in __init__
old = """        self._event_log = event_log
        self._spend_guard = spend_guard
"""
assert src.count(old) == 1, "init anchor"
new = """        self._event_log = event_log
        self._spend_guard = spend_guard
        # Replaced by the ratio the server reports, after the first
        # request of the first turn. Lives on the instance rather than
        # the turn so later turns start already calibrated.
        self._chars_per_token = _CHARS_PER_TOKEN_SEED
"""
src = src.replace(old, new)

# 2. the trim machinery, inserted before _check_context_held
anchor = "    def _check_context_held(\n"
assert src.count(anchor) == 1, "guard anchor"
methods = '''    def _calibrate(self, sent_chars: int, prompt_tokens: int) -> None:
        """Learns the server's characters-per-token from what it just
        reported, so the budget is enforced against this model's real
        tokenizer rather than a guess.

        An implausible ratio is discarded rather than stored. A ratio
        read as too high would make every transcript look small, every
        prompt look like it fits, and the trim never fire - which is the
        failure this whole mechanism exists to prevent, so a bad reading
        must not be able to cause it.

        A truncated prompt is itself a source of bad readings: the
        reported count stops tracking what was sent, so the ratio climbs.
        The upper bound catches that case too.
        """
        if prompt_tokens <= 0 or sent_chars <= 0:
            return
        low, high = _CHARS_PER_TOKEN_BOUNDS
        ratio = sent_chars / prompt_tokens
        if low <= ratio <= high:
            self._chars_per_token = ratio

    def _estimated_tokens(self, chars: int) -> int:
        return int(chars / self._chars_per_token)

    def _trim_transcript(
        self,
        messages: list[dict[str, Any]],
        system_message: dict[str, Any],
        ticket_turn: str,
        fixed_chars: int,
        elided: list[str],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Drops the oldest exchanges until the prompt fits the budget,
        replacing them with a digest of what they did.

        `fixed_chars` is the tool schemas, which are re-sent with every
        request and count toward the server's window just as the
        transcript does. Budgeting against the transcript alone would
        under-count by a fixed and substantial amount.

        The system prompt and the ticket turn are never candidates. The
        system prompt carries the role persona, which is this project's
        independent variable: a turn that has lost it is not a
        measurement of anything, which is the argument ContextWindowExceeded
        makes at more length. The ticket is what the turn is for.

        Returns the transcript to send and the accumulated digest, which
        the caller carries into the next iteration - a line, once
        written, is never dropped, so an attempt that failed early stays
        visible for the rest of the turn.
        """
        def fits(candidate: list[dict[str, Any]]) -> bool:
            chars = fixed_chars + len(json.dumps(candidate, default=str))
            return self._estimated_tokens(chars) <= CONTEXT_BUDGET_TOKENS

        if fits(messages):
            return messages, elided

        groups = _exchange_groups(messages[2:])
        digest = list(elided)

        def rebuild() -> list[dict[str, Any]]:
            user_content = ticket_turn + (_elision_note(digest) if digest else "")
            return [
                system_message,
                {"role": "user", "content": user_content},
                *[m for group in groups for m in group],
            ]

        while len(groups) > _MIN_KEPT_GROUPS:
            digest.extend(_digest_group(groups.pop(0)))
            candidate = rebuild()
            if fits(candidate):
                return candidate, digest

        # Down to the floor and still over. Returned anyway rather than
        # raised: the alternative is losing a run over one oversized
        # exchange, and _check_context_held is still behind this if the
        # server does truncate. A budget that cannot fit a single
        # exchange is a misconfigured budget, and the guard says so in
        # those terms.
        return rebuild(), digest

'''
src = src.replace(anchor, methods + anchor)

io.open(p, "w", encoding="utf-8").write(src)
print("methods inserted, grew", len(src) - len(orig))
