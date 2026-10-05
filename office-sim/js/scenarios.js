// The scenario library.
//
// Each scenario is a timeline of beats, and a beat is data rather than code: a
// time, an action from a fixed vocabulary, and a caption saying what is being
// shown. The player in player.js interprets them. Keeping them declarative
// means a scenario can be read end to end without following function calls, and
// means the caption and the action it describes cannot drift apart.
//
// This is a standing part of the interface, not scaffolding to be deleted once
// Layer 7 brings in live data. A real run is slow, costs money, and cannot be
// made to fail on demand; these can be played in front of someone in under a
// minute and show the cases that matter, including the ones a live run would
// only reach by accident.
//
// The failure paths use the orchestrator's real triggers, so what is being
// demonstrated is the actual state machine rather than an impression of it.
// `escalation` fails the tests four times against a cap of three: the fourth is
// substituted by the system for retry_cap_exceeded, which is the whole point of
// the shared correction budget.
//
// Nothing here is random. A scenario can stage movement as well as ticket
// events — `break` sends a named agent to a named spot for a set time — so what
// an audience sees is composed rather than whatever a dice roll produced. The
// idle behaviour that runs between staged beats is a rota rather than a random
// choice, so a scenario plays the same way every time it is run.

export const SCENARIOS = [
  {
    id: 'scoping',
    name: 'Scoping a ticket',
    summary: 'The clean path, end to end: scoped at the board, built, reviewed, merged.',
    beats: [
      {
        at: 0,
        do: 'summon',
        note: 'A requirement arrives. The CTO calls the team over to the drawing board.',
      },
      {
        at: 11,
        note: 'The CTO scopes it into a ticket. Product and Engineering gather round to listen.',
      },
      {
        at: 17,
        do: 'fire',
        args: ['decomposed'],
        note: 'Scoped. Everyone heads back to their own desk.',
      },
      {
        at: 18,
        do: 'deliver',
        args: ['product-1', 'eng2_dropoff'],
        note: 'Product writes the spec up and drops it on the desk of the engineer who will build it.',
      },
      {
        at: 44,
        do: 'fire',
        args: ['spec_ready'],
        note: 'The spec is ready and the ticket is handed to Engineering.',
      },
      { at: 48, do: 'fire', args: ['assigned'], note: 'Engineering 2 picks the ticket up and starts work.' },
      {
        at: 56,
        do: 'fire',
        args: ['code_submission'],
        note: 'Code submitted. The orchestrator runs the tests itself — nobody is acting here.',
      },
      { at: 61, do: 'fire', args: ['tests_passed'], note: 'Tests pass. The CTO reviews the work.' },
      { at: 68, do: 'fire', args: ['approved'], note: 'Approved, and approval is the merge. Done.' },
    ],
  },

  {
    id: 'retry',
    name: 'A failed test',
    summary: 'Tests fail once, the engineer reworks it, and the second attempt passes.',
    beats: [
      { at: 0, do: 'fire', args: ['decomposed'], note: 'A ticket already scoped, going straight into the build.' },
      { at: 1, do: 'fire', args: ['spec_ready'] },
      { at: 2, do: 'fire', args: ['assigned'], note: 'Engineering 2 starts work.' },
      { at: 9, do: 'fire', args: ['code_submission'], note: 'Code submitted, tests running.' },
      {
        at: 14,
        do: 'fire',
        args: ['tests_failed'],
        note: 'The tests fail. Back to the desk, with one of three attempts used.',
      },
      { at: 24, do: 'fire', args: ['code_submission'], note: 'Second attempt submitted.' },
      { at: 29, do: 'fire', args: ['tests_passed'], note: 'This time they pass, and it goes to review.' },
      { at: 36, do: 'fire', args: ['approved'], note: 'Approved and merged.' },
    ],
  },

  {
    id: 'timeout',
    name: 'A task that stalls',
    summary: 'A test run that never comes back, and the timer that eventually gives up on it.',
    beats: [
      { at: 0, do: 'fire', args: ['decomposed'] },
      { at: 1, do: 'fire', args: ['spec_ready'] },
      { at: 2, do: 'fire', args: ['assigned'], note: 'Engineering 2 starts work.' },
      { at: 9, do: 'fire', args: ['code_submission'], note: 'Code submitted, tests running.' },
      {
        at: 11,
        do: 'block',
        args: ['engineering-2', 10, 'tests_failed'],
        note: 'Nothing comes back. The timer over the engineer counts out the wait.',
      },
      {
        at: 22,
        note: 'The wait runs out and is treated as a failure, so the attempt is spent and the work goes back to the desk.',
      },
      { at: 30, do: 'fire', args: ['code_submission'], note: 'Tried again.' },
      { at: 35, do: 'fire', args: ['tests_passed'], note: 'Passes this time.' },
      { at: 42, do: 'fire', args: ['approved'], note: 'Approved and merged.' },
    ],
  },

  {
    id: 'escalation',
    name: 'Escalation to the CTO',
    summary: 'Four failures against a budget of three: the system escalates, then the CTO overrules it.',
    beats: [
      { at: 0, do: 'fire', args: ['decomposed'] },
      { at: 1, do: 'fire', args: ['spec_ready'] },
      { at: 2, do: 'fire', args: ['assigned'], note: 'Engineering 2 starts work.' },
      { at: 7, do: 'fire', args: ['code_submission'] },
      { at: 10, do: 'fire', args: ['tests_failed'], note: 'Failure one of three.' },
      { at: 15, do: 'fire', args: ['code_submission'] },
      { at: 18, do: 'fire', args: ['tests_failed'], note: 'Failure two.' },
      { at: 23, do: 'fire', args: ['code_submission'] },
      { at: 26, do: 'fire', args: ['tests_failed'], note: 'Failure three. The budget is now spent.' },
      { at: 31, do: 'fire', args: ['code_submission'] },
      {
        at: 34,
        do: 'fire',
        args: ['tests_failed'],
        note: 'A fourth failure. The orchestrator overrules it and escalates instead — the engineer carries the ticket to the CTO.',
      },
      {
        at: 45,
        do: 'fire',
        args: ['cto_override'],
        note: 'The CTO overrules the escalation and hands the work back with a fresh set of attempts. That reset is itself capped, at two overrides per ticket, or this edge could loop forever too.',
      },
      { at: 54, do: 'fire', args: ['code_submission'], note: 'The next attempt is submitted.' },
      { at: 59, do: 'fire', args: ['tests_passed'], note: 'This time the tests pass, and it goes to review.' },
      { at: 66, do: 'fire', args: ['approved'], note: 'Approved and merged. A ticket can come back from an escalation.' },
    ],
  },

  {
    id: 'deferred',
    name: 'A ticket deferred',
    summary: 'An escalation the CTO cannot resolve, so the ticket is parked in the backlog.',
    beats: [
      { at: 0, do: 'fire', args: ['decomposed'] },
      { at: 1, do: 'fire', args: ['spec_ready'] },
      { at: 2, do: 'fire', args: ['assigned'] },
      { at: 5, do: 'fire', args: ['code_submission'] },
      { at: 7, do: 'fire', args: ['tests_failed'], note: 'Failure one.' },
      { at: 10, do: 'fire', args: ['code_submission'] },
      { at: 12, do: 'fire', args: ['tests_failed'], note: 'Failure two.' },
      { at: 15, do: 'fire', args: ['code_submission'] },
      { at: 17, do: 'fire', args: ['tests_failed'], note: 'Failure three.' },
      { at: 20, do: 'fire', args: ['code_submission'] },
      { at: 22, do: 'fire', args: ['tests_failed'], note: 'Budget spent, so it escalates to the CTO.' },
      {
        at: 32,
        do: 'fire',
        args: ['cto_cannot_resolve'],
        note: 'The CTO cannot resolve it either. The ticket is walked out to the backlog and left there.',
      },
      { at: 52, note: 'A run that stalls leaves something behind, rather than simply stopping.' },
    ],
  },

  {
    id: 'idle',
    name: 'A quiet office',
    summary: 'Nothing assigned. Agents drift off for coffee and come back on their own.',
    beats: [
      // A fresh ticket starts at intake, which makes the CTO the active role
      // and would leave it scoping at its desk while the caption claims the
      // office is quiet. Standing everyone down first makes the two agree.
      {
        at: 0,
        do: 'disperse',
        note: 'No ticket in flight. Left alone, the office finds other things to do.',
      },
      { at: 3, do: 'break', args: ['engineering-1', 'coffee', 10] },
      {
        at: 7,
        do: 'break',
        args: ['product-1', 'water_cooler', 8],
        note: 'Product stops at the water cooler on the way past.',
      },
      {
        at: 12,
        do: 'break',
        args: ['engineering-3', 'sofa', 14],
        note: 'Engineering 3 takes the sofa.',
      },
      {
        at: 19,
        do: 'break',
        args: ['cto-1', 'fridge', 9],
        note: 'Even the CTO goes to the fridge.',
      },
      {
        at: 27,
        do: 'break',
        args: ['engineering-2', 'break_table', 12],
        note: 'Engineering 2 sits down at the kitchen table.',
      },
      { at: 45, note: 'Everyone drifts back to their own desk in their own time.' },
    ],
  },
];

export const SCENARIOS_BY_ID = new Map(SCENARIOS.map((s) => [s.id, s]));

// A scenario runs a little past its last beat so the final move finishes on
// screen rather than being cut off the instant the last trigger fires.
const TAIL_SECONDS = 12;

export function scenarioDuration(scenario) {
  const last = scenario.beats.reduce((m, b) => Math.max(m, b.at), 0);
  return last + TAIL_SECONDS;
}
