// Behaviour: what the agents do, and what the office looks like when a ticket
// does not simply succeed.
//
// Layer 3 can move an agent to a named spot. This layer decides which spot, and
// shows what state each agent is in while it does. It is driven by the real
// ticket state machine in tickets.js, so the office animates the orchestrator's
// own states and triggers rather than a parallel invention.
//
// Two things are deliberate here.
//
// Sequences are polled, never chained on promises. A multi-step errand like
// "carry this to the backlog, put it down, walk back" has to advance inside a
// synchronous step loop so that a headless capture can wind the simulation
// forward to any moment; a promise chain would not run at all until the loop
// finished. So a plan is a small list of steps and update() walks it.
//
// The failure paths get as much attention as the happy one. A visualization
// that only ever showed a ticket being scoped and merged would misrepresent a
// system whose interesting behaviour is mostly what it does when something
// goes wrong — the retry budget, the escalation to the CTO, and the halt.

import * as THREE from 'three';
import { LAYOUT } from './layout.js';
import { PALETTE } from './palette.js';
import { ROSTER, placeAtStation } from './agents.js';
import { STATUS, ACTIVE_ROLE_FOR_STATUS, applyTrigger } from './tickets.js';
import { setAgentBadge, setAgentTimer, setAgentCarrying } from './characters.js';

const IDLE_AFTER = 13; // seconds doing nothing before an agent wanders off
const BREAK_MIN = 6;
const BREAK_MAX = 11;
const STUCK_SECONDS = 9; // how long a timed-out agent stands there before giving up

// Where an idle agent goes. The kitchen ones are the flavour the brief asked
// for; the printer and the sofa keep the engineering pod from always emptying
// in the same direction.
const BREAK_STATIONS = ['coffee', 'fridge', 'water_cooler', 'break_table', 'sofa', 'printer'];

// Where the listeners stand when the CTO is scoping at the board.
export const BOARD_PLACES = {
  'cto-1': 'board_present',
  'product-1': 'board_seat_2',
  'engineering-1': 'board_seat_1',
  'engineering-2': 'board_seat_3',
  'engineering-3': 'board_seat_4',
};

export const HOME = Object.fromEntries(ROSTER.map((r) => [r.id, r.station]));

export function createBehaviour({ agents, byId, loco, scene, rng = Math.random }) {
  const recs = new Map();
  for (const agent of agents) {
    recs.set(agent.id, {
      agent,
      activity: 'waiting',
      detail: null,
      idleFor: 0,
      engaged: false, // involved in the current ticket, so never wanders off
      plan: null,
      planIdx: 0,
      stepStarted: false,
      waitLeft: 0,
      timer: null,
    });
  }

  // Tickets that were halted end up here, physically, so a stalled run looks
  // different from a finished one rather than merely stopping.
  const dropped = new THREE.Group();
  dropped.name = 'droppedTickets';
  scene.add(dropped);

  let ticket = null;
  const listeners = [];

  // --- badges --------------------------------------------------------------

  const BADGE = {
    waiting: () => ['WAITING', PALETTE.status.idle],
    working: (r) => [r.detail ?? 'WORKING', PALETTE.status.active],
    testing: () => ['TESTS RUNNING', PALETTE.status.active],
    reviewing: () => ['REVIEWING', PALETTE.status.active],
    scoping: () => ['SCOPING', PALETTE.status.active],
    listening: () => ['LISTENING', PALETTE.status.active],
    onBreak: (r) => [r.detail ?? 'BREAK', PALETTE.status.rest],
    blocked: (r) => [r.detail ?? 'BLOCKED', PALETTE.status.waiting],
    escalating: () => ['ESCALATING', PALETTE.status.alert],
    escalated: () => ['ESCALATED', PALETTE.status.alert],
    parking: () => ['TO BACKLOG', PALETTE.status.halted],
    delivering: () => ['DELIVERING', PALETTE.status.active],
    halted: () => ['HALTED', PALETTE.status.halted],
    done: () => ['DONE', PALETTE.status.active],
  };

  function refreshBadge(rec) {
    const fn = BADGE[rec.activity] ?? BADGE.waiting;
    const [text, color] = fn(rec);
    if (rec.agent.badgeText !== text) setAgentBadge(rec.agent, text, color);
  }

  function setActivity(id, activity, detail = null) {
    const rec = recs.get(id);
    if (!rec) return;
    rec.activity = activity;
    rec.detail = detail;
    rec.idleFor = 0;
    refreshBadge(rec);
    emit({ type: 'activity', agent: id, activity, detail });
  }

  // --- plans ---------------------------------------------------------------

  function setPlan(rec, steps) {
    rec.plan = steps;
    rec.planIdx = 0;
    rec.stepStarted = false;
    rec.waitLeft = 0;
  }

  function beginStep(rec, step) {
    if (step.kind === 'goto') loco.goTo(rec.agent.id, step.station);
    else if (step.kind === 'wait') rec.waitLeft = step.seconds;
    else if (step.kind === 'do') step.fn();
  }

  function advancePlan(rec, dt) {
    if (!rec.plan) return;
    const step = rec.plan[rec.planIdx];

    if (!rec.stepStarted) {
      rec.stepStarted = true;
      beginStep(rec, step);
      return;
    }

    let done = false;
    if (step.kind === 'goto') done = !loco.isBusy(rec.agent.id);
    else if (step.kind === 'wait') done = (rec.waitLeft -= dt) <= 0;
    else done = true;

    if (!done) return;
    rec.planIdx += 1;
    rec.stepStarted = false;
    if (rec.planIdx >= rec.plan.length) rec.plan = null;
  }

  // --- the ticket ----------------------------------------------------------

  function agentForRole(role) {
    if (role === 'cto') return 'cto-1';
    if (role === 'product') return 'product-1';
    if (role === 'engineering') return ticket?.assignee ?? 'engineering-1';
    return null;
  }

  function clearEngagement() {
    for (const rec of recs.values()) rec.engaged = false;
  }

  function sendHome(id, activity = 'waiting', detail = null) {
    const rec = recs.get(id);
    if (!rec) return;
    setActivity(id, activity, detail);
    setPlan(rec, [{ kind: 'goto', station: HOME[id] }]);
  }

  /** Adopts a ticket and places the office in the state that ticket implies. */
  function setTicket(t) {
    ticket = t;
    enterStatus(t.status, null);
  }

  /**
   * Fires a real orchestrator trigger. The transition that actually happens may
   * not be the one asked for — exceeding the shared correction budget turns a
   * retry into an escalation — and the office shows the override case
   * differently, because an agent being overruled by the system is not the same
   * event as an agent choosing something.
   */
  function fire(trigger) {
    if (!ticket) {
      console.warn('behaviour: no ticket to fire triggers at');
      return null;
    }
    const result = applyTrigger(ticket, trigger);
    if (!result) return null;
    emit({ type: 'transition', ...result, ticket: ticket.id });
    enterStatus(result.to, result);
    return result;
  }

  function enterStatus(status, result) {
    clearEngagement();
    const role = ACTIVE_ROLE_FOR_STATUS[status];
    const actor = role ? agentForRole(role) : null;
    if (actor) {
      const rec = recs.get(actor);
      if (rec) rec.engaged = true;
    }

    switch (status) {
      case STATUS.INTAKE:
        sendHome(actor, 'scoping');
        break;

      case STATUS.BACKLOG:
        sendHome(actor, 'working', 'WRITING SPEC');
        break;

      case STATUS.SPECD:
        sendHome(actor, 'waiting');
        break;

      case STATUS.IN_PROGRESS: {
        // Arriving here from a failure is the retry loop. Showing the shared
        // budget on the badge is the whole point: the interesting thing about
        // a retry is how many are left before it escalates.
        const retryish = result && result.from !== STATUS.SPECD && result.from !== STATUS.ESCALATED;
        const detail = retryish ? `RETRY ${ticket.retryCount}/${ticket.retryCap}` : 'IN PROGRESS';
        const rec = recs.get(actor);
        setActivity(actor, 'working', detail);
        setAgentCarrying(rec.agent, false);
        setPlan(rec, [{ kind: 'goto', station: HOME[actor] }]);
        break;
      }

      case STATUS.AWAITING_TEST:
        // A deterministic system step, not an agent turn: the engineer waits at
        // their desk rather than appearing to do the work.
        setActivity(actor, 'testing');
        break;

      case STATUS.REVIEW: {
        const rec = recs.get(actor);
        setActivity(actor, 'reviewing');
        setPlan(rec, [{ kind: 'goto', station: HOME[actor] }]);
        break;
      }

      case STATUS.ESCALATED: {
        // The engineer carries the problem into the CTO's office and stands at
        // the visitor spot. Both are marked engaged, so neither wanders off to
        // make coffee in the middle of an escalation.
        const engineer = ticket?.assignee;
        setActivity('cto-1', 'escalated');
        recs.get('cto-1').engaged = true;
        const ctoRec = recs.get('cto-1');
        setPlan(ctoRec, [{ kind: 'goto', station: HOME['cto-1'] }]);

        if (engineer && recs.has(engineer)) {
          const rec = recs.get(engineer);
          rec.engaged = true;
          setActivity(engineer, 'escalating');
          setAgentCarrying(rec.agent, true);
          setPlan(rec, [{ kind: 'goto', station: 'cto_visitor' }]);
        }
        break;
      }

      case STATUS.HALTED: {
        // Whoever was holding it walks it to the backlog and puts it down. The
        // dropped ticket stays on the floor for the rest of the run.
        const holder = ticket?.assignee && recs.has(ticket.assignee) ? ticket.assignee : 'cto-1';
        const rec = recs.get(holder);
        rec.engaged = true;
        setActivity(holder, 'parking');
        setAgentCarrying(rec.agent, true);
        setPlan(rec, [
          { kind: 'goto', station: 'backlog' },
          { kind: 'wait', seconds: 1.0 },
          {
            kind: 'do',
            fn: () => {
              setAgentCarrying(rec.agent, false);
              dropTicketAtBacklog();
              setActivity(holder, 'halted');
            },
          },
          { kind: 'wait', seconds: 1.4 },
          { kind: 'goto', station: HOME[holder] },
          { kind: 'do', fn: () => setActivity(holder, 'waiting') },
        ]);
        break;
      }

      case STATUS.DONE:
        for (const rec of recs.values()) setAgentCarrying(rec.agent, false);
        for (const id of recs.keys()) sendHome(id, 'done');
        break;
    }

    // Anyone the new state does not involve goes back to their desk. Without
    // this, the four listeners stay standing at the drawing board wearing a
    // LISTENING badge long after the meeting that put them there has ended.
    // A break is left alone: someone already at the coffee machine when the
    // ticket moved on has not been summoned back by it.
    // An errand already under way is left to finish. Delivering a spec and
    // walking a halted ticket to the backlog both end by putting an object
    // down somewhere; interrupting one half way would leave the document in
    // the agent's hand with nothing ever to resolve it.
    const ON_AN_ERRAND = new Set(['delivering', 'parking']);

    if (status !== STATUS.DONE) {
      for (const [id, rec] of recs) {
        if (rec.engaged || rec.activity === 'onBreak' || rec.activity === 'waiting') continue;
        if (ON_AN_ERRAND.has(rec.activity)) continue;
        clearTimer(rec);
        sendHome(id, 'waiting');
      }
    }
  }

  function dropDocumentAt(x, y, z, where = 'floor') {
    const paper = new THREE.Mesh(
      new THREE.BoxGeometry(0.2, 0.014, 0.28),
      new THREE.MeshStandardMaterial({ color: PALETTE.ticketPaper, roughness: 0.9 })
    );
    paper.castShadow = true;
    paper.receiveShadow = true;
    paper.position.set(x, y, z);
    paper.rotation.y = (rng() - 0.5) * 0.7;
    dropped.add(paper);
    emit({ type: 'dropped', at: where, count: dropped.children.length });
    return paper;
  }

  // Halted tickets fan out along the cabinet rather than stacking in one spot,
  // so three halted tickets read as three.
  function dropTicketAtBacklog() {
    const station = LAYOUT.stations.backlog;
    const n = dropped.children.length;
    dropDocumentAt(
      station.at[0] - 0.75 + (n % 5) * 0.34,
      0.012,
      station.at[1] - 0.55,
      'backlog'
    );
  }

  /**
   * Carries a document to a drop-off spot, leaves it there, and goes back to
   * the agent's own desk. This is what the scoping scenario uses for Product
   * putting the spec on an engineer's desk on its way back.
   */
  function deliver(agentId, stationName) {
    const rec = recs.get(agentId);
    const station = LAYOUT.stations[stationName];
    if (!rec || !station) {
      console.warn(`behaviour: cannot deliver to "${stationName}"`);
      return;
    }
    rec.engaged = true;
    setActivity(agentId, 'delivering');
    setAgentCarrying(rec.agent, true);
    setPlan(rec, [
      { kind: 'goto', station: stationName },
      { kind: 'wait', seconds: 0.7 },
      {
        kind: 'do',
        fn: () => {
          setAgentCarrying(rec.agent, false);
          const p = station.dropAt ?? [station.at[0], 0.012, station.at[1]];
          dropDocumentAt(p[0], p[1], p[2], stationName);
        },
      },
      { kind: 'wait', seconds: 0.6 },
      { kind: 'goto', station: HOME[agentId] },
      {
        kind: 'do',
        fn: () => {
          rec.engaged = false;
          setActivity(agentId, 'waiting');
        },
      },
    ]);
  }

  /**
   * Puts the office back to how it started, instantly rather than by walking
   * everyone home — a restart should look like a cut, not like a scenario of
   * its own. Anything in flight is abandoned first, or an agent half way across
   * the office would carry on to a destination from the run just discarded.
   */
  function resetOffice(newTicket = null) {
    loco.cancelAll();

    for (const rec of recs.values()) {
      rec.plan = null;
      rec.planIdx = 0;
      rec.stepStarted = false;
      rec.waitLeft = 0;
      rec.engaged = false;
      rec.idleFor = 0;
      rec.timer = null;
      setAgentTimer(rec.agent, null);
      setAgentCarrying(rec.agent, false);
      placeAtStation(rec.agent, HOME[rec.agent.id]);
      rec.activity = 'waiting';
      rec.detail = null;
      refreshBadge(rec);
    }

    for (const child of [...dropped.children]) {
      dropped.remove(child);
      child.geometry.dispose();
      child.material.dispose();
    }

    if (newTicket) {
      ticket = newTicket;
      enterStatus(ticket.status, null);
    }
    emit({ type: 'reset' });
  }

  // --- the stuck timer -----------------------------------------------------

  /**
   * An agent that has stopped making progress: it stays where it is with a
   * timer filling over its head, and when that runs out the orchestrator's own
   * failure path takes over. This is what a sandbox timeout or a stalled
   * iteration budget looks like from across the office.
   */
  function block(id, seconds = STUCK_SECONDS, onExpire = 'tests_failed') {
    const rec = recs.get(id);
    if (!rec) return;
    rec.engaged = true;
    setActivity(id, 'blocked');
    rec.timer = { elapsed: 0, duration: seconds, onExpire };
  }

  function clearTimer(rec) {
    rec.timer = null;
    setAgentTimer(rec.agent, null);
  }

  // --- gathering -----------------------------------------------------------

  function summonToBoard() {
    for (const [id, station] of Object.entries(BOARD_PLACES)) {
      const rec = recs.get(id);
      if (!rec) continue;
      rec.engaged = true;
      clearTimer(rec);
      setActivity(id, id === 'cto-1' ? 'scoping' : 'listening');
      setPlan(rec, [{ kind: 'goto', station }]);
    }
  }

  function disperse() {
    clearEngagement();
    for (const id of recs.keys()) {
      clearTimer(recs.get(id));
      sendHome(id, 'waiting');
    }
  }

  // --- idle quirks ---------------------------------------------------------

  function maybeWander(rec, dt) {
    if (rec.engaged || rec.plan || loco.isBusy(rec.agent.id)) {
      rec.idleFor = 0;
      return;
    }
    if (rec.activity !== 'waiting') {
      rec.idleFor = 0;
      return;
    }

    rec.idleFor += dt;
    // A little jitter per agent, so five people do not all stand up for coffee
    // on the same frame.
    if (rec.idleFor < IDLE_AFTER + rec.wanderJitter) return;

    const station = BREAK_STATIONS[Math.floor(rng() * BREAK_STATIONS.length)];
    const linger = BREAK_MIN + rng() * (BREAK_MAX - BREAK_MIN);
    const name = station.replace(/_/g, ' ').toUpperCase();

    rec.idleFor = 0;
    setActivity(rec.agent.id, 'onBreak', name);
    setPlan(rec, [
      { kind: 'goto', station },
      { kind: 'wait', seconds: linger },
      { kind: 'goto', station: HOME[rec.agent.id] },
      { kind: 'do', fn: () => setActivity(rec.agent.id, 'waiting') },
    ]);
  }

  for (const rec of recs.values()) rec.wanderJitter = rng() * 7;

  // --- events --------------------------------------------------------------

  function emit(event) {
    for (const fn of listeners) fn(event);
  }

  function onEvent(fn) {
    listeners.push(fn);
  }

  // --- frame ---------------------------------------------------------------

  function update(dt) {
    if (dt <= 0) return;
    for (const rec of recs.values()) {
      if (rec.timer) {
        rec.timer.elapsed += dt;
        const t = rec.timer.elapsed / rec.timer.duration;
        setAgentTimer(rec.agent, t);
        if (t >= 1) {
          const trigger = rec.timer.onExpire;
          clearTimer(rec);
          emit({ type: 'timeout', agent: rec.agent.id, trigger });
          if (trigger) fire(trigger);
          continue;
        }
      }

      advancePlan(rec, dt);
      maybeWander(rec, dt);
    }
  }

  // Initial badges, so the office reads correctly before anything happens.
  for (const rec of recs.values()) refreshBadge(rec);

  return {
    update,
    setTicket,
    fire,
    block,
    deliver,
    resetOffice,
    summonToBoard,
    disperse,
    onEvent,
    get ticket() {
      return ticket;
    },
    droppedCount: () => dropped.children.length,
    stateOf: (id) => {
      const r = recs.get(id);
      return r ? { activity: r.activity, detail: r.detail, engaged: r.engaged } : null;
    },
  };
}
