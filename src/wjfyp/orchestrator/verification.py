"""Structural verification of what an agent CLAIMS, before the FSM acts.

FYP-62. The principle, stated once here so the report can cite it rather
than reconstruct it from a conditional buried in an agent:

    An agent's narrative is evidence of what it believes it did. It is
    not evidence of what happened. Where a claim is load-bearing for the
    FSM, it is checked against the observable state of the world instead
    of being taken at its word.

This is not defensive coding against a hypothetical. Both backends have
been caught by it on real runs:

* A Haiku run during development called declare_outcome with trigger
  code_submission without ever calling git_commit, despite the system
  prompt explicitly requiring it. Prompt wording alone did not hold.
* A gpt-oss-20b run on 03/11/2026 declared code_submission with the
  narrative "Added header validation and test to reject malformed
  expiry values" having written no file and made no commit.

The second is the more interesting one, because the agent was not being
lazy. It had exhausted its iteration budget and produced a confident,
specific, entirely false account of work it had not done. No amount of
prompt engineering detects that from the inside.

Why code_submission specifically. Approval IS the merge in this system:
GitWorkspace.merge() is what the Review to Done transition runs, and
git_commit is the only thing that pushes an attempt's work back to the
host branch. So a code_submission with no commit would have the CTO
approve, and the merge would merge nothing, silently. The ticket would
read as Done with no change behind it.

Shared by claude_agent and openai_agent deliberately. The check is the
same invariant on both, and two copies of an invariant is one copy and
one future bug.
"""

from __future__ import annotations

from dataclasses import dataclass

# Triggers whose meaning depends on work actually having been committed.
# A set rather than a single string because the FSM may later grow
# another trigger that asserts the same thing, and the principle should
# extend by adding a name here rather than by writing a second check.
COMMIT_BACKED_TRIGGERS = frozenset({"code_submission"})

# Prefixed onto a narrative that was accepted despite failing
# verification. Only reachable where rejecting would risk never
# terminating (see accept_under_protest below). Deliberately visible in
# the message text rather than a quiet metadata flag, so it shows up in
# the dashboard feed, the event log and any transcript a human reads.
UNVERIFIED_PREFIX = "[submitted without committing]"


@dataclass(frozen=True)
class Verification:
    """The outcome of checking one declaration.

    `ok` False carries `rejection`, the plain-language correction to
    hand back to the model so it can fix the turn itself. That wording
    goes to an LLM rather than a log, which is why it says what to do
    next rather than merely what was wrong.
    """

    ok: bool
    rejection: str | None = None

    def __bool__(self) -> bool:
        return self.ok


def verify_declaration(trigger: str, *, has_committed: bool) -> Verification:
    """Checks a declared trigger against observed sandbox state.

    `has_committed` is whether a git_commit tool call actually succeeded
    this turn, tracked by the caller from real tool results rather than
    read out of the model's own account of itself. That distinction is
    the entire point of this module.
    """
    if trigger in COMMIT_BACKED_TRIGGERS and not has_committed:
        return Verification(
            ok=False,
            rejection="You haven't committed yet. Call git_commit, then declare_outcome again.",
        )
    return Verification(ok=True)


def accept_under_protest(narrative: str) -> str:
    """Flags a narrative that failed verification but is being accepted.

    There is one path where rejection is not available: the iteration
    budget has run out and the turn must end with a real FSM transition
    or the run cannot continue. A gate that can reject forever would
    never terminate, so termination wins and the failure is made loud
    instead of being enforced.

    Keeping that trade visible is the job of this function. An
    unverified submission still reaches the FSM, but nobody reading the
    feed, the log or the report can mistake it for a verified one.
    """
    if narrative.startswith(UNVERIFIED_PREFIX):
        return narrative
    return f"{UNVERIFIED_PREFIX} {narrative}"
