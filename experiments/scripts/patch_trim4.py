import io
p = "src/wjfyp/orchestrator/openai_agent.py"
src = io.open(p, encoding="utf-8").read()
orig = src

# 1. trim takes a logging hook: give it context+turn_id so it can log
old = """    def _trim_transcript(
        self,
        messages: list[dict[str, Any]],
        system_message: dict[str, Any],
        ticket_turn: str,
        fixed_chars: int,
        elided: list[str],
    ) -> tuple[list[dict[str, Any]], list[str]]:"""
new = """    def _trim_transcript(
        self,
        messages: list[dict[str, Any]],
        system_message: dict[str, Any],
        ticket_turn: str,
        fixed_chars: int,
        elided: list[str],
        context: AgentContext | None = None,
        turn_id: UUID | None = None,
    ) -> tuple[list[dict[str, Any]], list[str]]:"""
assert src.count(old) == 1, "trim sig anchor"
src = src.replace(old, new)

# 2. log when it fires, on both exits
old = """        while len(groups) > _MIN_KEPT_GROUPS:
            digest.extend(_digest_group(groups.pop(0)))
            candidate = rebuild()
            if fits(candidate):
                return candidate, digest
"""
new = """        dropped = 0
        while len(groups) > _MIN_KEPT_GROUPS:
            digest.extend(_digest_group(groups.pop(0)))
            dropped += 1
            candidate = rebuild()
            if fits(candidate):
                self._log_trim(context, turn_id, dropped, len(groups), fitted=True)
                return candidate, digest
"""
assert src.count(old) == 1, "trim loop anchor"
src = src.replace(old, new)

old = """        # those terms.
        return rebuild(), digest
"""
new = """        # those terms.
        self._log_trim(context, turn_id, dropped, len(groups), fitted=False)
        return rebuild(), digest
"""
assert src.count(old) == 1, "trim tail anchor"
src = src.replace(old, new)

# 3. the logger itself, next to the other loggers
anchor = "    def _log_no_tool_call(\n"
assert src.count(anchor) == 1, "logger anchor"
logger = '''    def _log_trim(
        self,
        context: AgentContext | None,
        turn_id: UUID | None,
        dropped: int,
        kept: int,
        fitted: bool,
    ) -> None:
        """Records that the transcript was trimmed, and whether it then
        fitted.

        Logged for the same reason every other unusual path in this
        module is: an agent that behaves differently because it was shown
        less of its own history is a confound, and one invisible in the
        results is the kind that gets written up as a property of the
        model. A reader comparing two arms needs to know which turns ran
        on a full transcript and which did not.

        `fitted=False` is the louder case. It means the budget could not
        accommodate even the most recent exchange, so the server may yet
        truncate and the budget wants raising or the model changing.
        """
        if self._event_log is None or context is None:
            return
        verdict = (
            "now within budget"
            if fitted
            else f"STILL over budget at the {_MIN_KEPT_GROUPS}-exchange floor"
        )
        self._event_log.append_message(
            Message(
                sender=AgentRef(role=self.role, instance_id=self.instance_id),
                channel_id=context.channel_id,
                ticket_ref=context.ticket.id,
                type=MessageType.SYSTEM,
                parent_id=turn_id,
                content=MessageContent(
                    text=_truncate(
                        f"trimmed transcript for {self._role_config.model}: dropped "
                        f"{dropped} oldest exchange(s), kept {kept}, "
                        f"budget {CONTEXT_BUDGET_TOKENS:,} tokens at "
                        f"{self._chars_per_token:.2f} chars/token; {verdict}"
                    )
                ),
            )
        )

'''
src = src.replace(anchor, logger + anchor)

# 4. pass context/turn_id at the call site
old = """            messages, elided = self._trim_transcript(
                messages, system_message, ticket_turn, tools_chars, elided
            )"""
new = """            messages, elided = self._trim_transcript(
                messages, system_message, ticket_turn, tools_chars, elided, context, turn_id
            )"""
assert src.count(old) == 1, "call site anchor"
src = src.replace(old, new)

io.open(p, "w", encoding="utf-8").write(src)
print("logging added, grew", len(src) - len(orig))
