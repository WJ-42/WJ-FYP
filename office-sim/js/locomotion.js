// Locomotion: getting an agent from where it is to a named station.
//
// Several things have to stay in step for a walk to read as walking, and they
// are all driven off distance travelled rather than wall-clock time, so that
// changing the speed cannot desynchronise them:
//
//   - the body advances along the taut path from nav.js
//   - it turns to face the way it is going, and pivots on the spot before
//     setting off rather than sliding sideways out of its chair
//   - the legs and arms swing through a cycle whose phase advances with
//     distance, so the feet do not skate
//
// Standing up and sitting down bracket the walk, because every home station in
// the floor plan is a seated one. That makes a trip a five-stage sequence, run
// as a small state machine per agent:
//
//   rising -> travel -> facing -> settling -> idle
//
// goTo() hands back a promise that resolves when the agent is finally settled,
// which is the hook Layer 5 needs to script a scenario without a timer.

import { LAYOUT } from './layout.js';
import { findPath } from './nav.js';
import { applyPose, applyPoseBlend, applyWalkPose, WALK_STRIDE } from './characters.js';
import { yawToFace } from './build.js';

const SPEED = 1.25; // m/s, a purposeful office walk
// Ground covered per half cycle. Derived from the rig's own leg length and hip
// swing rather than set here, so the stride cannot drift out of step with the
// pose that draws it and start skating.
const STRIDE = WALK_STRIDE;
const TURN_RATE = 3.4; // rad/s
const RISE_TIME = 0.42;
const SETTLE_TIME = 0.52;
const WAYPOINT_TOL = 0.05;
const PIVOT_GATE = 0.5; // rad of facing error above which the agent turns in place
const WALK_FADE = 7.0; // how fast the stride fades in and out, per second
const FACE_TOL = 0.025;
const PERSONAL_SPACE = 0.62; // metres between agent centres before they push apart

function shortestTurn(from, to) {
  let d = (to - from) % (Math.PI * 2);
  if (d > Math.PI) d -= Math.PI * 2;
  if (d < -Math.PI) d += Math.PI * 2;
  return d;
}

function yawToward(fromX, fromZ, toX, toZ) {
  const dx = toX - fromX;
  const dz = toZ - fromZ;
  const len = Math.hypot(dx, dz) || 1;
  return yawToFace([dx / len, dz / len]);
}

export function createLocomotion(agents, nav, opts = {}) {
  const pathOverlay = opts.pathOverlay ?? null;
  const state = new Map();

  for (const agent of agents) {
    state.set(agent.id, {
      agent,
      mode: 'idle',
      phase: 0,
      walk: 0,
      path: null,
      idx: 0,
      t: 0,
      station: agent.station ?? null,
      target: null,
      resolve: null,
    });
  }

  /**
   * Sends an agent to a named station. Any trip already in progress is
   * abandoned, which is what Layer 4 will want when a behaviour changes its
   * mind mid-corridor.
   *
   * Resolves true once seated or standing at the destination, false if the
   * station does not exist or no route reaches it.
   */
  function goTo(agentOrId, stationName) {
    const id = typeof agentOrId === 'string' ? agentOrId : agentOrId.id;
    const st = state.get(id);
    if (!st) {
      console.warn(`locomotion: unknown agent "${id}"`);
      return Promise.resolve(false);
    }

    const station = LAYOUT.stations[stationName];
    if (!station) {
      console.warn(`locomotion: unknown station "${stationName}"`);
      return Promise.resolve(false);
    }

    // Already there and settled. Without this an agent asked to go where it is
    // stands up, takes no steps and sits back down, which Layer 4 would show as
    // a twitch every time a behaviour re-asserted the station it already holds.
    if (st.mode === 'idle' && st.station === stationName) return Promise.resolve(true);

    // Abandon whatever was in flight before replacing it.
    finish(st, false);

    const pos = st.agent.root.position;
    const path = findPath(nav, [pos.x, pos.z], station.at);
    if (!path) {
      console.warn(`locomotion: no route from ${id} to "${stationName}"`);
      return Promise.resolve(false);
    }

    st.path = path;
    st.idx = 1; // path[0] is where the agent already is
    st.target = stationName;
    st.t = 0;
    st.mode = st.agent.pose === 'seated' ? 'rising' : 'travel';
    st.station = null;

    pathOverlay?.show(id, path, st.agent.spec.tierColor);

    return new Promise((resolve) => {
      st.resolve = resolve;
    });
  }

  function finish(st, ok) {
    const resolve = st.resolve;
    st.resolve = null;
    st.path = null;
    st.target = null;
    st.mode = 'idle';
    pathOverlay?.clear(st.agent.id);
    if (resolve) resolve(ok);
  }

  function arrive(st) {
    const stationName = st.target;
    const station = LAYOUT.stations[stationName];
    st.station = stationName;
    st.agent.station = stationName;
    applyPose(st.agent, station?.seated ? 'seated' : 'standing');
    finish(st, true);
  }

  function step(st, dt) {
    const agent = st.agent;
    const root = agent.root;

    switch (st.mode) {
      case 'idle':
        return;

      case 'rising': {
        st.t += dt;
        const k = Math.min(1, st.t / RISE_TIME);
        applyPoseBlend(agent, 'seated', 'standing', k);
        if (k >= 1) {
          st.mode = 'travel';
          st.t = 0;
        }
        return;
      }

      case 'travel': {
        if (!st.path || st.idx >= st.path.length) {
          st.mode = 'facing';
          return;
        }

        const [tx, tz] = st.path[st.idx];
        let dist = Math.hypot(tx - root.position.x, tz - root.position.z);

        // Turn toward the next waypoint, and only commit to moving once roughly
        // pointed at it. Without the gate an agent leaves a chair by sliding
        // sideways, which is the single most obvious tell that a walk is fake.
        const want = yawToward(root.position.x, root.position.z, tx, tz);
        const err = shortestTurn(root.rotation.y, want);
        const turn = Math.sign(err) * Math.min(Math.abs(err), TURN_RATE * dt);
        root.rotation.y += turn;
        const remaining = Math.abs(err) - Math.abs(turn);

        let moved = 0;
        if (remaining < PIVOT_GATE) {
          // Ease the pace down through a turn rather than cornering at speed.
          const pace = SPEED * (1 - 0.45 * (remaining / PIVOT_GATE));
          const stepLen = pace * dt;
          if (stepLen >= dist - WAYPOINT_TOL) {
            root.position.x = tx;
            root.position.z = tz;
            moved = dist;
            st.idx += 1;
          } else {
            const ux = (tx - root.position.x) / (dist || 1);
            const uz = (tz - root.position.z) / (dist || 1);
            root.position.x += ux * stepLen;
            root.position.z += uz * stepLen;
            moved = stepLen;
          }
        }

        st.phase += (moved / STRIDE) * Math.PI;
        const want_walk = moved > 1e-5 ? 1 : 0;
        st.walk += (want_walk - st.walk) * Math.min(1, WALK_FADE * dt);
        applyWalkPose(agent, st.phase, st.walk);

        if (st.idx >= st.path.length) {
          st.mode = 'facing';
          st.t = 0;
        }
        return;
      }

      case 'facing': {
        // Square up to whatever the station looks at, and let the stride fade
        // out over the same moment so the legs come to rest rather than stop.
        const station = LAYOUT.stations[st.target];
        const want = station
          ? yawToward(station.at[0], station.at[1], station.look[0], station.look[1])
          : root.rotation.y;
        const err = shortestTurn(root.rotation.y, want);
        const turn = Math.sign(err) * Math.min(Math.abs(err), TURN_RATE * dt);
        root.rotation.y += turn;

        st.walk += (0 - st.walk) * Math.min(1, WALK_FADE * dt);
        applyWalkPose(agent, st.phase, st.walk);

        if (Math.abs(err) - Math.abs(turn) < FACE_TOL && st.walk < 0.03) {
          root.rotation.y = want;
          if (station?.seated) {
            st.mode = 'settling';
            st.t = 0;
          } else {
            arrive(st);
          }
        }
        return;
      }

      case 'settling': {
        st.t += dt;
        const k = Math.min(1, st.t / SETTLE_TIME);
        applyPoseBlend(agent, 'standing', 'seated', k);
        if (k >= 1) arrive(st);
        return;
      }
    }
  }

  // Keeps two agents from occupying the same square metre.
  //
  // The nav grid routes around the building, which is static; it knows nothing
  // about the other four people walking through it, so two agents sent to the
  // same area merge into one figure. This is a nudge rather than real avoidance:
  // crowding still happens, bodies just no longer interpenetrate. Proper
  // steering belongs with the behaviour model in Layer 4.
  //
  // Only an agent that is actually travelling gets displaced, so nobody is
  // shoved off the chair they are sitting on, and never into a wall or a desk.
  function separate() {
    const list = [...state.values()];
    for (let i = 0; i < list.length; i++) {
      for (let j = i + 1; j < list.length; j++) {
        const a = list[i];
        const b = list[j];
        if (a.mode !== 'travel' && b.mode !== 'travel') continue;

        const pa = a.agent.root.position;
        const pb = b.agent.root.position;
        const dx = pb.x - pa.x;
        const dz = pb.z - pa.z;
        const d = Math.hypot(dx, dz);
        if (d >= PERSONAL_SPACE || d < 1e-4) continue;

        const push = (PERSONAL_SPACE - d) * 0.5;
        const ux = (dx / d) * push;
        const uz = (dz / d) * push;

        const movers = [a, b].filter((s) => s.mode === 'travel');
        // One of the pair standing still means the walker absorbs the whole
        // correction rather than half of it.
        const scale = movers.length === 1 ? 2 : 1;

        if (a.mode === 'travel') nudge(pa, -ux * scale, -uz * scale);
        if (b.mode === 'travel') nudge(pb, ux * scale, uz * scale);
      }
    }
  }

  function nudge(pos, dx, dz) {
    const x = pos.x + dx;
    const z = pos.z + dz;
    if (nav.isBlocked(x, z)) return; // never sidestep into a wall or a desk
    pos.x = x;
    pos.z = z;
  }

  function update(dt) {
    if (dt <= 0) return;
    for (const st of state.values()) step(st, dt);
    separate();
  }

  // Abandons a trip without finishing it. Restarting a scenario needs this:
  // an agent half way across the office has to stop where it is before being
  // put back at its desk, or it would carry on walking to a destination that
  // belongs to the run that was just thrown away.
  function cancel(agentOrId) {
    const id = typeof agentOrId === 'string' ? agentOrId : agentOrId.id;
    const st = state.get(id);
    if (st) finish(st, false);
  }

  function cancelAll() {
    for (const st of state.values()) finish(st, false);
  }

  return {
    goTo,
    cancel,
    cancelAll,
    update,
    isBusy: (id) => state.get(id)?.mode !== 'idle',
    anyBusy: () => [...state.values()].some((st) => st.mode !== 'idle'),
    stationOf: (id) => state.get(id)?.station ?? null,
    modeOf: (id) => state.get(id)?.mode ?? null,
  };
}
