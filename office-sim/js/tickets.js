// The ticket state machine, ported from the orchestrator.
//
// This is a deliberate copy of `src/wjfyp/orchestrator/fsm.py` and the budget
// handling in `loop.py`, not an invention. The office is supposed to make the
// real system legible, so the states it animates are the real states and the
// events it reacts to are the real triggers. Layer 7 replaces the scripted
// driver with the live orchestrator stream, and because the vocabulary already
// matches there is no mapping layer in between to get wrong — the same reason
// Layer 2 used the orchestrator's real agent ids.
//
// The copy has to be kept in step by hand until then. Anything here that
// disagrees with fsm.py is a bug in here.

export const STATUS = {
  INTAKE: 'intake',
  BACKLOG: 'backlog',
  SPECD: 'specd',
  IN_PROGRESS: 'in_progress',
  AWAITING_TEST: 'awaiting_test',
  REVIEW: 'review',
  DONE: 'done',
  ESCALATED: 'escalated',
  HALTED: 'halted',
};

// Which role is expected to act next while a ticket sits in a given state.
// AWAITING_TEST is attribution only: the orchestrator runs the tests itself
// rather than invoking an engineering agent, which is why the office shows
// that engineer waiting at their desk rather than typing.
export const ACTIVE_ROLE_FOR_STATUS = {
  [STATUS.INTAKE]: 'cto',
  [STATUS.BACKLOG]: 'product',
  [STATUS.SPECD]: 'engineering',
  [STATUS.IN_PROGRESS]: 'engineering',
  [STATUS.AWAITING_TEST]: 'engineering',
  [STATUS.REVIEW]: 'cto',
  [STATUS.ESCALATED]: 'cto',
};

export const TRANSITIONS = [
  { from: STATUS.INTAKE, to: STATUS.BACKLOG, trigger: 'decomposed' },
  { from: STATUS.BACKLOG, to: STATUS.SPECD, trigger: 'spec_ready' },
  { from: STATUS.SPECD, to: STATUS.IN_PROGRESS, trigger: 'assigned' },
  { from: STATUS.IN_PROGRESS, to: STATUS.AWAITING_TEST, trigger: 'code_submission' },
  { from: STATUS.AWAITING_TEST, to: STATUS.REVIEW, trigger: 'tests_passed' },
  { from: STATUS.AWAITING_TEST, to: STATUS.IN_PROGRESS, trigger: 'tests_failed' },
  { from: STATUS.REVIEW, to: STATUS.DONE, trigger: 'approved', requiresApproval: true },
  { from: STATUS.REVIEW, to: STATUS.IN_PROGRESS, trigger: 'changes_requested', requiresApproval: true },
  { from: STATUS.AWAITING_TEST, to: STATUS.ESCALATED, trigger: 'retry_cap_exceeded' },
  { from: STATUS.REVIEW, to: STATUS.ESCALATED, trigger: 'retry_cap_exceeded' },
  { from: STATUS.ESCALATED, to: STATUS.IN_PROGRESS, trigger: 'cto_override', requiresApproval: true },
  { from: STATUS.ESCALATED, to: STATUS.HALTED, trigger: 'cto_cannot_resolve', requiresApproval: true },
  { from: STATUS.ESCALATED, to: STATUS.HALTED, trigger: 'escalation_cap_exceeded' },
  { from: STATUS.REVIEW, to: STATUS.IN_PROGRESS, trigger: 'empty_diff' },
];

const INDEX = new Map(TRANSITIONS.map((t) => [`${t.from}|${t.trigger}`, t]));

// The orchestrator-only overrides. An agent never chooses either of these for
// itself; the loop substitutes them once a budget runs out.
export const RETRY_OVERFLOW_TRIGGER = 'retry_cap_exceeded';
export const ESCALATION_OVERFLOW_TRIGGER = 'escalation_cap_exceeded';

// The three edges that share one correction budget. They share it because each
// is the same "engineering needs to redo this" event, and any one of them can
// loop forever on its own — which a real run proved when the review-rejection
// edge went round 34 times before it was given the same cap as the others.
export const RETRY_COUNTED_TRIGGERS = new Set([
  `${STATUS.AWAITING_TEST}|tests_failed`,
  `${STATUS.REVIEW}|changes_requested`,
  `${STATUS.REVIEW}|empty_diff`,
]);

export function createTicket({
  id = 'FYP-1',
  title = 'Untitled',
  assignee = null,
  retryCap = 3,
  escalationCap = 2,
} = {}) {
  return {
    id,
    title,
    assignee, // the engineering instance id, e.g. 'engineering-2'
    status: STATUS.INTAKE,
    retryCount: 0,
    retryCap,
    escalationCount: 0,
    escalationCap,
  };
}

/**
 * Applies a trigger, mirroring the orchestrator's budget overrides.
 *
 * Returns the transition that actually fired — which is not always the one
 * asked for. Exceeding the shared correction budget turns a retry into an
 * escalation, and exceeding the escalation budget turns an override into a
 * halt. `overrodeFrom` records the trigger that was replaced, because the
 * office shows those two cases differently from the ones an agent chose.
 *
 * Returns null if the trigger is not legal from the current state, rather than
 * guessing at what was meant.
 */
export function applyTrigger(ticket, trigger) {
  let effective = trigger;
  let overrodeFrom = null;

  const key = `${ticket.status}|${trigger}`;

  if (RETRY_COUNTED_TRIGGERS.has(key)) {
    ticket.retryCount += 1;
    if (ticket.retryCount > ticket.retryCap) {
      effective = RETRY_OVERFLOW_TRIGGER;
      overrodeFrom = trigger;
    }
  } else if (ticket.status === STATUS.ESCALATED && trigger === 'cto_override') {
    ticket.escalationCount += 1;
    if (ticket.escalationCount > ticket.escalationCap) {
      effective = ESCALATION_OVERFLOW_TRIGGER;
      overrodeFrom = trigger;
    } else {
      // An override grants a fresh correction budget, which is exactly why the
      // escalation count needs a cap of its own.
      ticket.retryCount = 0;
    }
  }

  const t = INDEX.get(`${ticket.status}|${effective}`);
  if (!t) {
    console.warn(`tickets: "${effective}" is not a legal trigger from "${ticket.status}"`);
    return null;
  }

  const from = ticket.status;
  ticket.status = t.to;
  return { from, to: t.to, trigger: effective, overrodeFrom, requiresApproval: !!t.requiresApproval };
}

export function triggersFrom(status) {
  return TRANSITIONS.filter((t) => t.from === status).map((t) => t.trigger);
}
